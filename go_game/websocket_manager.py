import json
import asyncio
from fastapi import WebSocket
from typing import Dict, List, Optional, Any
from asyncio import Task, create_task, sleep
import redis.asyncio as redis
from .models import Game, StoneColor, GameStatus
from .schemas import WebSocketMessage, WebSocketMessageType, PlayerConnectionEvent, PlayerDisconnectedMessage, RedisConnectionEvent, PlayerReconnectedMessage
from sqlalchemy.orm import Session
from .game_logic import GameService
from .config import settings
from .logging_config import logger
from .event_manager import get_game_update_channel, get_game_connection_channel, get_challenge_update_channel, redis_manager, RedisManager

class ChallengeConnectionManager:
    def __init__(self, redis_manager: RedisManager = None):
        self.active_connections: Dict[str, list[WebSocket]] = {}
        self.redis = redis_manager or RedisManager()
        
    async def start(self):
        """Start the Redis connection and subscribe to challenge channels"""
        await self.redis.connect()
        await self.redis.subscribe(get_challenge_update_channel(), self._handle_challenge_update)
    
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

    def disconnect(self, websocket: WebSocket, challenge_id: str):
        if challenge_id in self.active_connections:
            if websocket in self.active_connections[challenge_id]:
                self.active_connections[challenge_id].remove(websocket)
            if not self.active_connections[challenge_id]:
                del self.active_connections[challenge_id]

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
        self.subscribed_games = set()  # Track which game channels we're subscribed to
    
    async def start(self):
        """Start the Redis connection"""
        logger.info("Starting Redis connection")
        await self.redis.connect()
        logger.info("ConnectionManager startup completed")
    
    async def _handle_game_update(self, message):
        """Handle game updates from Redis"""
        data = json.loads(message["data"])
        logger.debug(f"Received game update: {data}")
        game_id = data.get("game_id")
        source_id = data.get("source_id")
        if source_id == id(self):
            return
        if game_id and game_id in self.active_connections:
            await self.broadcast_to_game(game_id, data["message"])
    
    async def _handle_game_connection(self, message):
        """Handle connection events from Redis for a specific game"""
        data = json.loads(message["data"])
        action = data.get("action")
        game_id = data.get("game_id")
        source_id = data.get("source_id")
        if source_id == id(self):
            logger.debug(f"Ignoring message from self: {data}")
            return
        
        logger.info(f"Received connection event: {action} for game {game_id}")
        
        if action == "game_abandoned" and game_id:
            # Handle game abandonment
            if game_id in self.active_connections:
                message_data = data.get("message")
                if message_data:
                    await self.broadcast_to_game(game_id, message_data)
                await self.close_game_connections(game_id)
        elif action in ["player_disconnect", "player_reconnect"]:
            # Handle player connection events
            if game_id in self.active_connections:
                message_data = data.get("message")
                if message_data:
                    # Broadcast the connection event to all clients connected to this game
                    await self.broadcast_to_game(game_id, message_data)

    async def connect(self, websocket: WebSocket, game_id: int, player_id: int):
        await websocket.accept()
        logger.info(f"Connected to game {game_id} for player {player_id}")
        
        # Subscribe to game-specific channels if not already subscribed
        if game_id not in self.subscribed_games:
            game_update_channel = get_game_update_channel(game_id)
            game_connection_channel = get_game_connection_channel(game_id)
            await self.redis.subscribe(game_update_channel, self._handle_game_update)
            await self.redis.subscribe(game_connection_channel, self._handle_game_connection)
            self.subscribed_games.add(game_id)
            logger.info(f"Subscribed to channels for game {game_id}")
        
        if game_id not in self.active_connections:
            self.active_connections[game_id] = []
        self.active_connections[game_id].append(websocket)
        
        # Store player connection and cancel any pending disconnect check
        key = f"{game_id}:{player_id}"
        self.player_game_connections[key] = websocket
        await self.cancel_disconnect_check(game_id, player_id)
        logger.info(f"player_game_connections: {self.player_game_connections}")
        logger.info(f"active_connections: {self.active_connections}")

    def disconnect(self, websocket: WebSocket, game_id: int, player_id: int, db: Session):
        if game_id in self.active_connections and websocket in self.active_connections[game_id]:
            self.active_connections[game_id].remove(websocket)
            if not self.active_connections[game_id]:
                del self.active_connections[game_id]
                # We could unsubscribe from the game channels here, but keeping subscriptions
                # active for a while might be beneficial for reconnections
            
        # Remove player connection
        key = f"{game_id}:{player_id}"
        if key in self.player_game_connections:
            del self.player_game_connections[key]
        
        # Create a properly structured disconnection message
        disconnect_message = PlayerDisconnectedMessage(
            data=PlayerConnectionEvent(
                player_id=player_id,
                game_id=game_id
            )
        )
        
        # Broadcast to other players
        asyncio.create_task(self.broadcast_to_game(game_id, disconnect_message.dict()))
        
        # Notify Redis about the disconnection - use game-specific channel
        redis_message = RedisConnectionEvent(
            action="player_disconnect",
            game_id=game_id,
            player_id=player_id,
            source_id=id(self),
            message=disconnect_message.dict()
        )
        asyncio.create_task(self.redis.publish(get_game_connection_channel(game_id), redis_message.dict()))
        
        # Schedule disconnect check
        self.schedule_disconnect_check(game_id, player_id, db)

    async def broadcast_to_game(self, game_id: int, message: dict):
        logger.debug(f"Broadcasting to game {game_id}: {message}")
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
            if game_id in self.active_connections:
                del self.active_connections[game_id]
            
            # Clean up any player-specific connections
            keys_to_remove = [
                key for key in self.player_game_connections.keys()
                if key.startswith(f"{game_id}:")
            ]
            for key in keys_to_remove:
                del self.player_game_connections[key]
            
            # Remove from subscribed games
            if game_id in self.subscribed_games:
                self.subscribed_games.remove(game_id)

    async def handle_disconnect(self, game_id: int, player_id: int, db: Session):
        try:
            logger.info(f"waiting 10 seconds for player {player_id} to reconnect")
            await sleep(10)  # Wait 10 seconds
            logger.info(f"player {player_id} did not reconnect")
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
            
            # Broadcast locally
            await self.broadcast_to_game(game_id, close_message.dict())
            
            # Notify other processes about the abandonment - use game-specific channel
            await self.redis.publish(get_game_connection_channel(game_id), {
                "action": "game_abandoned",
                "game_id": game_id,
                "source_id": id(self),
                "message": close_message.dict()
            })

            # Then close all connections
            await self.close_game_connections(game_id)
        except Exception as e:
            logger.error(f"Error disconnecting from game {game_id} for player {player_id}: {e}")
        finally:
            logger.info(f"disconnecting from game {game_id} for player {player_id}")
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
            
            # Create a properly structured reconnection message
            reconnect_message = PlayerReconnectedMessage(
                data=PlayerConnectionEvent(
                    player_id=player_id,
                    game_id=game_id
                )
            )
        
            # Broadcast to other players in this game
            asyncio.create_task(self.broadcast_to_game(game_id, reconnect_message.dict()))
        
            # Notify other processes about the reconnection - use game-specific channel
            redis_message = RedisConnectionEvent(
                action="player_reconnect",
                game_id=game_id,
                player_id=player_id,
                message=reconnect_message.dict()
            )
            asyncio.create_task(self.redis.publish(f"game_connections:{game_id}", redis_message.dict()))

# Create instances
manager = ConnectionManager(redis_manager)
challenge_manager = ChallengeConnectionManager(redis_manager)
