import os
import sys
import logging
import asyncio
import signal
from typing import Dict, Any, Optional
from io import BytesIO

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# Updated ElevenLabs import - handle API changes
try:
    from elevenlabs import ElevenLabs
    ELEVENLABS_V1 = True
except ImportError:
    try:
        from elevenlabs import set_api_key
        ELEVENLABS_V1 = False
    except ImportError:
        raise ImportError("ElevenLabs library not properly installed")

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
    """Enhanced Telegram TTS Bot with Redis integration"""

    def __init__(self, config: Config):
        self.config = config

        # Initialize Redis client
        self.redis_client = None
        if config.redis_url:
            self.redis_client = RedisClient(
                redis_url=config.redis_url,
                key_prefix=config.redis_key_prefix
            )

        # Initialize ElevenLabs - handle both old and new API
        if ELEVENLABS_V1:
            # New API with client
            self.elevenlabs_client = ElevenLabs(api_key=config.elevenlabs_api_key)
        else:
            # Old API with set_api_key
            set_api_key(config.elevenlabs_api_key)
            self.elevenlabs_client = None

        self.tts_generator = TTSGenerator(
            api_key=config.elevenlabs_api_key,
            default_voice=config.default_voice,
            default_model=config.default_model,
            client=self.elevenlabs_client
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
        welcome_message = """
🎤 **Welcome to ElevenLabs TTS Bot!**

Send me any text and I'll convert it to speech using AI voices.

**Commands:**
/start - Show this welcome message
/voices - List available voices
/setvoice [voice_name] - Change your voice
/settings - View your current settings
/stats - View your usage statistics
/help - Get help

Just send me any text message to convert it to speech!
        """
        await update.message.reply_text(welcome_message, parse_mode='Markdown')
        await self.increment_usage("start_command", update.effective_user.id)
        logger.info(f"User {update.effective_user.id} started the bot")

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command"""
        help_text = f"""
**How to use this bot:**

1. **Convert text to speech**: Simply send any text message
2. **Change voice**: Use /setvoice followed by a voice name
3. **List voices**: Use /voices to see available options

**Tips:**
- Keep messages under {self.config.max_message_length} characters
- The bot supports multiple languages
- Voice quality may vary based on your ElevenLabs plan

**Current voice**: {await self.get_user_voice_name(update.effective_user.id)}

**Rate Limits:** {self.config.rate_limit_calls} requests per {self.config.rate_limit_window} seconds
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

        settings_text = f"""
⚙️ **Your Settings:**

**Voice**: {await self.get_user_voice_name(user_id)}
**Model**: {self.config.default_model}
**Max Message Length**: {self.config.max_message_length} characters

**Rate Limiting:**
- Limit: {self.config.rate_limit_calls} requests per {self.config.rate_limit_window} seconds
- Current Usage: {rate_status['calls']} requests
- Reset in: {rate_status['remaining_time']} seconds

**Storage**: {'Redis (Persistent)' if self.redis_client else 'In-Memory (Session Only)'}
        """
        await update.message.reply_text(settings_text, parse_mode='Markdown')
        await self.increment_usage("settings_command", user_id)

    async def stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /stats command"""
        if not self.redis_client:
            await update.message.reply_text("📊 Statistics are not available without Redis.")
            return

        try:
            stats = await self.redis_client.get_usage_stats()
            stats_text = "📊 **Bot Statistics:**\n\n"

            for metric, count in stats.items():
                formatted_metric = metric.replace("_", " ").title()
                stats_text += f"• **{formatted_metric}**: {count}\n"

            if not stats:
                stats_text += "No statistics available yet."

            await update.message.reply_text(stats_text, parse_mode='Markdown')

        except Exception as e:
            logger.error(f"Error getting stats: {e}")
            await update.message.reply_text("❌ Error retrieving statistics.")

    async def list_voices_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /voices command with caching"""
        try:
            # Try to get cached voices first
            available_voices = None
            if self.redis_client:
                cached_voices = await self.redis_client.get_cached_voices()
                if cached_voices:
                    available_voices = [type('Voice', (), voice) for voice in cached_voices]

            # If no cache, fetch from API
            if not available_voices:
                available_voices = await self.tts_generator.get_voices()

                # Cache the results
                if self.redis_client and available_voices:
                    voices_data = [{"name": v.name, "voice_id": v.voice_id} for v in available_voices]
                    await self.redis_client.cache_voices(voices_data, ttl=3600)  # Cache for 1 hour

            if not available_voices:
                await update.message.reply_text("❌ Unable to fetch voices. Please try again later.")
                return

            voice_list = "🎭 **Available Voices:**\n\n"
            for voice in available_voices[:15]:  # Limit for readability
                voice_list += f"• **{voice.name}**\n"

            voice_list += f"\nUse `/setvoice [voice_name]` to change your voice"
            await update.message.reply_text(voice_list, parse_mode='Markdown')
            await self.increment_usage("voices_command", update.effective_user.id)

        except Exception as e:
            logger.error(f"Error in list_voices_command: {e}")
            await update.message.reply_text("❌ Error fetching voices. Please try again later.")

    async def set_voice_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /setvoice command"""
        if not context.args:
            await update.message.reply_text(
                "Please specify a voice name. Use /voices to see available options."
            )
            return

        voice_name = " ".join(context.args)
        user_id = update.effective_user.id

        try:
            available_voices = await self.tts_generator.get_voices()
            selected_voice = None

            for voice in available_voices:
                if voice.name.lower() == voice_name.lower():
                    selected_voice = voice
                    break

            if selected_voice:
                await self.save_user_settings(user_id, {
                    'voice_id': selected_voice.voice_id,
                    'voice_name': selected_voice.name
                })

                await update.message.reply_text(
                    f"✅ Voice changed to **{selected_voice.name}**", 
                    parse_mode='Markdown'
                )
                await self.increment_usage("voice_change", user_id)
                logger.info(f"User {user_id} changed voice to {selected_voice.name}")
            else:
                await update.message.reply_text(
                    "❌ Voice not found. Use /voices to see available options."
                )

        except Exception as e:
            logger.error(f"Error in set_voice_command: {e}")
            await update.message.reply_text("❌ Error setting voice. Please try again later.")

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

        # Check rate limits
        if not await self.check_rate_limit(user_id):
            rate_status = {"remaining_time": 5}
            if self.redis_client:
                rate_status = await self.redis_client.get_rate_limit_status(
                    user_id, self.config.rate_limit_window
                )

            await update.message.reply_text(
                f"⏰ Rate limit reached! Please wait {rate_status['remaining_time']} seconds before making more requests."
            )
            return

        # Validate message length
        if len(text) > self.config.max_message_length:
            await update.message.reply_text(
                f"❌ Message too long! Please keep it under {self.config.max_message_length} characters."
            )
            return

        # Send "generating" message
        status_msg = await update.message.reply_text("🔄 Generating audio...")

        try:
            voice_id = await self.get_user_voice_id(user_id)
            audio_buffer = await self.tts_generator.generate_audio(text, voice_id)

            # Send audio file
            await update.message.reply_voice(
                voice=audio_buffer,
                caption=f"🎤 **Generated with voice**: {await self.get_user_voice_name(user_id)}",
                parse_mode='Markdown'
            )

            await status_msg.delete()
            await self.increment_usage("tts_generation", user_id)
            await self.increment_usage("characters_processed")
            logger.info(f"Generated audio for user {user_id}, text length: {len(text)}")

        except Exception as e:
            logger.error(f"Error generating audio for user {user_id}: {e}")
            await status_msg.edit_text(f"❌ Error generating audio: {str(e)}")

    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle errors"""
        logger.error(f"Update {update} caused error {context.error}")
        await self.increment_usage("errors")

    def setup_handlers(self):
        """Setup bot handlers"""
        if not self.application:
            return

        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(CommandHandler("settings", self.settings_command))
        self.application.add_handler(CommandHandler("stats", self.stats_command))
        self.application.add_handler(CommandHandler("voices", self.list_voices_command))
        self.application.add_handler(CommandHandler("setvoice", self.set_voice_command))
        self.application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text_message)
        )
        self.application.add_error_handler(self.error_handler)

    async def start(self):
        """Start the bot"""
        try:
            await self.start_bot()

            self.application = Application.builder().token(self.config.telegram_bot_token).build()
            self.setup_handlers()

            logger.info("Starting Telegram TTS Bot with Redis...")
            await self.application.initialize()
            await self.application.start()
            await self.application.updater.start_polling(allowed_updates=Update.ALL_TYPES)

            logger.info("Bot is running...")

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

def signal_handler(bot: TelegramTTSBot):
    """Handle shutdown signals"""
    def handler(signum, frame):
        logger.info(f"Received signal {signum}")
        asyncio.create_task(bot.stop())
    return handler

async def main():
    """Main entry point"""
    try:
        # Load configuration
        config = Config.from_env()
        setup_logging(config.log_level)

        # Create and start bot
        bot = TelegramTTSBot(config)

        # Setup signal handlers for graceful shutdown
        for sig in [signal.SIGTERM, signal.SIGINT]:
            signal.signal(sig, signal_handler(bot))

        await bot.start()

    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    # Create logs directory if it doesn't exist
    os.makedirs("logs", exist_ok=True)
    asyncio.run(main())
