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
    """Enhanced Telegram TTS Bot with Redis integration and HTML formatting"""

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
🎤 <b>Welcome to ElevenLabs TTS Bot, {user_name}!</b>

Transform any text into high-quality speech using AI voices.

<b>🚀 Quick Start:</b>
Just send me any text message and I'll convert it to speech!

<b>📋 Available Commands:</b>
/start - Show this welcome message
/voices - List all available AI voices
/setvoice [name] - Change your voice preference
/settings - View your current settings
/stats - View usage statistics
/help - Get detailed help

<b>💡 Tips:</b>
• Keep messages under {self.config.max_message_length} characters
• Supports 32+ languages
• Try different voices for variety!

<b>Example:</b> <code>/setvoice Rachel</code>
        """
        await update.message.reply_text(welcome_message, parse_mode='HTML')
        await self.increment_usage("start_command", update.effective_user.id)
        logger.info(f"User {update.effective_user.id} ({user_name}) started the bot")

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command"""
        current_voice = await self.get_user_voice_name(update.effective_user.id)
        help_text = f"""
<b>📖 How to use this bot:</b>

<b>1. 🎵 Convert text to speech:</b>
Simply send any text message and get an audio file back.

<b>2. 🎭 Change voices:</b>
Use <code>/setvoice [voice_name]</code> to select different AI voices.
Example: <code>/setvoice Liam</code>

<b>3. 📋 Browse voices:</b>
Use <code>/voices</code> to see all available voice options.

<b>4. ⚙️ Check settings:</b>
Use <code>/settings</code> to view your current configuration.

<b>📏 Limitations:</b>
• Max message length: {self.config.max_message_length} characters
• Rate limit: {self.config.rate_limit_calls} requests per {self.config.rate_limit_window} seconds

<b>🎤 Current voice:</b> {current_voice}

<b>🌍 Supported languages:</b> 32+ including English, Spanish, French, German, Italian, Portuguese, Chinese, Japanese, Korean, and more!

<b>💡 Pro Tips:</b>
• Use punctuation for natural pauses
• ALL CAPS will sound emphasized
• Question marks create rising intonation
• Experiment with different voices for variety

Need more help? Contact support through the bot developer.
        """
        await update.message.reply_text(help_text, parse_mode='HTML')
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
⚙️ <b>Your Personal Settings:</b>

<b>🎤 Voice Configuration:</b>
• <b>Name:</b> {voice_name}
• <b>ID:</b> <code>{voice_id}</code>
• <b>Category:</b> {voice_category.replace('_', ' ').title()}
• <b>Model:</b> {self.config.default_model}

<b>📊 Usage Limits:</b>
• <b>Max Message Length:</b> {self.config.max_message_length} characters
• <b>Rate Limit:</b> {self.config.rate_limit_calls} requests per {self.config.rate_limit_window} seconds
• <b>Current Usage:</b> {rate_status['calls']} requests in current window
• <b>Reset Time:</b> {rate_status['remaining_time']} seconds

<b>💾 Data Storage:</b>
• <b>Type:</b> {'Redis (Persistent across restarts)' if self.redis_client else 'In-Memory (Session only)'}
• <b>Settings Saved:</b> {'Yes - survives bot restarts' if self.redis_client else 'No - reset on restart'}

<b>🔧 Configuration:</b>
• <b>Environment:</b> {self.config.environment}
• <b>Log Level:</b> {self.config.log_level}

