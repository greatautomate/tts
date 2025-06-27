import time
import asyncio
import logging
import requests
from functools import wraps
from typing import Dict, List, Optional
from io import BytesIO

logger = logging.getLogger(__name__)

class TTSGenerator:
    """Text-to-speech generation using direct HTTP requests to ElevenLabs API"""

    def __init__(self, api_key: str, default_voice: str, default_model: str):
        self.api_key = api_key
        self.default_voice = default_voice
        self.default_model = default_model
        self.base_url = "https://api.elevenlabs.io/v1"
        self.headers = {
            "Accept": "audio/mpeg",
            "Content-Type": "application/json",
            "xi-api-key": self.api_key
        }

    def _is_rate_limit_error(self, status_code: int, response_text: str) -> bool:
        """Check if error is rate limiting related"""
        return status_code == 429 or "rate limit" in response_text.lower()

    def _is_auth_error(self, status_code: int, response_text: str) -> bool:
        """Check if error is authentication related"""
        return status_code == 401 or "unauthorized" in response_text.lower()

    def _is_quota_error(self, status_code: int, response_text: str) -> bool:
        """Check if error is quota/billing related"""
        quota_indicators = ["quota", "billing", "payment", "subscription", "credits"]
        return any(indicator in response_text.lower() for indicator in quota_indicators)

    async def generate_audio(self, text: str, voice_id: str) -> BytesIO:
        """Generate audio using direct HTTP requests"""
        max_retries = 3
        retry_delay = 1

        url = f"{self.base_url}/text-to-speech/{voice_id}"
        data = {
            "text": text,
            "model_id": self.default_model,
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.5
            }
        }

        for attempt in range(max_retries):
            try:
                # Run in thread pool to avoid blocking
                loop = asyncio.get_event_loop()
                response = await loop.run_in_executor(
                    None,
                    lambda: requests.post(url, json=data, headers=self.headers, timeout=30)
                )

                if response.status_code == 200:
                    audio_buffer = BytesIO(response.content)
                    audio_buffer.seek(0)
                    return audio_buffer

                # Handle specific errors
                response_text = response.text

                if self._is_auth_error(response.status_code, response_text):
                    raise Exception("❌ Authentication failed. Please check your ElevenLabs API key.")

                elif self._is_quota_error(response.status_code, response_text):
                    raise Exception("❌ Quota exceeded. Please check your ElevenLabs subscription.")

                elif self._is_rate_limit_error(response.status_code, response_text):
                    if attempt < max_retries - 1:
                        wait_time = retry_delay * (2 ** attempt)
                        logger.warning(f"Rate limited, waiting {wait_time}s...")
                        await asyncio.sleep(wait_time)
                        continue
                    else:
                        raise Exception("❌ Rate limit exceeded. Please try again in a moment.")

                else:
                    error_msg = f"HTTP {response.status_code}: {response_text}"
                    if attempt < max_retries - 1:
                        logger.warning(f"Attempt {attempt + 1} failed: {error_msg}")
                        await asyncio.sleep(retry_delay)
                        continue
                    else:
                        raise Exception(f"❌ Audio generation failed: {error_msg}")

            except requests.exceptions.RequestException as e:
                if attempt < max_retries - 1:
                    logger.warning(f"Request failed, attempt {attempt + 1}: {e}")
                    await asyncio.sleep(retry_delay)
                    continue
                else:
                    raise Exception(f"❌ Network error: {str(e)}")

            except Exception as e:
                if "Authentication failed" in str(e) or "Quota exceeded" in str(e) or "Rate limit exceeded" in str(e):
                    raise e
                else:
                    if attempt < max_retries - 1:
                        await asyncio.sleep(retry_delay)
                        continue
                    else:
                        raise Exception(f"❌ Audio generation failed: {str(e)}")

    async def get_voices(self) -> List[Dict]:
        """Get available voices using direct HTTP requests"""
        try:
            url = f"{self.base_url}/voices"
            headers = {"xi-api-key": self.api_key}

            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: requests.get(url, headers=headers, timeout=30)
            )

            if response.status_code == 200:
                voices_data = response.json().get("voices", [])
                # Convert to objects with name and voice_id attributes
                voices_list = []
                for voice_data in voices_data:
                    voice_obj = type('Voice', (), {
                        'name': voice_data.get('name', 'Unknown'),
                        'voice_id': voice_data.get('voice_id', ''),
                        **voice_data
                    })()
                    voices_list.append(voice_obj)

                logger.info(f"Successfully fetched {len(voices_list)} voices")
                return voices_list
            else:
                logger.error(f"Failed to fetch voices: HTTP {response.status_code}")
                return []

        except Exception as e:
            logger.error(f"Error fetching voices: {e}")
            return []

    async def test_api_connection(self) -> bool:
        """Test API connection by fetching voices"""
        try:
            voices_list = await self.get_voices()
            return len(voices_list) > 0
        except Exception as e:
            logger.error(f"API connection test failed: {e}")
            return False
