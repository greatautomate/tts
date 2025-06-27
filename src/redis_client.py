
import json
import time
import logging
from typing import Optional, Dict, Any, List
import redis.asyncio as redis
from redis.asyncio import Redis

logger = logging.getLogger(__name__)

class RedisClient:
    """Async Redis client for bot data management"""
    
    def __init__(self, redis_url: str, key_prefix: str = "tts_bot"):
        self.redis_url = redis_url
        self.key_prefix = key_prefix
        self.redis: Optional[Redis] = None
        
    async def connect(self):
        """Initialize Redis connection"""
        try:
            self.redis = redis.from_url(
                self.redis_url,
                encoding="utf-8",
                decode_responses=True,
                socket_keepalive=True,
                socket_keepalive_options={},
                health_check_interval=30
            )
            # Test connection
            await self.redis.ping()
            logger.info("Successfully connected to Redis")
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
            raise
    
    async def disconnect(self):
        """Close Redis connection"""
        if self.redis:
            await self.redis.close()
            logger.info("Redis connection closed")
    
    def _make_key(self, key: str) -> str:
        """Create prefixed key"""
        return f"{self.key_prefix}:{key}"
    
    # User Settings Methods
    async def get_user_settings(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Get user settings from Redis"""
        if not self.redis:
            return None
            
        try:
            key = self._make_key(f"user:{user_id}")
            data = await self.redis.get(key)
            if data:
                return json.loads(data)
            return None
        except Exception as e:
            logger.error(f"Error getting user settings for {user_id}: {e}")
            return None
    
    async def set_user_settings(self, user_id: int, settings: Dict[str, Any], ttl: int = 86400 * 30):
        """Save user settings to Redis with TTL"""
        if not self.redis:
            return False
            
        try:
            key = self._make_key(f"user:{user_id}")
            data = json.dumps(settings)
            await self.redis.setex(key, ttl, data)
            logger.debug(f"Saved user settings for {user_id}")
            return True
        except Exception as e:
            logger.error(f"Error saving user settings for {user_id}: {e}")
            return False
    
    async def delete_user_settings(self, user_id: int):
        """Delete user settings"""
        if not self.redis:
            return False
            
        try:
            key = self._make_key(f"user:{user_id}")
            await self.redis.delete(key)
            return True
        except Exception as e:
            logger.error(f"Error deleting user settings for {user_id}: {e}")
            return False
    
    # Rate Limiting Methods
    async def check_rate_limit(self, user_id: int, max_calls: int, window: int) -> bool:
        """Check if user is within rate limits"""
        if not self.redis:
            return True  # Allow if Redis unavailable
            
        try:
            key = self._make_key(f"rate_limit:{user_id}")
            current_time = int(time.time())
            
            # Use Redis pipeline for atomic operations
            async with self.redis.pipeline() as pipe:
                # Remove old entries
                await pipe.zremrangebyscore(key, 0, current_time - window)
                # Count current entries
                count = await pipe.zcard(key)
                
                if count >= max_calls:
                    return False
                
                # Add current request
                await pipe.zadd(key, {str(current_time): current_time})
                await pipe.expire(key, window)
                await pipe.execute()
                
                return True
                
        except Exception as e:
            logger.error(f"Error checking rate limit for {user_id}: {e}")
            return True  # Allow on error
    
    async def get_rate_limit_status(self, user_id: int, window: int) -> Dict[str, Any]:
        """Get rate limit status for user"""
        if not self.redis:
            return {"calls": 0, "remaining_time": 0}
            
        try:
            key = self._make_key(f"rate_limit:{user_id}")
            current_time = int(time.time())
            
            # Get calls in current window
            calls = await self.redis.zcount(key, current_time - window, current_time)
            
            # Get oldest call time
            oldest_calls = await self.redis.zrange(key, 0, 0, withscores=True)
            remaining_time = 0
            if oldest_calls:
                oldest_time = oldest_calls[0][1]
                remaining_time = max(0, int(window - (current_time - oldest_time)))
            
            return {
                "calls": calls,
                "remaining_time": remaining_time
            }
            
        except Exception as e:
            logger.error(f"Error getting rate limit status for {user_id}: {e}")
            return {"calls": 0, "remaining_time": 0}
    
    # Voice Cache Methods
    async def cache_voices(self, voices_data: List[Dict], ttl: int = 3600):
        """Cache available voices list"""
        if not self.redis:
            return False
            
        try:
            key = self._make_key("voices_cache")
            data = json.dumps(voices_data)
            await self.redis.setex(key, ttl, data)
            return True
        except Exception as e:
            logger.error(f"Error caching voices: {e}")
            return False
    
    async def get_cached_voices(self) -> Optional[List[Dict]]:
        """Get cached voices list"""
        if not self.redis:
            return None
            
        try:
            key = self._make_key("voices_cache")
            data = await self.redis.get(key)
            if data:
                return json.loads(data)
            return None
        except Exception as e:
            logger.error(f"Error getting cached voices: {e}")
            return None
    
    # Analytics Methods
    async def increment_usage_counter(self, metric: str, user_id: Optional[int] = None):
        """Increment usage counters for analytics"""
        if not self.redis:
            return
            
        try:
            # Global counter
            global_key = self._make_key(f"stats:{metric}")
            await self.redis.incr(global_key)
            
            # Daily counter
            from datetime import datetime
            today = datetime.now().strftime("%Y-%m-%d")
            daily_key = self._make_key(f"stats:{metric}:{today}")
            await self.redis.incr(daily_key)
            await self.redis.expire(daily_key, 86400 * 7)  # Keep for 7 days
            
            # User-specific counter
            if user_id:
                user_key = self._make_key(f"stats:user:{user_id}:{metric}")
                await self.redis.incr(user_key)
                await self.redis.expire(user_key, 86400 * 30)  # Keep for 30 days
                
        except Exception as e:
            logger.error(f"Error incrementing usage counter {metric}: {e}")
    
    async def get_usage_stats(self) -> Dict[str, Any]:
        """Get usage statistics"""
        if not self.redis:
            return {}
            
        try:
            stats = {}
            # Get global stats
            keys = await self.redis.keys(self._make_key("stats:*"))
            for key in keys:
                if ":" not in key.split(":")[-1]:  # Global keys only
                    metric = key.split(":")[-1]
                    count = await self.redis.get(key)
                    stats[metric] = int(count) if count else 0
            
            return stats
        except Exception as e:
            logger.error(f"Error getting usage stats: {e}")
            return {}

    async def health_check(self) -> bool:
        """Check Redis health"""
        try:
            if not self.redis:
                return False
            await self.redis.ping()
            return True
        except Exception as e:
            logger.error(f"Redis health check failed: {e}")
            return False


