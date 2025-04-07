from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
import asyncio

from .database import engine, get_db
from fastapi.security import OAuth2PasswordRequestForm
import go_game.models as models
from .game_logic import GameService, InvalidMoveError, KoViolationError, SuicideMoveError, MoveResultType, MoveResult
from .models import StoneColor, GameStatus, AuthProviderType
from .event_manager import redis_manager, get_game_update_channel, get_challenge_update_channel

from .schemas import (
    GameStateResponse,
    GameMoveRequest,
    GameMoveSuccessResponse,
    OpenChallengeResponse,
    DirectChallenge,
    OpenChallenge,
    AnonymousChallenge,
    GameHistoryResponse,
    GameSummary,
    WebSocketResponseType,
    WebSocketResponse,
    DrawOfferRequest,
    DrawOfferResponse,
    DrawAcceptResponse,
    UserInfoResponse,
    RedisGameUpdate,
    UserCreate,
    MoveResponse,
    GameHistory,
    PasswordResetRequest,
    PasswordResetWithCode,
    AppleLoginRequest,
    Token
    )
from .auth import (get_current_user,
                   authenticate_user, 
                   create_access_token, 
                   ACCESS_TOKEN_EXPIRE_MINUTES, 
                   create_refresh_token, 
                   validate_token, 
                   get_password_hash, 
                   verify_apple_token)
from .utils.board_visualizer import visualize_game
from datetime import timedelta, datetime
from .background_tasks import cleanup_stale_challenges, cleanup_stale_games
import traceback
import random
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from jwt import PyJWTError
from .loggers import api_logger as logger  # Import the logger
from .game_handlers import process_game_move
from .email_service import send_password_reset_code_email
import string

# Create router instead of app
router = APIRouter()

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
    return Token(username=user.username, access_token=access_token, refresh_token=refresh_token, token_type="bearer")

@router.post("/register", response_model=Token)
async def register(
    user_data: UserCreate,
    db: Session = Depends(get_db)
) -> Token:
    """Register a new user"""
    logger.info("Registration attempt for user: %s", user_data.username)
    
    # Check if username already exists
    if db.query(models.User).filter(models.User.username == user_data.username).first():
        logger.warning("Registration failed - username already exists: %s", user_data.username)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already registered"
        )
        
    # Check if email already exists
    if db.query(models.User).filter(models.User.email == user_data.email).first():
        logger.warning("Registration failed - email already exists: %s", user_data.email)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )

    # Create new user
    hashed_password = get_password_hash(user_data.password)
    db_user = models.User(
        username=user_data.username,
        email=user_data.email,
        hashed_password=hashed_password
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)

    # Generate tokens
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": db_user.username}, 
        expires_delta=access_token_expires
    )
    refresh_token = create_refresh_token(data={"sub": db_user.username})

    logger.info("Successfully registered user: %s", user_data.username)
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
        redis_message = RedisGameUpdate(
            game_id=new_game.id,
            message=response.dict(),
            source_id=None
        )
        await redis_manager.publish(get_challenge_update_channel(), redis_message.dict())
        
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
    redis_message = RedisGameUpdate(
        game_id=new_challenge.id,
        message=response.dict(),
        source_id=None
    )
    await redis_manager.publish(get_challenge_update_channel(), redis_message.dict())
    
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
    
    status, response_data, error_message = await process_game_move(
        game_id=game_id,
        x=move.x,
        y=move.y,
        user_id=current_user.id,
        username=current_user.username,
        db=db
    )
    
    if status == "error":
        raise HTTPException(status_code=400, detail=error_message)
    
    return response_data

