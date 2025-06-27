import os
import sys
import logging
import asyncio
import signal
from typing import Dict, Any, Optional
from io import BytesIO

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

from .config import Config
from .redis_client import RedisClient
from .utils import TTSGenerator

# Configure logging
def setup_logging(log_level: str):
    """Setup logging configuration"""
    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        level=getattr(logging, log_level.upper()),
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler('logs/bot.log') if os.path.exists('logs') else logging.NullHandler()
        ]
    )

logger = logging.getLogger(__name__)

class TelegramTTSBot:
    """Enhanced Telegram TTS Bot with Redis integration and fixed voice handling"""

    def __init__(self, config: Config):
        self.config = config

        # Initialize Redis client
        self.redis_client = None
        if config.redis_url:
            self.redis_client = RedisClient(
                redis_url=config.redis_url,
                key_prefix=config.redis_key_prefix
            )

        # Initialize TTS Generator
        self.tts_generator = TTSGenerator(
            api_key=config.elevenlabs_api_key,
            default_voice=config.default_voice,
            default_model=config.default_model
        )

        # Fallback in-memory storage
        self.user_settings: Dict[int, Dict[str, Any]] = {}

        # Application instance
        self.application = None
        self._running = True

    async def start_bot(self):
        """Initialize bot and Redis connection"""
        try:
            # Connect to Redis if available
            if self.redis_client:
                await self.redis_client.connect()
                logger.info("Redis connected successfully")
            else:
                logger.info("Running without Redis (in-memory storage)")

            # Test ElevenLabs API
            api_test = await self.tts_generator.test_api_connection()
            if api_test:
                logger.info("ElevenLabs API connection successful")
            else:
                logger.warning("ElevenLabs API connection failed - check your API key")

        except Exception as e:
            logger.warning(f"Redis connection failed, using in-memory storage: {e}")
            self.redis_client = None

    async def stop_bot(self):
        """Cleanup resources"""
        if self.redis_client:
            await self.redis_client.disconnect()

    async def get_user_settings(self, user_id: int) -> Dict[str, Any]:
        """Get user settings from Redis or memory"""
        if self.redis_client:
            settings = await self.redis_client.get_user_settings(user_id)
            if settings:
                return settings

        # Fallback to in-memory
        return self.user_settings.get(user_id, {})

    async def save_user_settings(self, user_id: int, settings: Dict[str, Any]):
        """Save user settings to Redis or memory"""
        if self.redis_client:
            await self.redis_client.set_user_settings(
                user_id, 
                settings, 
                self.config.redis_user_settings_ttl
            )
        else:
            # Fallback to in-memory
            self.user_settings[user_id] = settings

    async def check_rate_limit(self, user_id: int) -> bool:
        """Check rate limits using Redis or in-memory"""
        if self.redis_client:
            return await self.redis_client.check_rate_limit(
                user_id,
                self.config.rate_limit_calls,
                self.config.rate_limit_window
            )

        # Fallback rate limiting logic (simplified)
        return True  # For now, allow all requests when Redis unavailable

    async def increment_usage(self, metric: str, user_id: Optional[int] = None):
        """Track usage statistics"""
        if self.redis_client:
            await self.redis_client.increment_usage_counter(metric, user_id)

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        user_name = update.effective_user.first_name or "User"
        welcome_message = f"""
🎤 **Welcome to ElevenLabs TTS Bot, {user_name}!**

Transform any text into high-quality speech using AI voices.

**🚀 Quick Start:**
Just send me any text message and I'll convert it to speech!

**📋 Available Commands:**
/start - Show this welcome message
/voices - List all available AI voices
/setvoice [name] - Change your voice preference
/settings - View your current settings
/stats - View usage statistics
/help - Get detailed help

**💡 Tips:**
• Keep messages under {self.config.max_message_length} characters
• Supports 32+ languages
• Try different voices for variety!

**Example:** `/setvoice Rachel`
        """
        await update.message.reply_text(welcome_message, parse_mode='Markdown')
        await self.increment_usage("start_command", update.effective_user.id)
        logger.info(f"User {update.effective_user.id} ({user_name}) started the bot")

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command"""
        current_voice = await self.get_user_voice_name(update.effective_user.id)
        help_text = f"""
