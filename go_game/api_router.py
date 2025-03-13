from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, Query, HTTPException, status
from sqlalchemy.orm import Session
import asyncio
import json

from .database import engine, get_db
from fastapi.security import OAuth2PasswordRequestForm
import go_game.models as models
from .game_logic import GameService, InvalidMoveError, KoViolationError, SuicideMoveError
from .models import StoneColor, GameStatus, TimeControl
from .websocket_manager import redis_manager

from .schemas import (
    GameStateResponse,
    GameMoveRequest,
    Token,
    GameMoveSuccessResponse,
    OpenChallengeResponse,
    DirectChallenge,
    OpenChallenge,
    AnonymousChallenge,
    ChallengeStatus,
    ActiveGameInfo,
    ActiveGamesResponse,
    WebSocketMessageType,
    WebSocketMessage,
    DrawOfferRequest,
    DrawOfferResponse,
    DrawAcceptResponse,
    UserInfoResponse
)
from .auth import get_current_user, Token, authenticate_user, create_access_token, ACCESS_TOKEN_EXPIRE_MINUTES, create_refresh_token, validate_token
from .utils.board_visualizer import visualize_game
from datetime import timedelta, datetime
from .background_tasks import cleanup_stale_challenges, cleanup_stale_games
import traceback
import random
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from jwt import PyJWTError
from .loggers import api_logger as logger  # Import the logger

# Create router instead of app
router = APIRouter()

# Create the database tables
models.Base.metadata.create_all(bind=engine)

@router.on_event("startup")
async def startup_event():
    logger.info("Starting API router")
    # Initialize Redis connection
    await redis_manager.connect()
    # Start background tasks
    logger.info("Starting background tasks")
    asyncio.create_task(cleanup_stale_challenges())
    #asyncio.create_task(cleanup_stale_games())
    logger.info("API router startup complete")

@router.on_event("shutdown")
async def shutdown_event():
    logger.info("Shutting down API router")
    # Close Redis connection
    await redis_manager.disconnect()
    logger.info("API router shutdown complete")

@router.get("/")
async def root():
    logger.debug("Root endpoint accessed")
    return {"message": "Welcome to the Go Game API"}

@router.post("/token")
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
) -> Token:
    logger.info("Login attempt for user: %s", form_data.username)
    user = authenticate_user(db, form_data.username, form_data.password)
    if not user:
        logger.warning("Failed login attempt for user: %s", form_data.username)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username}, expires_delta=access_token_expires
    )
    refresh_token = create_refresh_token(data={"sub": user.username})
    logger.info("Successful login for user: %s", user.username)
    return Token(access_token=access_token, refresh_token=refresh_token, token_type="bearer")

@router.get("/me", response_model=UserInfoResponse)
async def get_current_user_info(
    current_user: models.User = Depends(get_current_user)
):
    """Get information about the currently authenticated user"""
    return UserInfoResponse(
        id=current_user.id,
        username=current_user.username,
        email=current_user.email,
        is_anonymous=current_user.is_anonymous if hasattr(current_user, 'is_anonymous') else False
    )

@router.get("/games/{game_id}/visualize")
async def visualize_game_state(
    game_id: int,
    db: Session = Depends(get_db)
):
    game = db.query(models.Game).filter(models.Game.id == game_id).first()
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    
    visualization = visualize_game(game)
    return {"board": visualization}

@router.post("/challenge/direct")
def create_direct_challenge(challenge: DirectChallenge,
                            current_user: models.User = Depends(get_current_user),
                            db: Session = Depends(get_db)):
    # Create a new game with pending status
    new_game = models.Game(
        challenger_id=current_user.id,  # You'll need to implement user authentication
        challenged_id=challenge.challenged_user_id,
        board_size=challenge.board_size,
        time_control=challenge.time_control,
        status="pending"
    )
    db.add(new_game)
    db.commit()
    db.refresh(new_game)
    return {"game_id": new_game.id, "status": "challenge_sent"}

