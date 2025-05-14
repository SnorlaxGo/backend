# go_game/timer_service.py
import asyncio
from datetime import datetime
import json
from .event_manager import redis_manager, get_game_update_channel
from .schemas import TimeoutData, TimeoutMessage, RedisGameUpdate
from .models import StoneColor, GameStatus, Game, TimeControl
from .logging_config import logger
import socket
import uuid
from .database import get_db
import math

# Initialize the timer service with a db factory that works with the existing get_db
def db_factory():
    # Get the generator from get_db
    db_gen = get_db()
    # Get the actual db session
    return next(db_gen)

class GameTimerService:
    def __init__(self, redis_manager, db_factory):
        self.redis = redis_manager
        self.db_factory = db_factory
        self.worker_id = f"{socket.gethostname()}:{uuid.uuid4()}"
        self.is_leader = False
        self.leadership_task = None
        self.heartbeat_task = None
        
        # Configuration
        self.LEASE_TTL = 120       # Lease expires after 30 seconds
        self.HEARTBEAT_INTERVAL = 20  # Renew lease every 5 seconds
        self.ELECTION_INTERVAL = 40  # Try to become leader every 10 seconds
    
    async def start(self):
        """Start the timer service"""
        logger.info(f"Starting GameTimerService with ID {self.worker_id}")
        await self.redis.connect()
        
        # Start leadership management
        self.leadership_task = asyncio.create_task(self._leadership_loop())
    
    async def _leadership_loop(self):
        """Main leadership loop - tries to acquire leadership and monitors status"""
        while True:
            try:
                # Try to acquire leadership
                leader_key = "timer_service_leader"
                acquired = await self.redis.redis_conn.set(
                    leader_key,
                    self.worker_id,
                    nx=True,      # Only set if key doesn't exist
                    ex=self.LEASE_TTL  # Expires after TTL seconds
                )
                
                # If we're not leader, check who is
                is_current_leader = False
                current_leader = await self.redis.redis_conn.get(leader_key)
                if current_leader:
                    is_current_leader = current_leader.decode('utf-8') == self.worker_id
            
                # Handle leadership changes
                if is_current_leader and not self.is_leader:
                    # We just became leader
                    self.is_leader = True
                    logger.info(f"Worker {self.worker_id} became timer service leader")
                    
                    # Start heartbeat task
                    if self.heartbeat_task:
                        self.heartbeat_task.cancel()
                    self.heartbeat_task = asyncio.create_task(self._heartbeat_loop())
                    
                    # Set up expiration listener and check for missed timeouts
                    await self._setup_expiration_listener()
                
                elif not is_current_leader and self.is_leader:
                    # We lost leadership
                    logger.info(f"Worker {self.worker_id} lost timer service leadership somehow")
                    await self._handle_leadership_loss()
                
            except Exception as e:
                logger.error(f"Error in leadership loop: {str(e)}", exc_info=True)
                if self.is_leader:
                    await self._handle_leadership_loss()
            
            # Wait before next attempt
            await asyncio.sleep(self.ELECTION_INTERVAL)
    
    async def _heartbeat_loop(self):
        """Periodically renew our leadership lease while we're leader"""
        try:
            while self.is_leader:
                try:
                    # Renew our lease
                    leader_key = "timer_service_leader"
                    current_leader = await self.redis.redis_conn.get(leader_key)
                    
                    if not current_leader or current_leader.decode('utf-8') != self.worker_id:
                        # We're not recognized as leader anymore
                        logger.warning(f"Worker {self.worker_id} lost leadership during heartbeat")
                        await self._handle_leadership_loss()
                        return
                    
                    # Extend the lease
                    await self.redis.redis_conn.expire(leader_key, self.LEASE_TTL)
                    logger.debug(f"Worker {self.worker_id} renewed leadership lease")
                    
                except Exception as e:
                    logger.error(f"Error in heartbeat: {str(e)}", exc_info=True)
                    await self._handle_leadership_loss()
                    return
                
                # Wait before next heartbeat (shorter than TTL)
                await asyncio.sleep(self.HEARTBEAT_INTERVAL)
                
        except asyncio.CancelledError:
            # Task was cancelled, do nothing
            pass
        except Exception as e:
            logger.error(f"Unexpected error in heartbeat loop: {str(e)}", exc_info=True)
            if self.is_leader:
                await self._handle_leadership_loss()
    
    async def _handle_leadership_loss(self):
        """Handle the case where we lost leadership"""
        if not self.is_leader:
            return
            
        self.is_leader = False
        logger.info(f"Worker {self.worker_id} lost timer service leadership")
        
        # Cancel heartbeat task
        if self.heartbeat_task:
            self.heartbeat_task.cancel()
            self.heartbeat_task = None
        
        # Remove expiration listener
        await self._remove_expiration_listener()

    async def _setup_expiration_listener(self):
        """Set up listener for key expirations (only called when becoming leader)"""
        # Configure Redis to send notifications when keys expire
        await self.redis.redis_conn.config_set('notify-keyspace-events', 'Ex')
        
        # Subscribe to expiration events
        self.expiration_subscription = await self.redis.subscribe(
            '__keyevent@0__:expired', 
            self._handle_expired_key
        )
        logger.info("Leader subscribed to Redis key expiration events")

    async def _remove_expiration_listener(self):
        """Remove listener for key expirations (when losing leadership)"""
        if hasattr(self, 'expiration_subscription'):
            # Unsubscribe from the expiration events
            await self.redis.unsubscribe('__keyevent@0__:expired')
            delattr(self, 'expiration_subscription')
            logger.info("Unsubscribed from Redis key expiration events")
            
    async def _verify_leadership(self):
        """Verify this worker is still the leader by checking the Redis key"""
        try:
            leader_key = "timer_service_leader"
            current_leader = await self.redis.redis_conn.get(leader_key)
            
            if current_leader and current_leader.decode('utf-8') == self.worker_id:
                # We're still the leader
                return True
            else:
                # We're no longer the leader
                if self.is_leader:
                    logger.warning(f"Worker {self.worker_id} lost leadership but didn't detect it yet")
                    self.is_leader = False
                    await self._remove_expiration_listener()
                return False
        except Exception as e:
            logger.error(f"Error verifying leadership: {str(e)}")
            return False  # Assume we're not leader on error
    
    async def _handle_expired_key(self, message):
        """Handle expired keys (timers) - only the leader processes these"""
        # Only process if we're still the leader
        logger.info(f"Leader {self.worker_id} received expired key event")
        if not self.is_leader:
            return
            
        try:
            # Get the expired key
            expired_key = message['data'].decode('utf-8')
            
            # Check if it's a timer key
            if expired_key.startswith('timer:'):
                # Parse game_id and player_color from the key
                _, game_id_str, player_color_str = expired_key.split(':')
                game_id = int(game_id_str)
                player_color = StoneColor(int(player_color_str))
                
                logger.info(f"Timer expired for game {game_id}, player {player_color}")
                
                # Handle the timeout
                await self._process_timeout(game_id, player_color)
        except Exception as e:
            logger.error(f"Error handling expired key: {str(e)}", exc_info=True)

    async def _process_timeout(self, game_id: int, player_color: StoneColor):
        """Process a game timeout"""
        db = self.db_factory()
        try:
            # Get the game
            game = db.query(Game).filter(Game.id == game_id).first()
            
            if not game or game.status != GameStatus.ACTIVE:
                return
            
            # Verify the timeout is still valid
            if (player_color == StoneColor.BLACK and not game.is_black_turn) or \
               (player_color == StoneColor.WHITE and game.is_black_turn):
                logger.warning(f"Invalid timeout - not {player_color}'s turn in game {game_id}")
                return
            
            # Update game status
            if player_color == StoneColor.BLACK:
                game.status = GameStatus.WHITE_WON_TIMEOUT
            else:
                game.status = GameStatus.BLACK_WON_TIMEOUT
            
            db.commit()
            logger.info(f"Game {game_id} ended: {player_color} timed out")
            
            # Create timeout message using proper schema
            timeout_data = TimeoutData(
                timeout_player=player_color,
                status=game.status,
                game_id=game.id
            )
            
            timeout_message = TimeoutMessage(data=timeout_data)
                # Create proper Redis message
            redis_message = RedisGameUpdate(
                game_id=game_id,
                message=timeout_message.dict(),
                source_id=None  # System-generated message
            )
            # Publish to Redis for all workers to broadcast
            await self.redis.publish(
                get_game_update_channel(game_id),
                redis_message.dict()
            )
            
        finally:
            db.close()
            
    async def cancel_timer(self, game_id: int, player_color: StoneColor):
        """Cancel a timer for a game"""
        timer_key = f"timer:{game_id}:{player_color.value}"
        
        # Delete the timer key
        await self.redis.redis_conn.delete(timer_key)
        logger.info(f"Cancelled timer for game {game_id}, player {player_color.value}")

    async def set_timer(self, game_id: int, player_color: StoneColor, time_remaining: float):
        """Set a timer for a game"""
        # Round to integer seconds (ceiling to be conservative)
        seconds = max(1, int(math.ceil(time_remaining)))
        
        timer_key = f"timer:{game_id}:{player_color.value}"
        
        # Set the key with expiration
        await self.redis.redis_conn.set(timer_key, "1", ex=seconds)
        logger.info(f"Set timer for game {game_id}, player {player_color.value}: {seconds}s")

# Create singleton instance
timer_service = GameTimerService(redis_manager, db_factory)