**📖 How to use this bot:**

**1. 🎵 Convert text to speech:**
Simply send any text message and get an audio file back.

**2. 🎭 Change voices:**
Use `/setvoice [voice_name]` to select different AI voices.
Example: `/setvoice Liam`

**3. 📋 Browse voices:**
Use `/voices` to see all available voice options.

**4. ⚙️ Check settings:**
Use `/settings` to view your current configuration.

**📏 Limitations:**
• Max message length: {self.config.max_message_length} characters
• Rate limit: {self.config.rate_limit_calls} requests per {self.config.rate_limit_window} seconds

**🎤 Current voice:** {current_voice}

**🌍 Supported languages:** 32+ including English, Spanish, French, German, Italian, Portuguese, Chinese, Japanese, Korean, and more!

**💡 Pro Tips:**
• Use punctuation for natural pauses
• ALL CAPS will sound emphasized
• Question marks create rising intonation
• Experiment with different voices for variety

Need more help? Contact support through the bot developer.
        """
        await update.message.reply_text(help_text, parse_mode='Markdown')
        await self.increment_usage("help_command", update.effective_user.id)

    async def settings_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /settings command"""
        user_id = update.effective_user.id
        user_settings = await self.get_user_settings(user_id)

        # Get rate limit status
        rate_status = {"calls": 0, "remaining_time": 0}
        if self.redis_client:
            rate_status = await self.redis_client.get_rate_limit_status(
                user_id, self.config.rate_limit_window
            )

        # Get voice info
        voice_name = await self.get_user_voice_name(user_id)
        voice_id = await self.get_user_voice_id(user_id)
        voice_category = user_settings.get('voice_category', 'generated')

        settings_text = f"""
⚙️ **Your Personal Settings:**

**🎤 Voice Configuration:**
• **Name:** {voice_name}
• **ID:** `{voice_id[:16]}...`
• **Category:** {voice_category.replace('_', ' ').title()}
• **Model:** {self.config.default_model}

**📊 Usage Limits:**
• **Max Message Length:** {self.config.max_message_length} characters
• **Rate Limit:** {self.config.rate_limit_calls} requests per {self.config.rate_limit_window} seconds
• **Current Usage:** {rate_status['calls']} requests in current window
• **Reset Time:** {rate_status['remaining_time']} seconds

**💾 Data Storage:**
• **Type:** {'Redis (Persistent across restarts)' if self.redis_client else 'In-Memory (Session only)'}
• **Settings Saved:** {'Yes - survives bot restarts' if self.redis_client else 'No - reset on restart'}

**🔧 Configuration:**
• **Environment:** {self.config.environment}
• **Log Level:** {self.config.log_level}

**💡 Want to change your voice?**
Use `/voices` to see options, then `/setvoice [name]`
        """
        await update.message.reply_text(settings_text, parse_mode='Markdown')
        await self.increment_usage("settings_command", user_id)

    async def stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /stats command"""
        if not self.redis_client:
            await update.message.reply_text(
                "📊 **Statistics Unavailable**\n\n"
                "Statistics tracking requires Redis database connection.\n"
                "Currently running in memory-only mode.\n\n"
                "Contact the bot administrator to enable statistics."
            )
            return

        try:
            stats = await self.redis_client.get_usage_stats()
            user_id = update.effective_user.id

            # Get user-specific stats if available
            user_stats = {}
            try:
                user_keys = await self.redis_client.redis.keys(f"{self.redis_client.key_prefix}:stats:user:{user_id}:*")
                for key in user_keys:
                    metric = key.split(":")[-1]
                    count = await self.redis_client.redis.get(key)
                    user_stats[metric] = int(count) if count else 0
            except Exception as e:
                logger.error(f"Error getting user stats: {e}")

            stats_text = "📊 **Usage Statistics:**\n\n"

            # Global stats
            if stats:
                stats_text += "**🌍 Global Stats:**\n"
                for metric, count in stats.items():
                    formatted_metric = metric.replace("_", " ").title()
                    stats_text += f"• **{formatted_metric}:** {count:,}\n"
                stats_text += "\n"

            # User stats
            if user_stats:
                stats_text += "**👤 Your Personal Stats:**\n"
                for metric, count in user_stats.items():
                    formatted_metric = metric.replace("_", " ").title()
                    stats_text += f"• **{formatted_metric}:** {count:,}\n"
            else:
                stats_text += "**👤 Your Personal Stats:**\nNo personal statistics available yet.\nStart using the bot to see your stats!"

            if not stats and not user_stats:
                stats_text += "No statistics available yet.\nStart using the bot to generate data!"

            await update.message.reply_text(stats_text, parse_mode='Markdown')

        except Exception as e:
            logger.error(f"Error getting stats: {e}")
            await update.message.reply_text("❌ Error retrieving statistics. Please try again later.")

    async def list_voices_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /voices command with proper API integration"""
        # Send initial message
        status_msg = await update.message.reply_text("🔄 Fetching available voices from ElevenLabs...")

        try:
            # Always fetch fresh data from API for accuracy
            available_voices = await self.tts_generator.get_voices()

            if not available_voices:
                await status_msg.edit_text("❌ Unable to fetch voices from ElevenLabs API. Please try again later.")
                return

            # Cache the results for future use
            if self.redis_client:
                voices_data = [voice.to_dict() for voice in available_voices]
                await self.redis_client.cache_voices(voices_data, ttl=1800)  # 30 minutes cache

            # Format voice list with categories
            voice_list = "🎭 **Available AI Voices:**\n\n"

            # Group by category
            categorized_voices = {}
            for voice in available_voices:
                category = voice.category or 'generated'
                if category not in categorized_voices:
                    categorized_voices[category] = []
                categorized_voices[category].append(voice)

            # Display categorized voices
            total_shown = 0
            for category, voices_in_category in categorized_voices.items():
                if total_shown >= 25:  # Limit total display to prevent message overflow
                    break

                category_emoji = "🤖" if category == "generated" else "👤" if category == "cloned" else "🎨" if category == "professional" else "🔊"
                category_title = category.replace('_', ' ').title()
                voice_list += f"{category_emoji} **{category_title} Voices:**\n"

                for voice in voices_in_category[:10]:  # Limit per category
                    if total_shown >= 25:
                        break
                    # Show name and partial ID for verification
                    voice_list += f"  • **{voice.name}** (`{voice.voice_id[:8]}...`)\n"
                    total_shown += 1

                voice_list += "\n"

            if len(available_voices) > total_shown:
                voice_list += f"... and **{len(available_voices) - total_shown} more voices** available\n\n"

            voice_list += f"**📝 Usage Examples:**\n"
            voice_list += f"• `/setvoice Rachel` - Set voice to Rachel\n"
            voice_list += f"• `/setvoice Liam` - Set voice to Liam\n\n"
            voice_list += f"**📊 Total Available:** {len(available_voices)} voices\n"
            voice_list += f"**🎯 Current Voice:** {await self.get_user_voice_name(update.effective_user.id)}"

            await status_msg.edit_text(voice_list, parse_mode='Markdown')
            await self.increment_usage("voices_command", update.effective_user.id)

        except Exception as e:
            logger.error(f"Error in list_voices_command: {e}")
            await status_msg.edit_text("❌ Error fetching voices. Please try again later.")

    async def set_voice_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /setvoice command with proper voice search"""
        if not context.args:
            await update.message.reply_text(
                "**🎤 Voice Selection Help**\n\n"
                "Please specify a voice name to change your voice.\n\n"
                "**Usage:** `/setvoice [voice_name]`\n"
                "**Examples:**\n"
                "• `/setvoice Rachel`\n"
                "• `/setvoice Liam`\n"
                "• `/setvoice Sarah`\n\n"
                "Use /voices to see all available voice options.", 
                parse_mode='Markdown'
            )
            return

        voice_name = " ".join(context.args)
        user_id = update.effective_user.id

        # Send searching message
        status_msg = await update.message.reply_text(f"🔍 Searching for voice: **{voice_name}**...", parse_mode='Markdown')

        try:
            # Use the dedicated method to find voice by name
            selected_voice = await self.tts_generator.get_voice_by_name(voice_name)

            if selected_voice:
                # Save voice settings
                await self.save_user_settings(user_id, {
                    'voice_id': selected_voice.voice_id,
                    'voice_name': selected_voice.name,
                    'voice_category': selected_voice.category
                })

                # Create info string
                category_info = f" ({selected_voice.category.replace('_', ' ').title()})" if selected_voice.category else ""

                success_msg = f"""
