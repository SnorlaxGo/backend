# Create a new file: go_game/game_handlers.py

import logging
from typing import Dict, Any, Tuple, Optional, Callable, Awaitable
from sqlalchemy.orm import Session

from .game_logic import GameService, InvalidMoveError, KoViolationError, SuicideMoveError, MoveResultType
from .schemas import (
    WebSocketResponse, 
    WebSocketResponseType, 
    TimeoutMessage, 
    TimeoutData, 
    RedisGameUpdate
)
from .event_manager import redis_manager, get_game_update_channel
from .loggers import api_logger as logger

async def handle_pass_move(game_id: int, x: int, y: int, user_id: int, username: str, db: Session):
    service = GameService(db)
    

async def process_game_move(
    game_id: int,
    x: int,
    y: int,
    user_id: int,
    username: str,
    db: Session,
    is_correspondence: bool = False
) -> Tuple[str, Dict[str, Any], Optional[str]]:
    """
    Process a game move and handle the result.
    
    Args:
        game_id: The ID of the game
        x: X coordinate of the move
        y: Y coordinate of the move
        user_id: ID of the user making the move
        username: Username of the user making the move
        db: Database session
        is_correspondence: Whether this is a correspondence game
        
    Returns:
        Tuple of (status, response_data, error_message)
        status: 'success', 'timeout', 'game_over', or 'error'
        response_data: Data to return to the client
        error_message: Error message if status is 'error', None otherwise
    """
    logger.info("Move request for game %d by user %s: (%d, %d)", 
               game_id, username, x, y)
    try:
        service = GameService(db)
        result = service.make_move(game_id, x, y, user_id)
        
        # Handle different result types
        if result.type == MoveResultType.TIMEOUT:
            logger.info("Timeout detected in game %d for %s player", 
                       game_id, result.player_color)
            
            # Broadcast the timeout via Redis (skip for correspondence games)
            if not is_correspondence:
                timeout_message = TimeoutMessage(data=TimeoutData(
                    timeout_player=result.player_color,
                    status=result.game.status,
                    game_id=game_id
                ))

                redis_message = RedisGameUpdate(
                    game_id=game_id,
                    message=timeout_message.dict(),
                    source_id=None
                )

                await redis_manager.publish(get_game_update_channel(game_id), redis_message.dict())

            return "timeout", {"status": "timeout", "message": result.message}, None
            
        elif result.type == MoveResultType.GAME_OVER:
            logger.info("Game over detected in game %d", game_id)
            
            game_over_message = WebSocketResponse(
                type=WebSocketResponseType.GAME_OVER,
                data=result.game
            )
            redis_message = RedisGameUpdate(
                game_id=game_id,
                message=game_over_message.dict()            
            )
            await redis_manager.publish(get_game_update_channel(game_id), redis_message.dict())

            return "game_over", {"status": "game_over", "message": result.message}, None
        
        elif result.type == MoveResultType.PASS:
            logger.info("Pass move detected in game %d", game_id)
            pass_message = WebSocketResponse(
                type=WebSocketResponseType.PASS,
                data=result.game
            )
            redis_message = RedisGameUpdate(
                game_id=game_id,
                message=pass_message.dict()
            )
            await redis_manager.publish(get_game_update_channel(game_id), redis_message.dict())
            return "pass", {"status": "pass", "message": result.message}, None
        
        else:  # SUCCESS case
            logger.debug("Broadcasting move for game %d via Redis", game_id)
            ws_resp = WebSocketResponse(
                    type=WebSocketResponseType.GAME_STATE,
                    data=result.game
                )
            redis_message = RedisGameUpdate(
                game_id=game_id,
                message=ws_resp.dict()
            )
            await redis_manager.publish(get_game_update_channel(game_id), redis_message.dict())
            
            logger.info("Move successful for game %d by user %s", game_id, username)
            return "success", {"status": "success"}, None
        
    except (InvalidMoveError, KoViolationError, SuicideMoveError, Exception) as e:
        # Get the error type from the exception class name
        error_type = e.__class__.__name__.replace("Error", "").lower()
        
        logger.warning("%s in game %d by user %s: %s", 
                      error_type.replace("_", " ").title(), game_id, username, str(e))
        
        # Get current game state to send back to client
        current_state = service.get_game(game_id)
        ws_resp = WebSocketResponse(
            type=WebSocketResponseType.GAME_STATE,
            data=current_state
        )
        redis_message = RedisGameUpdate(
            game_id=game_id,
            message=ws_resp.dict()
        )
        await redis_manager.publish(get_game_update_channel(game_id), redis_message.dict())

        return "error", {"status": "error", "message": str(e)}, str(e)