<b>💡 Want to change your voice?</b>
Use <code>/voices</code> to see options, then <code>/setvoice [name]</code>
        """
        await update.message.reply_text(settings_text, parse_mode='HTML')
        await self.increment_usage("settings_command", user_id)

    async def stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /stats command"""
        if not self.redis_client:
            await update.message.reply_text(
                "📊 <b>Statistics Unavailable</b>\n\n"
                "Statistics tracking requires Redis database connection.\n"
                "Currently running in memory-only mode.\n\n"
                "Contact the bot administrator to enable statistics.",
                parse_mode='HTML'
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

            stats_text = "📊 <b>Usage Statistics:</b>\n\n"

            # Global stats
            if stats:
                stats_text += "<b>🌍 Global Stats:</b>\n"
                for metric, count in stats.items():
                    formatted_metric = metric.replace("_", " ").title()
                    stats_text += f"• <b>{formatted_metric}:</b> {count:,}\n"
                stats_text += "\n"

            # User stats
            if user_stats:
                stats_text += "<b>👤 Your Personal Stats:</b>\n"
                for metric, count in user_stats.items():
                    formatted_metric = metric.replace("_", " ").title()
                    stats_text += f"• <b>{formatted_metric}:</b> {count:,}\n"
            else:
                stats_text += "<b>👤 Your Personal Stats:</b>\nNo personal statistics available yet.\nStart using the bot to see your stats!"

            if not stats and not user_stats:
                stats_text += "No statistics available yet.\nStart using the bot to generate data!"

            await update.message.reply_text(stats_text, parse_mode='HTML')

        except Exception as e:
            logger.error(f"Error getting stats: {e}")
            await update.message.reply_text("❌ Error retrieving statistics. Please try again later.")

    async def list_voices_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /voices command with complete voice listing and easy copy format"""
        # Send initial message
        status_msg = await update.message.reply_text("🔄 Fetching all available voices from ElevenLabs...")

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

            # Group by category
            categorized_voices = {}
            for voice in available_voices:
                category = voice.category or 'generated'
                if category not in categorized_voices:
                    categorized_voices[category] = []
                categorized_voices[category].append(voice)

            # Format complete voice list with HTML and easy copy names
            voice_list = "🎭 <b>Complete AI Voices List:</b>\n\n"

            # Display all voices by category with copy-friendly format
            total_voices = 0
            for category, voices_in_category in categorized_voices.items():
                category_emoji = {
                    "generated": "🤖",
                    "cloned": "👤", 
                    "professional": "🎨",
                    "premade": "🔊"
                }.get(category, "🎙️")

                category_title = category.replace('_', ' ').title()
                voice_list += f"{category_emoji} <b>{category_title} Voices:</b>\n"

                # Show all voices in this category
                for voice in voices_in_category:
                    # Make voice name easy to copy with code formatting
                    voice_list += f"  • <code>{voice.name}</code>\n"
                    total_voices += 1

                voice_list += "\n"

            # Add usage instructions
            voice_list += f"<b>📝 How to use:</b>\n"
            voice_list += f"1. Copy any voice name above (tap to copy)\n"
            voice_list += f"2. Use: <code>/setvoice [voice_name]</code>\n\n"

            voice_list += f"<b>💡 Examples:</b>\n"
            # Show examples with actual available voice names
            example_voices = [voice.name for voice in available_voices[:3]]
            for example_voice in example_voices:
                voice_list += f"• <code>/setvoice {example_voice}</code>\n"

            voice_list += f"\n<b>📊 Total Available:</b> {total_voices} voices\n"
            voice_list += f"<b>🎯 Your Current Voice:</b> {await self.get_user_voice_name(update.effective_user.id)}\n\n"
            voice_list += f"<b>💡 Pro Tip:</b> Tap any voice name above to copy it, then use /setvoice!"

            await status_msg.edit_text(voice_list, parse_mode='HTML')
            await self.increment_usage("voices_command", update.effective_user.id)

        except Exception as e:
            logger.error(f"Error in list_voices_command: {e}")
            await status_msg.edit_text("❌ Error fetching voices. Please try again later.")

    async def set_voice_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /setvoice command with proper voice search"""
        if not context.args:
            await update.message.reply_text(
                "<b>🎤 Voice Selection Help</b>\n\n"
                "Please specify a voice name to change your voice.\n\n"
                "<b>Usage:</b> <code>/setvoice [voice_name]</code>\n"
                "<b>Examples:</b>\n"
                "• <code>/setvoice Rachel</code>\n"
                "• <code>/setvoice Liam</code>\n"
                "• <code>/setvoice Sarah</code>\n\n"
                "Use /voices to see all available voice options with easy copy format.", 
                parse_mode='HTML'
            )
            return

        voice_name = " ".join(context.args)
        user_id = update.effective_user.id

        # Send searching message
        status_msg = await update.message.reply_text(f"🔍 Searching for voice: <b>{voice_name}</b>...", parse_mode='HTML')

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
✅ <b>Voice Successfully Changed!</b>

<b>🎤 New Voice:</b> {selected_voice.name}{category_info}
<b>🆔 Voice ID:</b> <code>{selected_voice.voice_id}</code>
<b>📂 Category:</b> {selected_voice.category.replace('_', ' ').title() if selected_voice.category else 'Unknown'}

<b>💡 Test it out:</b> Send me any text message to hear your new voice!

<b>🔄 Change again:</b> Use <code>/voices</code> to browse all options
                """

                await status_msg.edit_text(success_msg, parse_mode='HTML')
                await self.increment_usage("voice_change", user_id)
                logger.info(f"User {user_id} changed voice to {selected_voice.name} (ID: {selected_voice.voice_id})")
            else:
                # Get all voices for suggestions
                all_voices = await self.tts_generator.get_voices()
                similar_voices = []
                exact_matches = []
                partial_matches = []
                voice_name_lower = voice_name.lower()

                # Find exact and partial matches
                for voice in all_voices:
                    voice_name_api_lower = voice.name.lower()
                    if voice_name_api_lower == voice_name_lower:
                        exact_matches.append(voice.name)
                    elif voice_name_lower in voice_name_api_lower or voice_name_api_lower in voice_name_lower:
                        partial_matches.append(voice.name)

                similar_voices = exact_matches + partial_matches

                error_msg = f"❌ <b>Voice '{voice_name}' not found.</b>\n\n"

                if similar_voices:
                    error_msg += "<b>🤔 Did you mean one of these?</b>\n"
                    for similar_voice in similar_voices[:8]:  # Show more suggestions
                        error_msg += f"• <code>/setvoice {similar_voice}</code>\n"
                    error_msg += f"\n<b>💡 Tip:</b> Tap any command above to copy it!"
                else:
                    error_msg += f"<b>📋 Available Options:</b>\n"
                    error_msg += f"Use <code>/voices</code> to see all {len(all_voices)} available voices with easy copy format.\n\n"
                    error_msg += f"<b>💡 Tips:</b>\n"
                    error_msg += f"• Voice names are case-sensitive\n"
                    error_msg += f"• Try browsing /voices first\n"
                    error_msg += f"• Popular voices: Rachel, Liam, Sarah, George"

                await status_msg.edit_text(error_msg, parse_mode='HTML')

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
                f"⏰ <b>Rate Limit Reached!</b>\n\n"
                f"You've exceeded the limit of {self.config.rate_limit_calls} requests per {self.config.rate_limit_window} seconds.\n\n"
                f"⏳ <b>Please wait:</b> {rate_status['remaining_time']} seconds\n"
                f"🔄 <b>Then try again:</b> Send your message after the cooldown",
                parse_mode='HTML'
            )
            return

        # Validate message length
        if len(text) > self.config.max_message_length:
            await update.message.reply_text(
                f"❌ <b>Message Too Long!</b>\n\n"
                f"📏 <b>Your message:</b> {len(text)} characters\n"
                f"📐 <b>Maximum allowed:</b> {self.config.max_message_length} characters\n"
                f"✂️ <b>Exceeded by:</b> {len(text) - self.config.max_message_length} characters\n\n"
                f"💡 <b>Tip:</b> Break your text into smaller parts and send multiple messages.",
                parse_mode='HTML'
            )
            return

        # Check for empty or very short messages
        if len(text.strip()) < 2:
            await update.message.reply_text(
                "❌ <b>Message Too Short!</b>\n\n"
                "Please send at least 2 characters of text for speech generation.\n\n"
                "💡 <b>Example:</b> Try sending 'Hello world!'",
                parse_mode='HTML'
            )
            return

        # Send "generating" message with progress
        status_msg = await update.message.reply_text(
            f"🔄 <b>Generating audio...</b>\n\n"
            f"👤 <b>User:</b> {user_name}\n"
            f"🎤 <b>Voice:</b> {await self.get_user_voice_name(user_id)}\n"
            f"📝 <b>Text:</b> <code>[Content Hidden]</code>\n"
            f"📊 <b>Length:</b> {len(text)} characters",
            parse_mode='HTML'
        )

        try:
            voice_id = await self.get_user_voice_id(user_id)
            voice_name = await self.get_user_voice_name(user_id)

            # Generate audio
            audio_buffer = await self.tts_generator.generate_audio(text, voice_id)

            # Prepare caption with hidden text content
            caption = f"""
🎤 <b>Voice:</b> {voice_name}
👤 <b>User:</b> {user_name}
📝 <b>Text:</b> <code>[Content Hidden]</code>
📊 <b>Stats:</b> {len(text)} chars, {len(text.split())} words
            """.strip()

            # Send audio file with caption
            await update.message.reply_voice(
                voice=audio_buffer,
                caption=caption,
                parse_mode='HTML'
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
                    "❌ <b>API Authentication Failed</b>\n\n"
                    "The ElevenLabs API key is invalid or expired.\n"
                    "Please contact the bot administrator.",
                    parse_mode='HTML'
                )
            elif "Quota exceeded" in error_msg:
                await status_msg.edit_text(
                    "❌ <b>Service Quota Exceeded</b>\n\n"
                    "The ElevenLabs subscription limit has been reached.\n"
                    "Please try again later or contact the administrator.",
                    parse_mode='HTML'
                )
            elif "Rate limit exceeded" in error_msg:
                await status_msg.edit_text(
                    "❌ <b>API Rate Limit Exceeded</b>\n\n"
                    "Too many requests to ElevenLabs API.\n"
                    "Please wait a moment and try again.",
                    parse_mode='HTML'
                )
            else:
                await status_msg.edit_text(
                    f"❌ <b>Audio Generation Failed</b>\n\n"
                    f"An error occurred while generating your audio.\n"
                    f"Please try again in a moment.\n\n"
                    f"<b>Error:</b> {error_msg[:100]}{'...' if len(error_msg) > 100 else ''}",
                    parse_mode='HTML'
                )

    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle errors"""
        logger.error(f"Update {update} caused error {context.error}")
        await self.increment_usage("errors")

        # Try to inform user about the error
        try:
            if update and update.effective_message:
                await update.effective_message.reply_text(
                    "❌ <b>Unexpected Error</b>\n\n"
                    "An unexpected error occurred while processing your request.\n"
                    "Please try again later.\n\n"
                    "If the problem persists, contact the bot administrator.",
                    parse_mode='HTML'
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
        self.application.add_handler(CommandHandler("voices", self.list_voices_command))
        self.application.add_handler(CommandHandler("setvoice", self.set_voice_command))
        self.application.add_handler(CommandHandler("stats", self.stats_command))

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

            logger.info("Starting Telegram TTS Bot with enhanced HTML formatting...")
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
        logger.info("Starting ElevenLabs Telegram TTS Bot with HTML formatting")
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
