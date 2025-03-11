import json
import asyncio
from fastapi import WebSocket
from typing import Dict, List, Optional, Any
from asyncio import Task, create_task, sleep
import redis.asyncio as redis
from .models import Game, StoneColor, GameStatus
from .schemas import WebSocketMessage, WebSocketMessageType
from sqlalchemy.orm import Session
from .game_logic import GameService
from .config import settings

# Redis connection
REDIS_URL = "redis://localhost:6379/0"  # Update with your Redis URL

class RedisManager:
    """Handles Redis pub/sub for WebSocket communication"""
    
    def __init__(self, redis_url: str = settings.REDIS_URL):
        self.redis_url = redis_url
        self.redis_conn: Optional[redis.Redis] = None
        self.pubsub: Optional[redis.client.PubSub] = None
        self.listener_task: Optional[Task] = None
    
    async def connect(self):
        """Connect to Redis"""
        self.redis_conn = await redis.from_url(self.redis_url)
        self.pubsub = self.redis_conn.pubsub()
    
    async def disconnect(self):
        """Disconnect from Redis"""
        if self.listener_task:
            self.listener_task.cancel()
        
        if self.pubsub:
            await self.pubsub.unsubscribe()
            await self.pubsub.close()
        
        if self.redis_conn:
            await self.redis_conn.close()
    
    async def publish(self, channel: str, message: Any):
        """Publish a message to a Redis channel"""
        if not self.redis_conn:
            await self.connect()
        
        await self.redis_conn.publish(channel, json.dumps(message))
    
    async def subscribe(self, channel: str, callback):
        """Subscribe to a Redis channel"""
        if not self.redis_conn:
            await self.connect()
        
        await self.pubsub.subscribe(**{channel: callback})
        
        # Start listener if not already running
        if not self.listener_task or self.listener_task.done():
            self.listener_task = create_task(self._listener())
    
    async def _listener(self):
        """Listen for messages on subscribed channels"""
        while True:
            try:
                message = await self.pubsub.get_message(ignore_subscribe_messages=True)
                if message:
                    # Process message
                    pass
                await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"Redis listener error: {e}")
                await asyncio.sleep(1)  # Avoid tight loop on error

class ChallengeConnectionManager:
    def __init__(self, redis_manager: RedisManager = None):
        self.active_connections: Dict[str, list[WebSocket]] = {}
        self.redis = redis_manager or RedisManager()
        
    async def start(self):
        """Start the Redis connection and subscribe to challenge channels"""
        await self.redis.connect()
        await self.redis.subscribe("challenge_updates", self._handle_challenge_update)
    
    async def _handle_challenge_update(self, message):
        """Handle challenge updates from Redis"""
        data = json.loads(message["data"])
        challenge_id = data.get("challenge_id")
        if challenge_id and challenge_id in self.active_connections:
            await self.broadcast_to_challenge(challenge_id, data)

    async def connect(self, websocket: WebSocket, challenge_id: str):
        await websocket.accept()
        if challenge_id not in self.active_connections:
            self.active_connections[challenge_id] = []
        self.active_connections[challenge_id].append(websocket)
        
        # Store connection info in Redis
        await self.redis.publish("challenge_connections", {
            "action": "connect",
            "challenge_id": challenge_id
        })

    def disconnect(self, websocket: WebSocket, challenge_id: str):
        if challenge_id in self.active_connections:
            if websocket in self.active_connections[challenge_id]:
                self.active_connections[challenge_id].remove(websocket)
            if not self.active_connections[challenge_id]:
                del self.active_connections[challenge_id]
                
            # Update Redis about disconnection
            asyncio.create_task(self.redis.publish("challenge_connections", {
                "action": "disconnect",
                "challenge_id": challenge_id
            }))

    async def broadcast_to_challenge(self, challenge_id: str, message: dict):
        if challenge_id in self.active_connections:
            for connection in self.active_connections[challenge_id]:
                await connection.send_json(message)


