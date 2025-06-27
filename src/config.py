
import os
from typing import Optional
from dataclasses import dataclass

@dataclass
class Config:
    """Application configuration with Redis support"""
    
    # Required environment variables
    telegram_bot_token: str
    elevenlabs_api_key: str
    
    # Optional environment variables
    log_level: str = "INFO"
    environment: str = "development"
    redis_url: Optional[str] = None
    
    # Bot settings
    max_message_length: int = 2500
    rate_limit_calls: int = 10
    rate_limit_window: int = 60
    default_voice: str = "JBFqnCBsd6RMkjVDRZzb"
    default_model: str = "eleven_multilingual_v2"
    
    # Redis settings
    redis_key_prefix: str = "tts_bot"
    redis_user_settings_ttl: int = 86400 * 30  # 30 days
    redis_rate_limit_ttl: int = 3600  # 1 hour
    
    @classmethod
    def from_env(cls) -> "Config":
        """Create config from environment variables"""
        telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
        elevenlabs_key = os.getenv("ELEVENLABS_API_KEY")
        
        if not telegram_token:
            raise ValueError("TELEGRAM_BOT_TOKEN environment variable is required")
        if not elevenlabs_key:
            raise ValueError("ELEVENLABS_API_KEY environment variable is required")
            
        return cls(
            telegram_bot_token=telegram_token,
            elevenlabs_api_key=elevenlabs_key,
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            environment=os.getenv("ENVIRONMENT", "development"),
            redis_url=os.getenv("REDIS_URL"),
            max_message_length=int(os.getenv("MAX_MESSAGE_LENGTH", "2500")),
            rate_limit_calls=int(os.getenv("RATE_LIMIT_CALLS", "10")),
            rate_limit_window=int(os.getenv("RATE_LIMIT_WINDOW", "60")),
            redis_key_prefix=os.getenv("REDIS_KEY_PREFIX", "tts_bot"),
            redis_user_settings_ttl=int(os.getenv("REDIS_USER_SETTINGS_TTL", str(86400 * 30))),
            redis_rate_limit_ttl=int(os.getenv("REDIS_RATE_LIMIT_TTL", "3600"))
        )