✅ **Voice Successfully Changed!**

**🎤 New Voice:** {selected_voice.name}{category_info}
**🆔 Voice ID:** `{selected_voice.voice_id}`
**📂 Category:** {selected_voice.category.replace('_', ' ').title() if selected_voice.category else 'Unknown'}

**💡 Test it out:** Send me any text message to hear your new voice!
                """

                await status_msg.edit_text(success_msg, parse_mode='Markdown')
                await self.increment_usage("voice_change", user_id)
                logger.info(f"User {user_id} changed voice to {selected_voice.name} (ID: {selected_voice.voice_id})")
            else:
                # Get all voices for suggestions
                all_voices = await self.tts_generator.get_voices()
                similar_voices = []
                voice_name_lower = voice_name.lower()

                # Find partial matches
                for voice in all_voices:
                    if voice_name_lower in voice.name.lower():
                        similar_voices.append(voice.name)

                error_msg = f"❌ **Voice '{voice_name}' not found.**\n\n"

                if similar_voices:
                    error_msg += "**🤔 Did you mean one of these?**\n"
                    for similar_voice in similar_voices[:5]:  # Show top 5 suggestions
                        error_msg += f"• `/setvoice {similar_voice}`\n"
                    error_msg += f"\n**💡 Tip:** Voice names are case-sensitive!"
                else:
                    error_msg += f"**📋 Available Options:**\n"
                    error_msg += f"Use `/voices` to see all {len(all_voices)} available voices.\n\n"
                    error_msg += f"**💡 Tips:**\n"
                    error_msg += f"• Check spelling carefully\n"
                    error_msg += f"• Voice names are case-sensitive\n"
                    error_msg += f"• Try browsing `/voices` first"

                await status_msg.edit_text(error_msg, parse_mode='Markdown')

        except Exception as e:
            logger.error(f"Error in set_voice_command: {e}")
            await status_msg.edit_text("❌ Error setting voice. Please try again later.")

    async def get_user_voice_name(self, user_id: int) -> str:
        """Get user's selected voice name or default"""
        settings = await self.get_user_settings(user_id)
        return settings.get('voice_name', 'George (Default)')

    async def get_user_voice_id(self, user_id: int) -> str:
        """Get user's selected voice ID or default"""
        settings = await self.get_user_settings(user_id)
        return settings.get('voice_id', self.config.default_voice)

    async def handle_text_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle text messages and convert to speech"""
        text = update.message.text
        user_id = update.effective_user.id
        user_name = update.effective_user.first_name or "User"

        # Check rate limits
        if not await self.check_rate_limit(user_id):
            rate_status = {"remaining_time": 60}
            if self.redis_client:
                rate_status = await self.redis_client.get_rate_limit_status(
                    user_id, self.config.rate_limit_window
                )

            await update.message.reply_text(
                f"⏰ **Rate Limit Reached!**\n\n"
                f"You've exceeded the limit of {self.config.rate_limit_calls} requests per {self.config.rate_limit_window} seconds.\n\n"
                f"⏳ **Please wait:** {rate_status['remaining_time']} seconds\n"
                f"🔄 **Then try again:** Send your message after the cooldown"
            )
            return

        # Validate message length
        if len(text) > self.config.max_message_length:
            await update.message.reply_text(
                f"❌ **Message Too Long!**\n\n"
                f"📏 **Your message:** {len(text)} characters\n"
                f"📐 **Maximum allowed:** {self.config.max_message_length} characters\n"
                f"✂️ **Exceeded by:** {len(text) - self.config.max_message_length} characters\n\n"
                f"💡 **Tip:** Break your text into smaller parts and send multiple messages."
            )
            return

        # Check for empty or very short messages
        if len(text.strip()) < 2:
            await update.message.reply_text(
                "❌ **Message Too Short!**\n\n"
                "Please send at least 2 characters of text for speech generation.\n\n"
                "💡 **Example:** Try sending 'Hello world!'"
            )
            return

        # Send "generating" message with progress
        status_msg = await update.message.reply_text(
            f"🔄 **Generating audio...**\n\n"
            f"👤 **User:** {user_name}\n"
            f"🎤 **Voice:** {await self.get_user_voice_name(user_id)}\n"
            f"📝 **Text:** {text[:50]}{'...' if len(text) > 50 else ''}\n"
            f"📊 **Length:** {len(text)} characters",
            parse_mode='Markdown'
        )

        try:
            voice_id = await self.get_user_voice_id(user_id)
            voice_name = await self.get_user_voice_name(user_id)

            # Generate audio
            audio_buffer = await self.tts_generator.generate_audio(text, voice_id)

            # Prepare caption with text preview
            text_preview = text[:100] + "..." if len(text) > 100 else text
            caption = f"""
