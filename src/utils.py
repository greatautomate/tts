import time
import asyncio
import logging
from functools import wraps
from typing import Dict, List, Optional, Union
from io import BytesIO

# Use the official ElevenLabs client
from elevenlabs.client import ElevenLabs
from elevenlabs import Voice

logger = logging.getLogger(__name__)

class TTSGenerator:
    """Text-to-speech generation using official ElevenLabs Python SDK"""

    def __init__(self, api_key: str, default_voice: str, default_model: str):
        self.api_key = api_key
        self.default_voice = default_voice
        self.default_model = default_model

        # Initialize ElevenLabs client
        self.client = ElevenLabs(api_key=api_key)

    def _is_rate_limit_error(self, error: Exception) -> bool:
        """Check if error is rate limiting related"""
        error_indicators = [
            "rate limit", "429", "too many requests", 
            "quota exceeded", "throttle"
        ]
        error_str = str(error).lower()
        return any(indicator in error_str for indicator in error_indicators)

    def _is_auth_error(self, error: Exception) -> bool:
        """Check if error is authentication related"""
        auth_indicators = [
            "auth", "401", "unauthorized", "invalid api key",
            "forbidden", "access denied"
        ]
        error_str = str(error).lower()
        return any(indicator in error_str for indicator in auth_indicators)

    def _is_quota_error(self, error: Exception) -> bool:
        """Check if error is quota/billing related"""
        quota_indicators = [
            "quota", "billing", "payment", "subscription",
            "credits", "usage limit"
        ]
        error_str = str(error).lower()
        return any(indicator in error_str for indicator in quota_indicators)

    async def generate_audio(self, text: str, voice_id: str) -> BytesIO:
        """Generate audio using official ElevenLabs SDK"""
        max_retries = 3
        retry_delay = 1

        for attempt in range(max_retries):
            try:
                # Run in thread pool to avoid blocking
                loop = asyncio.get_event_loop()

                # Use the official client
                audio_generator = await loop.run_in_executor(
                    None,
                    lambda: self.client.generate(
                        text=text,
                        voice=voice_id,
                        model=self.default_model
                    )
                )

                # Convert generator to bytes
                audio_bytes = b"".join(audio_generator)
                audio_buffer = BytesIO(audio_bytes)
                audio_buffer.seek(0)
                return audio_buffer

            except Exception as e:
                logger.error(f"Audio generation attempt {attempt + 1} failed: {e}")

                # Handle specific error types
                if self._is_auth_error(e):
                    raise Exception("❌ Authentication failed. Please check your ElevenLabs API key.")

                elif self._is_quota_error(e):
                    raise Exception("❌ Quota exceeded. Please check your ElevenLabs subscription.")

                elif self._is_rate_limit_error(e):
                    if attempt < max_retries - 1:
                        wait_time = retry_delay * (2 ** attempt)
                        logger.warning(f"Rate limited, waiting {wait_time}s...")
                        await asyncio.sleep(wait_time)
                        continue
                    else:
                        raise Exception("❌ Rate limit exceeded. Please try again in a moment.")

                # Generic retry for other errors
                else:
                    if attempt < max_retries - 1:
                        await asyncio.sleep(retry_delay)
                        continue
                    else:
                        raise Exception(f"❌ Audio generation failed: {str(e)}")

    async def get_voices(self) -> List[Voice]:
        """Get available voices using official SDK"""
        try:
            loop = asyncio.get_event_loop()
            voices_response = await loop.run_in_executor(
                None,
                lambda: self.client.voices.get_all()
            )

            # Extract voices from response
            if hasattr(voices_response, 'voices'):
                voices_list = voices_response.voices
            else:
                voices_list = voices_response

            logger.info(f"Successfully fetched {len(voices_list)} voices")
            return voices_list

        except Exception as e:
            logger.error(f"Error fetching voices: {e}")
            if self._is_auth_error(e):
                logger.error("Authentication error when fetching voices")
            return []

    async def test_api_connection(self) -> bool:
        """Test API connection"""
        try:
            test_voices = await self.get_voices()
            return len(test_voices) > 0
        except Exception as e:
            logger.error(f"API connection test failed: {e}")
            return False
