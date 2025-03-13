import asyncio
import uvicorn
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, Query, HTTPException
from sqlalchemy.orm import Session
import redis.asyncio as redis
import json
from datetime import datetime

from .database import get_db
from .auth import get_current_user_ws
from .websocket_manager import manager, challenge_manager, redis_manager
from .game_logic import GameService
from .models import GameStatus
from . import models
from .loggers import api_logger as logger  # Import the logger

# Create router instead of app
router = APIRouter(prefix="/api")  # Optional prefix

@router.on_event("startup")
async def startup_event():
    logger.info("Starting WebSocket router")
    # Initialize Redis connections
    await manager.start()
    await challenge_manager.start()
    logger.info("WebSocket router startup complete")

@router.on_event("shutdown")
async def shutdown_event():
    logger.info("Shutting down WebSocket router")
    # Close Redis connections
    await redis_manager.disconnect()
    logger.info("WebSocket router shutdown complete")

@router.websocket("/ws/game/{game_id}")
async def handle_game_socket(websocket: WebSocket, 
                             game_id: int,
                             token: str = Query(...),
                             db: Session = Depends(get_db)):
    try:
        logger.debug("WebSocket connection attempt for game %d", game_id)
        current_user = await get_current_user_ws(token.encode('utf-8'), db)
        logger.info("WebSocket connection for game %d by user %s", game_id, current_user.username)
        
        # Send initial game state
        service = GameService(db)
        game = service.get_game(game_id)
        logger.debug("Game %d status: %s", game_id, game.status)
        
        if not game or game.status != GameStatus.ACTIVE:
            logger.warning("WebSocket connection rejected: Game %d is not active", game_id)
            raise HTTPException(status_code=400, detail="Game is not active")
            
        await manager.connect(websocket, game_id, current_user.id)
        logger.info("WebSocket connected for game %d, user %s", game_id, current_user.username)
        
        try:
            from .schemas import WebSocketMessage, WebSocketMessageType
            message = WebSocketMessage(
                type=WebSocketMessageType.GAME_STATE,
                data=service.to_response(game)
            )
            await websocket.send_json(message.dict())
            logger.debug("Initial game state sent for game %d", game_id)
        finally:
            db.close()

        logger.debug("Waiting for disconnect from game %d", game_id)
        await websocket.receive_text()  # Just wait for disconnect
        
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected from game %d for user %s", 
                   game_id, current_user.username)
        manager.disconnect(websocket, game_id, current_user.id, db)
    except Exception as e:
        logger.error("Error in WebSocket connection for game %d: %s", 
                    game_id, str(e), exc_info=True)

@router.websocket("/ws/challenge/{challenge_id}")
async def challenge_status(websocket: WebSocket, challenge_id: int):
    await challenge_manager.connect(websocket, f"challenge_{challenge_id}")
    start_time = datetime.now()
    CHALLENGE_TIMEOUT = 10  # seconds
    
    try:
        while True:
            # Create a new session for each check
            db = next(get_db())
            try:
                from .schemas import OpenChallengeResponse, ChallengeStatus, StoneColor
                challenge = db.query(models.Challenge).filter(models.Challenge.id == challenge_id).first()
                
                if not challenge:
                    await websocket.send_json(
                        OpenChallengeResponse(
                            challenge_id=challenge_id,
                            status="error",
                            message="Challenge not found"
                        ).dict()
                    )
                    break
                
                # Check if challenge has timed out
                if (datetime.now() - start_time).seconds >= CHALLENGE_TIMEOUT:
                    db.delete(challenge)
                    db.commit()
                    await websocket.send_json(
                        OpenChallengeResponse(
                            challenge_id=challenge_id,
                            status=ChallengeStatus.EXPIRED
                        ).dict()
                    )
                    break
                
                logger.debug(f"challenge.status: {challenge.status}")
                if challenge.status == ChallengeStatus.MATCHED:
                    game = db.query(models.Game).filter(
                        (models.Game.black_player_id == challenge.challenger_id) |
                        (models.Game.white_player_id == challenge.challenger_id)
                    ).order_by(models.Game.id.desc()).first()
                    
                    logger.debug(f"game.black_player_id: {game.black_player_id}, name: {game.black_player.username}")
                    logger.debug(f"game.white_player_id: {game.white_player_id}, name: {game.white_player.username}")
                    await websocket.send_json(
                        OpenChallengeResponse(
                            challenge_id=challenge_id,
                            status=ChallengeStatus.MATCHED,
                            game_id=game.id,
                            color=StoneColor.BLACK if game.black_player_id == challenge.challenger_id else StoneColor.WHITE
                        ).dict()
                    )
                    break
                else:
                    await websocket.send_json(
                        OpenChallengeResponse(
                            challenge_id=challenge_id,
                            status=ChallengeStatus.WAITING
                        ).dict()
                    )

            finally:
                db.close()
            
            await asyncio.sleep(1)
            
    except WebSocketDisconnect:
        # Cleanup on websocket disconnect
        db = next(get_db())
        try:
            challenge = db.query(models.Challenge).filter(models.Challenge.id == challenge_id).first()
            if challenge and challenge.status == "open":
                db.delete(challenge)
                db.commit()
        finally:
            db.close()
        challenge_manager.disconnect(websocket, f"challenge_{challenge_id}")