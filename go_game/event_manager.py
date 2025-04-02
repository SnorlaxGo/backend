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
            logger.info(f"Publishing message to channel {channel}: {message}")
            await self.redis_conn.publish(channel, json.dumps(message))
            logger.debug("Published message to channel %s", channel)
        except Exception as e:
            logger.error("Failed to publish to Redis channel %s: %s", channel, str(e), exc_info=True)
            raise
    
    async def subscribe(self, channel: str, callback):
        """Subscribe to a Redis channel"""
        if not self.redis_conn:
            logger.debug("Redis connection not established, connecting now")
            await self.connect()
        
        try:
            logger.info("Subscribing to Redis channel: %s", channel)
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

redis_manager = RedisManager()