@router.post("/challenge/open")
async def create_open_challenge(challenge: OpenChallenge,
                          current_user: models.User = Depends(get_current_user),
                          db: Session = Depends(get_db)):
    # First, check for matching open challenges
    logger.info("Creating open challenge for user %d with board size %d and time control %d", 
                current_user.id, challenge.board_size, challenge.time_control)
    matching_challenge = db.query(models.Challenge).filter(
        models.Challenge.status == "open",
        models.Challenge.board_size == challenge.board_size,
        models.Challenge.time_control == challenge.time_control,
        models.Challenge.challenger_id != current_user.id  # Don't match with self
    ).first()
    
    if matching_challenge:
        # Create a new game with the matched players
        # Randomly assign black and white players
        white_player_id = matching_challenge.challenger_id if random.choice([True, False]) else current_user.id
        black_player_id = current_user.id if white_player_id == matching_challenge.challenger_id else matching_challenge.challenger_id
        new_game = models.Game(
            black_player_id=black_player_id,
            white_player_id=white_player_id,
            board_size=challenge.board_size,
            time_control=challenge.time_control
        )
        db.add(new_game)
        matching_challenge.status = "matched"
        db.commit()
        db.refresh(new_game)
        
        # Notify via Redis about the match
        response = OpenChallengeResponse(
            challenge_id=matching_challenge.id,
            status="matched",
            game_id=new_game.id,
            color=StoneColor.WHITE if white_player_id == current_user.id else StoneColor.BLACK
        )
        await redis_manager.publish("challenge_updates", {
            "challenge_id": matching_challenge.id,
            "data": response.dict()
        })
        
        return response
    
    # If no match, create new open challenge
    new_challenge = models.Challenge(
        challenger_id=current_user.id,
        board_size=challenge.board_size,
        time_control=challenge.time_control,
        status="open"
    )
    db.add(new_challenge)
    db.commit()
    
    response = OpenChallengeResponse(
        challenge_id=new_challenge.id,
        status="waiting"
    )
    
    # Notify via Redis about the new challenge
    await redis_manager.publish("challenge_updates", {
        "challenge_id": new_challenge.id,
        "data": response.dict()
    })
    
    return response