🎤 **Voice:** {voice_name}
👤 **User:** {user_name}
📝 **Text:** {text_preview}
📊 **Stats:** {len(text)} chars, {len(text.split())} words
            """.strip()

            # Send audio file with caption
            await update.message.reply_voice(
                voice=audio_buffer,
                caption=caption,
                parse_mode='Markdown'
            )

            # Clean up status message
            await status_msg.delete()

            # Track usage
            await self.increment_usage("tts_generation", user_id)
            await self.increment_usage("characters_processed")

            logger.info(f"Generated audio for user {user_id} ({user_name}), voice: {voice_name}, text length: {len(text)}")

        except Exception as e:
            logger.error(f"Error generating audio for user {user_id}: {e}")

            # Update status message with specific error
            error_msg = str(e)
            if "Authentication failed" in error_msg:
                await status_msg.edit_text(
                    "❌ **API Authentication Failed**\n\n"
                    "The ElevenLabs API key is invalid or expired.\n"
                    "Please contact the bot administrator."
                )
            elif "Quota exceeded" in error_msg:
                await status_msg.edit_text(
                    "❌ **Service Quota Exceeded**\n\n"
                    "The ElevenLabs subscription limit has been reached.\n"
                    "Please try again later or contact the administrator."
                )
            elif "Rate limit exceeded" in error_msg:
                await status_msg.edit_text(
                    "❌ **API Rate Limit Exceeded**\n\n"
                    "Too many requests to ElevenLabs API.\n"
                    "Please wait a moment and try again."
                )
            else:
                await status_msg.edit_text(
                    f"❌ **Audio Generation Failed**\n\n"
                    f"An error occurred while generating your audio.\n"
                    f"Please try again in a moment.\n\n"
                    f"**Error:** {error_msg[:100]}{'...' if len(error_msg) > 100 else ''}"
                )

    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle errors"""
        logger.error(f"Update {update} caused error {context.error}")
        await self.increment_usage("errors")

        # Try to inform user about the error
        try:
            if update and update.effective_message:
                await update.effective_message.reply_text(
                    "❌ **Unexpected Error**\n\n"
                    "An unexpected error occurred while processing your request.\n"
                    "Please try again later.\n\n"
                    "If the problem persists, contact the bot administrator."
                )
        except Exception as e:
            logger.error(f"Failed to send error message to user: {e}")

    def setup_handlers(self):
        """Setup bot handlers"""
        if not self.application:
            return

        # Command handlers
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(CommandHandler("settings", self.settings_command))
        self.application.add_handler(CommandHandler("stats", self.stats_command))
        self.application.add_handler(CommandHandler("voices", self.list_voices_command))
        self.application.add_handler(CommandHandler("setvoice", self.set_voice_command))

        # Message handler for text-to-speech
        self.application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text_message)
        )

        # Error handler
        self.application.add_error_handler(self.error_handler)

    async def start(self):
        """Start the bot"""
        try:
            await self.start_bot()

            # Create Telegram application
            self.application = Application.builder().token(self.config.telegram_bot_token).build()
            self.setup_handlers()

            logger.info("Starting Telegram TTS Bot with Redis integration...")
            await self.application.initialize()
            await self.application.start()
            await self.application.updater.start_polling(
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=True
            )

            logger.info("Bot is running and ready to receive messages...")
            logger.info(f"Environment: {self.config.environment}")
            logger.info(f"Redis: {'Enabled' if self.redis_client else 'Disabled'}")
            logger.info(f"Rate limiting: {self.config.rate_limit_calls} calls per {self.config.rate_limit_window}s")

            # Keep the bot running
            while self._running:
                await asyncio.sleep(1)

        except Exception as e:
            logger.error(f"Error starting bot: {e}")
            raise

    async def stop(self):
        """Stop the bot gracefully"""
        logger.info("Stopping bot...")
        self._running = False

        if self.application:
            await self.application.updater.stop()
            await self.application.stop()
            await self.application.shutdown()

        await self.stop_bot()
        logger.info("Bot stopped successfully")

