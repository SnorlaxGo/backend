from .config import settings
from .logging_config import logger
import redis.asyncio as redis
from typing import Optional, Any
import json
import asyncio
from asyncio import Task
import uuid

def get_game_update_channel(game_id: int) -> str:
    """Get the Redis channel name for game updates"""
    return f"game_updates:{game_id}"

def get_game_connection_channel(game_id: int) -> str:
    """Get the Redis channel name for game connection events"""
    return f"game_connections:{game_id}"

def get_challenge_update_channel() -> str:
    """Get the Redis channel name for challenge updates"""
    return f"challenge_updates"

class RedisManager:
    """Handles Redis pub/sub for WebSocket communication"""
    
    def __init__(self, redis_url: str = settings.REDIS_URL):
        self.redis_url = redis_url
        self.redis_conn: Optional[redis.Redis] = None
        self.pubsub: Optional[redis.client.PubSub] = None
        self.listener_task: Optional[Task] = None
        self.instance_id = str(uuid.uuid4())  # Generate a unique ID for this instance
    
    async def connect(self):
        """Connect to Redis"""
        logger.info("Connecting to Redis at %s", self.redis_url)
        try:
            self.redis_conn = await redis.from_url(self.redis_url)
            self.pubsub = self.redis_conn.pubsub()
            self.listener_task = asyncio.create_task(self.pubsub.run())
            logger.info("Successfully connected to Redis")
        except Exception as e:
            logger.error("Failed to connect to Redis: %s", str(e), exc_info=True)
            raise
    
    async def disconnect(self):
        """Disconnect from Redis"""
        logger.info("Disconnecting from Redis")
        try:
            if self.listener_task:
                self.listener_task.cancel()
            
            if self.pubsub:
                await self.pubsub.unsubscribe()
                await self.pubsub.close()
            
            if self.redis_conn:
                await self.redis_conn.close()
            logger.info("Successfully disconnected from Redis")
        except Exception as e:
            logger.error("Error disconnecting from Redis: %s", str(e), exc_info=True)
    
    async def publish(self, channel: str, message: Any):
        """Publish a message to a Redis channel"""
        if not self.redis_conn:
            logger.debug("Redis connection not established, connecting now")
            await self.connect()

        message["source_id"] = self.instance_id

        try:
            logger.debug(f"Publishing message to channel {channel}: {message}")
            await self.redis_conn.publish(channel, json.dumps(message))
            logger.debug("Published message to channel %s", channel)
        except Exception as e:
            logger.error("Failed to publish to Redis channel %s: %s", channel, str(e), exc_info=True)
            raise
    
    async def subscribe(self, channel: str, callback):
        """Subscribe to a Redis channel"""
        if not self.redis_conn:
            logger.info("Redis connection not established, connecting now")
            await self.connect()
        
        try:
            logger.debug("Subscribing to Redis channel: %s", channel)
            await self.pubsub.subscribe(**{channel: callback})
        except Exception as e:
            logger.error("Failed to subscribe to Redis channel %s: %s", channel, str(e), exc_info=True)
            raise

    async def unsubscribe(self, channel: str):
        """Unsubscribe from a Redis channel"""
        if not self.redis_conn:
            logger.warning("Cannot unsubscribe - Redis connection not established")
            return
            
        try:
            logger.info("Unsubscribing from Redis channel: %s", channel)
            await self.pubsub.unsubscribe(channel)
        except Exception as e:
            logger.error("Failed to unsubscribe from Redis channel %s: %s", channel, str(e), exc_info=True)
            raise

    async def set(self, key: str, value: Any, ex: Optional[int] = None):
        """Set a key-value pair with optional expiration"""
        if not self.redis_conn:
            await self.connect()
        
        try:
            if isinstance(value, (dict, list)):
                value = json.dumps(value)
            
            if ex:
                await self.redis_conn.setex(key, ex, value)
            else:
                await self.redis_conn.set(key, value)
            logger.debug(f"Set key {key}")
        except Exception as e:
            logger.error(f"Failed to set key {key}: {str(e)}", exc_info=True)
            raise
    
    async def get(self, key: str) -> Optional[Any]:
        """Get a value by key, automatically parsing JSON if possible"""
        if not self.redis_conn:
            await self.connect()
        
        try:
            data = await self.redis_conn.get(key)
            if data is None:
                return None
            
            # Decode bytes to string
            if isinstance(data, bytes):
                data = data.decode('utf-8')
            
            # Try to parse as JSON, fall back to string
            try:
                return json.loads(data)
            except (json.JSONDecodeError, TypeError):
                return data
        except Exception as e:
            logger.error(f"Failed to get key {key}: {str(e)}", exc_info=True)
            return None
    
    async def keys(self, pattern: str) -> list:
        """Get keys matching a pattern"""
        if not self.redis_conn:
            await self.connect()
        
        try:
            keys = await self.redis_conn.keys(pattern)
            # Decode bytes keys to strings
            return [key.decode('utf-8') if isinstance(key, bytes) else key for key in keys]
        except Exception as e:
            logger.error(f"Failed to get keys with pattern {pattern}: {str(e)}", exc_info=True)
            return []
    
    async def delete(self, *keys: str):
        """Delete one or more keys"""
        if not self.redis_conn:
            await self.connect()
        
        try:
            if keys:
                await self.redis_conn.delete(*keys)
                logger.debug(f"Deleted keys: {keys}")
        except Exception as e:
            logger.error(f"Failed to delete keys {keys}: {str(e)}", exc_info=True)
            raise
    
    async def exists(self, key: str) -> bool:
        """Check if a key exists"""
        if not self.redis_conn:
            await self.connect()
        
        try:
            return bool(await self.redis_conn.exists(key))
        except Exception as e:
            logger.error(f"Failed to check existence of key {key}: {str(e)}", exc_info=True)
            return False

redis_manager = RedisManager()