@router.post("/challenge/{challenge_id}/accept")
async def accept_challenge(challenge_id: int,
                    current_user: models.User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    challenge = db.query(models.Challenge).filter(models.Challenge.id == challenge_id).first()
    if not challenge:
        raise HTTPException(status_code=404, detail="Challenge not found")
    
    if challenge.status != "open":
        raise HTTPException(status_code=400, detail="Challenge is not pending")
    
    # Create new game and update challenge status
    new_game = models.Game(
        black_player_id=challenge.challenger_id,
        white_player_id=current_user.id,
        board_size=challenge.board_size,
        time_control=challenge.time_control,
    )
    db.add(new_game)
    challenge.status = "accepted"
    db.commit()
    db.refresh(new_game)
    
    # Notify via Redis about the acceptance
    response = {
        "game_id": new_game.id,
        "status": "game_created",
        "challenge_id": challenge_id
    }
    await redis_manager.publish("challenge_updates", {
        "challenge_id": challenge_id,
        "data": response
    })
    
    return response

@router.post("/anonymous/challenge")
async def create_anonymous_challenge(challenge: AnonymousChallenge, db: Session = Depends(get_db)):
    """Create or accept an anonymous challenge"""
    # First, check for matching open anonymous challenges
    matching_challenge = db.query(models.Challenge).filter(
        models.Challenge.status == "open",
        models.Challenge.board_size == challenge.board_size,
        models.Challenge.time_control == challenge.time_control,
        models.Challenge.is_anonymous == True
    ).first()
    
    if matching_challenge:
        # Create anonymous player for challenger
        anon_player = models.User(
            username=f"anonymous_{datetime.now().strftime('%Y%m%d%H%M%S%f')}",
            email=f"anon_{datetime.now().strftime('%Y%m%d%H%M%S%f')}@temp.com",
            is_anonymous=True
        )
        db.add(anon_player)
        db.commit()
        db.refresh(anon_player)

        # Create a new game with the matched players
        new_game = models.Game(
            black_player_id=matching_challenge.challenger_id,
            white_player_id=anon_player.id,
            board_size=challenge.board_size,
            time_control=challenge.time_control
        )
        db.add(new_game)
        matching_challenge.status = "matched"
        db.commit()
        db.refresh(new_game)
        
        response = {
            "game_id": new_game.id,
            "status": "matched",
            "player_id": anon_player.id,
            "color": "white",
            "challenge_id": matching_challenge.id
        }
        
        # Notify via Redis about the match
        await redis_manager.publish("challenge_updates", {
            "challenge_id": matching_challenge.id,
            "data": response
        })
        
        return response
    
    # If no match, create anonymous player and new open challenge
    anon_player = models.User(
        username=f"anonymous_{datetime.now().strftime('%Y%m%d%H%M%S%f')}",
        email=f"anon_{datetime.now().strftime('%Y%m%d%H%M%S%f')}@temp.com",
        is_anonymous=True
    )
    db.add(anon_player)
    db.commit()
    db.refresh(anon_player)

    # Create new open challenge
    new_challenge = models.Challenge(
        challenger_id=anon_player.id,
        board_size=challenge.board_size,
        time_control=challenge.time_control,
        status="open",
        is_anonymous=True
    )
    db.add(new_challenge)
    db.commit()
    
    response = {
        "challenge_id": new_challenge.id,
        "status": "waiting",
        "player_id": anon_player.id,
        "color": "black"
    }
    
    # Notify via Redis about the new challenge
    await redis_manager.publish("challenge_updates", {
        "challenge_id": new_challenge.id,
        "data": response
    })
    
    return response

@router.post("/game/{game_id}/move")
async def make_game_move(
    game_id: int,
    move: GameMoveRequest,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> GameMoveSuccessResponse:
    logger.info("Move request for game %d by user %s: (%d, %d)", 
               game_id, current_user.username, move.x, move.y)
    try:
        service = GameService(db)
        result = service.make_move(game_id, move.x, move.y, current_user.id)
        
        # Broadcast the move via Redis
        logger.debug("Broadcasting move for game %d via Redis", game_id)
        await redis_manager.publish("game_updates", {
            "game_id": game_id,
            "message": WebSocketMessage(
                type=WebSocketMessageType.GAME_STATE,
                data=result
            ).dict()
        })
        
        logger.info("Move successful for game %d by user %s", game_id, current_user.username)
        return {"status": "success"}
        
    except InvalidMoveError as e:
        logger.warning("Invalid move in game %d by user %s: %s", 
                      game_id, current_user.username, str(e))
        raise HTTPException(status_code=400, detail=str(e))
    except KoViolationError as e:
        logger.warning("Ko violation in game %d by user %s: %s", 
                      game_id, current_user.username, str(e))
        raise HTTPException(status_code=400, detail=str(e))
    except SuicideMoveError as e:
        logger.warning("Suicide move in game %d by user %s: %s", 
                      game_id, current_user.username, str(e))
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Error making move in game %d by user %s: %s", 
                    game_id, current_user.username, str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/game/{game_id}/state")
def get_current_game_state(
    game_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> GameStateResponse:
    gs = GameService(db)
    game = gs.get_game(game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    
    # Verify the user is a player in this game
    if current_user.id not in [game.black_player_id, game.white_player_id]:
        raise HTTPException(status_code=403, detail="Not a player in this game")
    game_state = gs.to_response(game)
    
    # Add player names to the response
    game_state.black_player_name = game.black_player.username
    game_state.white_player_name = game.white_player.username
    
    return game_state

@router.post("/game/{game_id}/resign")
async def resign_game(
    game_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    logger.info("Resignation request for game %d by user %s", game_id, current_user.username)
    gs = GameService(db)
    game = gs.get_game(game_id)
    if not game:
        logger.warning("Resignation failed: Game %d not found", game_id)
        raise HTTPException(status_code=404, detail="Game not found")
    
    # Verify the user is a player in this game
    if current_user.id not in [game.black_player_id, game.white_player_id]:
        logger.warning("Unauthorized resignation attempt for game %d by user %s", 
                      game_id, current_user.username)
        raise HTTPException(status_code=403, detail="Not a player in this game")
        
    is_black_player = current_user.id == game.black_player_id
    game.status = GameStatus.BLACK_WON_RESIGNATION if not is_black_player else GameStatus.WHITE_WON_RESIGNATION
    game.resigned = True
    
    db.commit()
    logger.info("Game %d: %s player resigned", 
               game_id, "Black" if is_black_player else "White")
               
    message = WebSocketMessage(
        type=WebSocketMessageType.RESIGN,
        data=gs.to_response(game)
    )

    # Broadcast via Redis
    logger.debug("Broadcasting resignation for game %d via Redis", game_id)
    await redis_manager.publish("game_updates", {
        "game_id": game.id,
        "message": message.dict()
    })
    
    return message.dict()

@router.get("/games/active", response_model=ActiveGamesResponse)
def get_active_games(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    # Find all active games where the user is a player
    active_games = db.query(models.Game).filter(
        (models.Game.black_player_id == current_user.id) | 
        (models.Game.white_player_id == current_user.id),
        models.Game.status == GameStatus.ACTIVE
    ).order_by(models.Game.last_move_at.desc()).all()
    
    game_info_list = []
    for game in active_games:
        is_black = game.black_player_id == current_user.id
        opponent = game.white_player if is_black else game.black_player
        
        # Determine if it's the user's turn
        your_turn = game.is_black_turn == is_black
        
        game_info = ActiveGameInfo(
            game_id=game.id,
            opponent_name=opponent.username,
            color=StoneColor.BLACK if is_black else StoneColor.WHITE,
            board_size=game.board_size,
            time_control=game.time_control,
            black_time_used=game.black_time_remaining,
            white_time_used=game.white_time_remaining,
            last_move_at=game.last_move_at,
            game_type="real_time" if game.time_control != TimeControl.CORRESPONDENCE else "correspondence",
            your_turn=your_turn
        )
        game_info_list.append(game_info)
    
    return ActiveGamesResponse(
        games=game_info_list,
        count=len(game_info_list)
    )

@router.post("/game/{game_id}/offer_draw")
async def offer_draw(
    game_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> DrawOfferResponse:
    game = db.query(models.Game).filter(models.Game.id == game_id).first()
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    
    # Verify the user is a player in this game
    if current_user.id not in [game.black_player_id, game.white_player_id]:
        raise HTTPException(status_code=403, detail="Not a player in this game")
    
    # Offer the draw
    result = game.offer_draw(current_user.id)
    db.commit()
    
    # Notify the other player via Redis
    gs = GameService(db)
    message = WebSocketMessage(
        type=WebSocketMessageType.DRAW_OFFER,
        data=gs.to_response(game)
    )
    
    await redis_manager.publish("game_updates", {
        "game_id": game_id,
        "message": message.dict()
    })
    
    return DrawOfferResponse(
        status="success",
        message="Draw offer sent"
    )

@router.post("/game/{game_id}/accept_draw")
async def accept_draw(
    game_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> DrawAcceptResponse:
    game = db.query(models.Game).filter(models.Game.id == game_id).first()
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    
    # Verify the user is a player in this game
    if current_user.id not in [game.black_player_id, game.white_player_id]:
        raise HTTPException(status_code=403, detail="Not a player in this game")
    
    # Accept the draw
    result = game.accept_draw(current_user.id)
    if not result:
        raise HTTPException(status_code=400, detail="No draw offer to accept")
    
    db.commit()
    
    # Notify both players via Redis
    gs = GameService(db)
    message = WebSocketMessage(
        type=WebSocketMessageType.DRAW_ACCEPTED,
        data=gs.to_response(game)
    )
    
    await redis_manager.publish("game_updates", {
        "game_id": game_id,
        "message": message.dict()
    })
    
    return DrawAcceptResponse(
        status="success",
        message="Draw accepted, game ended in a draw"
    )

class RefreshTokenRequest(BaseModel):
    refresh_token: str

@router.post("/token/refresh")
async def refresh_token(
    request: RefreshTokenRequest,
    db: Session = Depends(get_db)
) -> Token:
    try:
        # Validate the refresh token
        payload = validate_token(request.refresh_token)
        
        # Check if it's actually a refresh token
        if payload["type"] != "refresh":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token type",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        username = payload["username"]
        
        # Get the user
        user = db.query(models.User).filter(models.User.username == username).first()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        # Create new access token
        access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        access_token = create_access_token(
            data={"sub": user.username}, expires_delta=access_token_expires
        )
        
        # Create new refresh token
        refresh_token = create_refresh_token(data={"sub": user.username})
        
        return Token(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="bearer"
        )
    except PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )
