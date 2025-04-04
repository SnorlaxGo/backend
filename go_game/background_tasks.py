from datetime import datetime, timedelta
import asyncio
from .database import get_db
from . import models
from fastapi import BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import func
from sqlalchemy.types import Integer
from .models import Game, GameStatus, TimeControl, StoneColor
from .websocket_manager import manager
from .schemas import WebSocketResponse, WebSocketResponseType
import traceback
from .event_manager import get_game_update_channel
from .logging_config import logger
from sqlalchemy import case

async def cleanup_stale_challenges():
    while True:

        try:
            db = next(get_db())
            try:
                # Delete challenges older than 10 seconds
                cutoff_time = datetime.now() - timedelta(seconds=10)
                stale_challenges = db.query(models.Challenge).filter(
                    models.Challenge.created_at < cutoff_time,
                    models.Challenge.status == "open"
                ).all()
                
                for challenge in stale_challenges:
                    db.delete(challenge)
                
                db.commit()
            finally:
                db.close()
        except Exception as e:
            print(f"Error in cleanup task: {e}")  # Consider proper logging
            
        await asyncio.sleep(30)  # Run cleanup every 30 seconds 

async def cleanup_timeout_games(db: Session):
    now = datetime.utcnow()
    # Handle correspondence games
    correspondence_games = db.query(Game).filter(
        Game.status == GameStatus.ACTIVE,
        Game.time_control.is_(TimeControl.CORRESPONDENCE),  # No time control = correspondence
        Game.last_move_at < now - timedelta(seconds=TimeControl.CORRESPONDENCE)  # No move for 7 days
    ).all()    

    for game in correspondence_games:
        # Determine who abandoned (whose turn it was)
        abandoned_by_black = game.is_black_turn
        winner_id = game.white_player_id if abandoned_by_black else game.black_player_id
        
        game.status = GameStatus.BLACK_WON_TIMEOUT if abandoned_by_black else GameStatus.WHITE_WON_TIMEOUT
        game.winner_id = winner_id

    db.commit() 

async def cleanup_stale_games():
    while True:
        try:
            db = next(get_db())
            try:
                now = datetime.utcnow()
                
                # Find active games
                time_in_seconds = case(
                    (models.Game.time_control == TimeControl.BLITZ, TimeControl.BLITZ.value),  # 5 minutes
                    (models.Game.time_control == TimeControl.RAPID, TimeControl.RAPID.value),  # 10 minutes
                    (models.Game.time_control == TimeControl.NORMAL, TimeControl.NORMAL.value),  # 30 minutes
                    else_=604800  # 7 days for correspondence
                )
                active_games = db.query(models.Game).filter(
                    models.Game.status == GameStatus.ACTIVE,
                    models.Game.created_at < now - timedelta(seconds=time_in_seconds)
                ).all()

                logger.debug(f"Found {len(active_games)} active games")

                for game in active_games:
                    time_since_creation = now - game.created_at
                    time_since_last_move = now - game.last_move_at if game.last_move_at else time_since_creation
                    logger.info(
                        f"Deactivating game {game.id}: created {time_since_creation.total_seconds()/3600:.2f} hours ago, "
                        f"last move {time_since_last_move.total_seconds()/3600:.1f} hours ago, "
                        f"time control: {game.time_control} ({time_in_seconds} seconds)"
                    )
                    # Get time elapsed since last move
                    time_since_move = (now - game.last_move_at).total_seconds()
                    
                    # Check if current player has run out of time
                    is_black_turn = game.is_black_turn
                    time_remaining = game.black_time_remaining if is_black_turn else game.white_time_remaining
                    if time_remaining is None:
                        # Delete game if time_remaining is None (old game format)
                        db.delete(game)
                        continue
                    if time_since_move > time_remaining:
                        game.status = GameStatus.WHITE_WON_TIMEOUT if is_black_turn else GameStatus.BLACK_WON_TIMEOUT
                        game.winner_id = game.white_player_id if is_black_turn else game.black_player_id
                        game.last_move_at = now
                        
                        try:
                            message = WebSocketResponse(
                                type=WebSocketResponseType.TIMEOUT,
                                data={
                                    "timeout_player": StoneColor.BLACK if is_black_turn else StoneColor.WHITE,
                                    "status": game.status
                                }
                            )
                            print(f"Sending timeout message for game {game.id}: {message.dict()}")  # Debug print
                            await manager.broadcast_to_game(get_game_update_channel(game.id), message.dict())
                            await manager.close_game_connections(game.id)
                        except Exception as e:
                            print(f"Error notifying clients for game {game.id}: {str(e)}")
                            print(f"Message was: {message.dict() if 'message' in locals() else 'not created'}")
                            print(f"Traceback: {traceback.format_exc()}")

                db.commit()
            finally:
                db.close()
        except Exception as e:
            logger.debug(f"Error in stale games cleanup task: {e}")

        await asyncio.sleep(60)  # Run cleanup every minute