class ConnectionManager:
    def __init__(self, redis_manager: RedisManager = None):
        self.active_connections: Dict[int, List[WebSocket]] = {}
        self.player_game_connections: Dict[str, WebSocket] = {}  # "game_id:player_id" -> websocket
        self.disconnect_tasks: Dict[str, Task] = {}  # "game_id:player_id" -> task
        self.redis = redis_manager or RedisManager()
    
    async def start(self):
        """Start the Redis connection and subscribe to game channels"""
        await self.redis.connect()
        await self.redis.subscribe("game_updates", self._handle_game_update)
        await self.redis.subscribe("disconnect_requests", self._handle_disconnect_request)
    
    async def _handle_game_update(self, message):
        """Handle game updates from Redis"""
        data = json.loads(message["data"])
        game_id = data.get("game_id")
        if game_id and game_id in self.active_connections:
            await self.broadcast_to_game(game_id, data["message"])
    
    async def _handle_disconnect_request(self, message):
        """Handle disconnect requests from Redis"""
        data = json.loads(message["data"])
        game_id = data.get("game_id")
        if game_id and game_id in self.active_connections:
            await self.close_game_connections(game_id)

    async def connect(self, websocket: WebSocket, game_id: int, player_id: int):
        await websocket.accept()
        print(f"Connected to game {game_id} for player {player_id}")
        if game_id not in self.active_connections:
            self.active_connections[game_id] = []
        self.active_connections[game_id].append(websocket)
        
        # Store player connection and cancel any pending disconnect check
        key = f"{game_id}:{player_id}"
        self.player_game_connections[key] = websocket
        await self.cancel_disconnect_check(game_id, player_id)
        


    def disconnect(self, websocket: WebSocket, game_id: int, player_id: int, db: Session):
        if game_id in self.active_connections and websocket in self.active_connections[game_id]:
            self.active_connections[game_id].remove(websocket)
            if not self.active_connections[game_id]:
                del self.active_connections[game_id]
            
        # Remove player connection and schedule disconnect check
        key = f"{game_id}:{player_id}"
        if key in self.player_game_connections:
            del self.player_game_connections[key]
        
        # Notify Redis about the disconnection
        asyncio.create_task(self.redis.publish("game_connections", {
            "action": "disconnect",
            "game_id": game_id,
            "player_id": player_id
        }))
        
        self.schedule_disconnect_check(game_id, player_id, db)

    async def broadcast_to_game(self, game_id: int, message: dict):
        print(f"Broadcasting to game {game_id}: {message}")
        if game_id in self.active_connections:
            for connection in self.active_connections[game_id]:
                await connection.send_json(message)

    async def close_game_connections(self, game_id: int):
        """Close all connections for a game"""
        if game_id in self.active_connections:
            # Then close all connections
            for connection in self.active_connections[game_id]:
                await connection.close(code=1000)  # 1000 is normal closure
            
            # Clean up connections
            del self.active_connections[game_id]
            
            # Clean up any player-specific connections
            keys_to_remove = [
                key for key in self.player_game_connections.keys()
                if key.startswith(f"{game_id}:")
            ]
            for key in keys_to_remove:
                del self.player_game_connections[key]

    async def handle_disconnect(self, game_id: int, player_id: int, db: Session):
        try:
            print("waiting 10 seconds")
            await sleep(10)  # Wait 10 seconds
            
            # Check if player reconnected
            key = f"{game_id}:{player_id}"
            if key in self.player_game_connections:
                return  # Player reconnected, no need to abandon game
            
            # Check if game should be marked as abandoned
            gs = GameService(db)
            game = gs.get_game(game_id)
            if not game or game.status != GameStatus.ACTIVE:
                return
                
            is_black = game.black_player_id == player_id
            game.status = GameStatus.BLACK_ABANDONED if is_black else GameStatus.WHITE_ABANDONED
            db.commit()
            
            # Notify remaining players
            close_message = WebSocketMessage(
                type=WebSocketMessageType.GAME_ABANDONED,
                data=gs.to_response(game)
            )
            
            # Broadcast locally and via Redis
            await self.broadcast_to_game(game_id, close_message.dict())
            await self.redis.publish("game_updates", {
                "game_id": game_id,
                "message": close_message.dict()
            })

            # Then close all connections
            await self.close_game_connections(game_id)
        finally:
            key = f"{game_id}:{player_id}"
            if key in self.disconnect_tasks:
                del self.disconnect_tasks[key]

    def schedule_disconnect_check(self, game_id: int, player_id: int, db: Session):
        key = f"{game_id}:{player_id}"
        if key in self.disconnect_tasks:
            self.disconnect_tasks[key].cancel()
        self.disconnect_tasks[key] = create_task(self.handle_disconnect(game_id, player_id, db))

    async def cancel_disconnect_check(self, game_id: int, player_id: int):
        key = f"{game_id}:{player_id}"
        if key in self.disconnect_tasks:
            self.disconnect_tasks[key].cancel()
            del self.disconnect_tasks[key]

# Create instances
redis_manager = RedisManager()
manager = ConnectionManager(redis_manager)
challenge_manager = ChallengeConnectionManager(redis_manager)
