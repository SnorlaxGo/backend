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

# Create router instead of app
router = APIRouter(prefix="/api")  # Optional prefix

@router.on_event("startup")
async def startup_event():
    # Initialize Redis connections

    await manager.start()
    await challenge_manager.start()

@router.on_event("shutdown")
async def shutdown_event():
    # Close Redis connections
    await redis_manager.disconnect()

@router.websocket("/ws/game/{game_id}")
async def handle_game_socket(websocket: WebSocket, 
                             game_id: int,
                             token: str = Query(...),
                             db: Session = Depends(get_db)):
    print(f"token: {token.encode('utf-8')}")
    current_user = await get_current_user_ws(token.encode('utf-8'), db)
    # Send initial game state
    service = GameService(db)
    game = service.get_game(game_id)
    print(f"game.status: {game.status}")
    if not game or game.status != GameStatus.ACTIVE:
        raise HTTPException(status_code=400, detail="Game is not active")
    await manager.connect(websocket, game_id, current_user.id)
    try:

        try:
            from .schemas import WebSocketMessage, WebSocketMessageType
            message = WebSocketMessage(
                type=WebSocketMessageType.GAME_STATE,
                data=service.to_response(game)
            )
            await websocket.send_json(message.dict())
        finally:
            db.close()

        print(f"waiting for disconnect")
        await websocket.receive_text()  # Just wait for disconnect
    except WebSocketDisconnect:
        print(f"disconnecting from game {game_id} for player {current_user.id}", flush=True)
        manager.disconnect(websocket, game_id, current_user.id, db)
    except Exception as e:
        print(f"Error disconnecting from game {game_id} for player {current_user.id}: {e}", flush=True)

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
                
                print(f"challenge.status: {challenge.status}")
                if challenge.status == ChallengeStatus.MATCHED:
                    game = db.query(models.Game).filter(
                        (models.Game.black_player_id == challenge.challenger_id) |
                        (models.Game.white_player_id == challenge.challenger_id)
                    ).order_by(models.Game.id.desc()).first()
                    
                    print(f"game.black_player_id: {game.black_player_id}, name: {game.black_player.username}")
                    print(f"game.white_player_id: {game.white_player_id}, name: {game.white_player.username}")
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