def signal_handler(bot: TelegramTTSBot):
    """Handle shutdown signals"""
    def handler(signum, frame):
        logger.info(f"Received signal {signum} - initiating graceful shutdown")
        asyncio.create_task(bot.stop())
    return handler

async def main():
    """Main entry point"""
    try:
        # Load configuration
        config = Config.from_env()
        setup_logging(config.log_level)

        logger.info("=" * 50)
        logger.info("Starting ElevenLabs Telegram TTS Bot")
        logger.info("=" * 50)
        logger.info(f"Environment: {config.environment}")
        logger.info(f"Log Level: {config.log_level}")
        logger.info(f"Redis URL: {'Configured' if config.redis_url else 'Not configured'}")
        logger.info(f"Max Message Length: {config.max_message_length}")
        logger.info(f"Rate Limit: {config.rate_limit_calls} calls per {config.rate_limit_window}s")
        logger.info("=" * 50)

        # Create and start bot
        bot = TelegramTTSBot(config)

        # Setup signal handlers for graceful shutdown
        for sig in [signal.SIGTERM, signal.SIGINT]:
            signal.signal(sig, signal_handler(bot))

        await bot.start()

    except KeyboardInterrupt:
        logger.info("Bot stopped by user (Ctrl+C)")
    except Exception as e:
        logger.error(f"Fatal error during startup: {e}")
        sys.exit(1)
    finally:
        logger.info("Bot shutdown complete")

if __name__ == "__main__":
    # Create logs directory if it doesn't exist
    os.makedirs("logs", exist_ok=True)

    # Run the bot
    asyncio.run(main())
