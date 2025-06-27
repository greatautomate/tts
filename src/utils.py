
import time
import asyncio
import logging
from functools import wraps
from typing import Dict, List, Optional
from io import BytesIO
from elevenlabs import generate, voices, RateLimitError, AuthenticationError

logger = logging.getLogger(__name__)

class TTSGenerator:
    """Text-to-speech generation utility with async support"""
    
    def __init__(self, api_key: str, default_voice: str, default_model: str):
        self.api_key = api_key
        self.default_voice = default_voice
        self.default_model = default_model
        
    async def generate_audio(self, text: str, voice_id: str) -> BytesIO:
        """Generate audio with error handling and retries"""
        max_retries = 3
        retry_delay = 1
        
        for attempt in range(max_retries):
            try:
                # Run in thread pool to avoid blocking
                loop = asyncio.get_event_loop()
                audio = await loop.run_in_executor(
                    None,
                    lambda: generate(
                        text=text,
                        voice=voice_id,
                        model=self.default_model,
                        stream=False
                    )
                )
                
                audio_buffer = BytesIO(audio)
                audio_buffer.seek(0)
                return audio_buffer
                
            except RateLimitError:
                if attempt < max_retries - 1:
                    wait_time = retry_delay * (2 ** attempt)
                    logger.warning(f"Rate limited, waiting {wait_time}s...")
                    await asyncio.sleep(wait_time)
                    continue
                raise Exception("Rate limit exceeded. Please try again later.")
                
            except AuthenticationError:
                raise Exception("Authentication failed. Please check API key.")
                
            except Exception as e:
                if attempt < max_retries - 1:
                    logger.warning(f"Attempt {attempt + 1} failed: {e}")
                    await asyncio.sleep(retry_delay)
                    continue
                raise Exception(f"Audio generation failed: {str(e)}")
    
    async def get_voices(self) -> List:
        """Get available voices"""
        try:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, voices)
        except Exception as e:
            logger.error(f"Error fetching voices: {e}")
            return []