@router.get('/game/{game_id}/history', response_model=GameHistory)
def get_game_history(game_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    # Check if the user is a player in this game
    print(current_user.id)
    game = db.query(models.Game).filter(models.Game.id == game_id, (models.Game.black_player_id == current_user.id) | (models.Game.white_player_id == current_user.id)).first()
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    
    moves = db.query(models.Move).filter(models.Move.game_id == game_id).all()
    move_list = []
    for move in moves:
        moves_response = MoveResponse(move_number=move.move_number, x=move.x, y=move.y, color=move.color)
        move_list.append(moves_response)

    return GameHistory(game_id=game.id, moves=move_list, black_player_name=game.black_player.username, white_player_name=game.white_player.username, board_size=game.board_size)

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
               
    message = WebSocketResponse(
        type=WebSocketResponseType.RESIGN,
        data=gs.to_response(game)
    )

    # Broadcast via Redis
    logger.debug("Broadcasting resignation for game %d via Redis", game_id)
    await redis_manager.publish(get_game_update_channel(game_id), {
        "game_id": game.id,
        "message": message.dict()
    })
    
    return message.dict()

@router.get("/games", response_model=GameHistoryResponse)
def get_games(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
    skip: int = 0,
    limit: int = 10
):
    # Find all games where the user is a player
    query = db.query(models.Game).filter(
        (models.Game.black_player_id == current_user.id) | 
        (models.Game.white_player_id == current_user.id),
        models.Game.status != GameStatus.ACTIVE
    ).order_by(models.Game.last_move_at.desc())
    
    # Get total count for pagination
    total_count = query.count()
    
    # Apply pagination
    games = query.offset(skip).limit(limit).all()
    
    # Convert games to response format
    games_response = []
    for game in games:
        # Determine opponent (the other player)
        opponent_id = game.white_player_id if game.black_player_id == current_user.id else game.black_player_id
        opponent = db.query(models.User).filter(models.User.id == opponent_id).first()
        
        # Format score string
        score = f"B+{game.black_points}" if game.black_points > game.white_points else f"W+{game.white_points}"
        
        result = "win" if game.black_points > game.white_points else "loss" if game.black_points < game.white_points else "draw"

        game_history_info = GameSummary(
            id=game.id,
            opponent=opponent.username,
            date=game.created_at,
            result=result,
            board_size=game.board_size,
            score=score
        )
        games_response.append(game_history_info)
        
    return GameHistoryResponse(games=games_response, count=total_count)

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
    message = WebSocketResponse(
        type=WebSocketResponseType.DRAW_OFFER,
        data=gs.to_response(game)
    )

    redis_message = RedisGameUpdate(
        game_id=game.id,
        message=message.dict(),
        target_id=game.white_player_id if current_user.id == game.black_player_id else game.black_player_id
    )
    
    await redis_manager.publish(get_game_update_channel(game_id), redis_message.dict())
    
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
    message = WebSocketResponse(
        type=WebSocketResponseType.DRAW_ACCEPTED,
        data=gs.to_response(game)
    )
    
    await redis_manager.publish(get_game_update_channel(game_id), {
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

import random
import string

@router.post("/forgot-password/request")
async def request_password_reset(
    request: PasswordResetRequest,
    db: Session = Depends(get_db)
):
    """Request a password reset code"""
    # Find the user by email
    user = db.query(models.User).filter(models.User.email == request.email).first()
    
    # Always return success even if email not found (security best practice)
    if not user:
        logger.info(f"Password reset requested for non-existent email: {request.email}")
        return {"status": "success", "message": "If your email is registered, you will receive a password reset code"}
    
    # Generate a more secure 8-character alphanumeric code
    reset_code = ''.join(random.choices(string.digits + string.ascii_uppercase, k=8))
    token_expiry = datetime.utcnow() + timedelta(hours=1)  # Shorter expiry for codes
    
    # Store the code in the database
    user.reset_token = reset_code
    user.reset_token_expires = token_expiry
    db.commit()
    
    # Send the email with the code
    await send_password_reset_code_email(user.email, reset_code)
    
    logger.info(f"Password reset code sent to: {user.email}")
    return {"status": "success", "message": "If your email is registered, you will receive a password reset code"}

@router.post("/forgot-password/reset")
async def verify_reset_code(
    request: PasswordResetWithCode,
    db: Session = Depends(get_db)
):
    """Verify reset code and set new password"""
    # Find user with this email and code
    user = db.query(models.User).filter(
        models.User.email == request.email,
        models.User.reset_token == request.reset_code
    ).first()
    
    # Check if code exists and is valid
    if not user or not user.reset_token_expires or user.reset_token_expires < datetime.utcnow():
        logger.warning(f"Invalid or expired password reset code used")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired code"
        )
    
    # Update the password
    user.hashed_password = get_password_hash(request.new_password)
    
    # Clear the reset code
    user.reset_token = None
    user.reset_token_expires = None
    
    db.commit()
    
    logger.info(f"Password successfully reset for user: {user.username}")
    return {"status": "success", "message": "Password has been reset successfully"}

@router.post("/auth/apple", response_model=Token)
async def login_with_apple(
    apple_data: AppleLoginRequest,
    db: Session = Depends(get_db)
) -> Token:
    """Login or register with Apple credentials"""
    logger.info("Apple login attempt")
    
    try:
        # Verify the Apple identity token
        is_valid, payload, error_message = verify_apple_token(apple_data.identity_token)
        
        if not is_valid:
            logger.warning(f"Apple token validation failed: {error_message}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid Apple token: {error_message}",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        # Extract user ID and email from the token payload
        apple_user_id = payload.get('sub')
        apple_email = payload.get('email')
        
        if not apple_user_id:
            logger.warning("No user ID found in Apple token")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid Apple token: missing user ID",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        if not apple_email:
            logger.warning("No email found in Apple token")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email not provided in Apple token",
            )
        
        # Check if this Apple ID is already linked to a user
        auth_provider = db.query(models.AuthProvider).filter(
            models.AuthProvider.provider == AuthProviderType.APPLE,
            models.AuthProvider.provider_user_id == apple_user_id
        ).first()
        
        if auth_provider:
            # User exists, retrieve and return tokens
            user = db.query(models.User).filter(models.User.id == auth_provider.user_id).first()
            logger.info("Existing Apple user found: %s", user.username)
            
            # Update provider email if it changed (Apple relay emails can change)
            if auth_provider.provider_email != apple_email:
                auth_provider.provider_email = apple_email
                db.commit()
        else:
            # Check if a user with this email already exists
            existing_user = db.query(models.User).filter(models.User.email == apple_email).first()
            
            if existing_user:
                # Link this Apple ID to the existing account
                logger.info(f"Linking Apple ID to existing account: {existing_user.username}")
                
                auth_provider = models.AuthProvider(
                    user_id=existing_user.id,
                    provider=AuthProviderType.APPLE,
                    provider_user_id=apple_user_id,
                    provider_email=apple_email
                )
                db.add(auth_provider)
                db.commit()
                
                user = existing_user
            else:
                # Create new user
                logger.info("Creating new user for Apple login")
                
                # Get name from token or use email as fallback
                name = payload.get('name', {})
                full_name = f"{name.get('firstName', '')} {name.get('lastName', '')}".strip()
                
                # Generate a unique username
                base_username = full_name.split()[0].lower() if full_name else apple_email.split('@')[0]
                username = base_username
                
                # Ensure username is unique
                counter = 1
                while db.query(models.User).filter(models.User.username == username).first():
                    username = f"{base_username}{counter}"
                    counter += 1
                
                # Create the user
                user = models.User(
                    username=username,
                    email=apple_email,
                    hashed_password=None,
                    role=models.UserRole.USER
                )
                db.add(user)
                db.commit()
                db.refresh(user)
                
                # Create the auth provider link
                auth_provider = models.AuthProvider(
                    user_id=user.id,
                    provider=AuthProviderType.APPLE,
                    provider_user_id=apple_user_id,
                    provider_email=apple_email
                )
                db.add(auth_provider)
                db.commit()
                
                logger.info("Created new user from Apple login: %s", username)
        
        # Generate tokens
        access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        access_token = create_access_token(
            data={"sub": user.username},
            expires_delta=access_token_expires
        )
        refresh_token = create_refresh_token(data={"sub": user.username})
        
        return Token(username=user.username, access_token=access_token, refresh_token=refresh_token, token_type="bearer")
        
    except Exception as e:
        logger.error(f"Apple login error: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication failed",
            headers={"WWW-Authenticate": "Bearer"},
        )