from fastapi import FastAPI, Depends, HTTPException, WebSocket, WebSocketDisconnect, status, Query
from sqlalchemy.orm import Session
import asyncio

from .database import engine, get_db
from fastapi.security import OAuth2PasswordRequestForm
import go_game.models as models
from .game_logic import GameService, InvalidMoveError, KoViolationError, SuicideMoveError
from .models import StoneColor, GameStatus, TimeControl
from .websocket_manager import manager, challenge_manager, WebSocketMessage, WebSocketMessageType

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
    DrawAcceptResponse
)
from .auth import get_current_user, get_current_user_ws, Token, authenticate_user, create_access_token, ACCESS_TOKEN_EXPIRE_MINUTES
from .utils.board_visualizer import visualize_game
from datetime import timedelta, datetime
from .background_tasks import cleanup_stale_challenges, cleanup_stale_games
import traceback
import random
app = FastAPI()

# Create the database tables
models.Base.metadata.create_all(bind=engine)

@app.get("/")
async def root():
    return {"message": "Welcome to the Go Game API"}

@app.post("/token")
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
) -> Token:
    user = authenticate_user(db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username}, expires_delta=access_token_expires
    )
    return Token(access_token=access_token, token_type="bearer")

@app.get("/games/{game_id}/visualize")
async def visualize_game_state(
    game_id: int,
    db: Session = Depends(get_db)
):
    game = db.query(models.Game).filter(models.Game.id == game_id).first()
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    
    visualization = visualize_game(game)
    return {"board": visualization}

@app.websocket("/ws/game/{game_id}")
async def handle_game_socket(websocket: WebSocket, 
                             game_id: int,
                             token: str = Query(...),
                             db: Session = Depends(get_db)):
    print(f"token: {token.encode('utf-8')}")
    current_user = await get_current_user_ws(token.encode('utf-8'), db)
    
    await manager.connect(websocket, game_id, current_user.id)
    try:
        # Send initial game state
        service = GameService(db)
        try:
            game = service.get_game(game_id)
            if game:
                message = WebSocketMessage(
                    type=WebSocketMessageType.GAME_STATE,
                    data=service.to_response(game)
                )
                await websocket.send_json(message.dict())
        finally:
            db.close()
            
        await websocket.receive_text()  # Just wait for disconnect
    except WebSocketDisconnect:
        manager.disconnect(websocket, game_id, current_user.id, db)

@app.post("/challenge/direct")
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

@app.post("/challenge/open")
def create_open_challenge(challenge: OpenChallenge,
                          current_user: models.User = Depends(get_current_user),
                          db: Session = Depends(get_db)):
    # First, check for matching open challenges
    print(f"Creating open challenge for user {current_user.id} with board size {challenge.board_size} and time control {challenge.time_control}")
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
        return OpenChallengeResponse(
            challenge_id=matching_challenge.id,
            status="matched",
            game_id=new_game.id,
            color=StoneColor.WHITE if white_player_id == current_user.id else StoneColor.BLACK
        )
    
    # If no match, create new open challenge
    new_challenge = models.Challenge(
        challenger_id=current_user.id,
        board_size=challenge.board_size,
        time_control=challenge.time_control,
        status="open"
    )
    db.add(new_challenge)
    db.commit()
    return OpenChallengeResponse(
        challenge_id=new_challenge.id,
        status="waiting"
    )

@app.post("/challenge/{challenge_id}/accept")
def accept_challenge(challenge_id: int,
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
    return {"game_id": new_game.id, "status": "game_created"}

# Add this new function for testing purposes
@app.post("/anonymous/challenge")
async def create_anonymous_challenge(challenge: AnonymousChallenge, db: Session = Depends(get_db)):
    """Create or accept an anonymous challenge"""
    # First, check for matching open anonymous challenges
    matching_challenge = db.query(models.Challenge).filter(
        models.Challenge.status == "open",
        models.Challenge.board_size == challenge.board_size,
        models.Challenge.time_control == challenge.time_control,
        models.Challenge.is_anonymous == True  # Add this field to Challenge model
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
        
        return {
            "game_id": new_game.id,
            "status": "matched",
            "player_id": anon_player.id,
            "color": "white"
        }
    
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
    
    return {
        "challenge_id": new_challenge.id,
        "status": "waiting",
        "player_id": anon_player.id,
        "color": "black"
    }

@app.websocket("/ws/challenge/{challenge_id}")
async def challenge_status(websocket: WebSocket, challenge_id: int):
    await challenge_manager.connect(websocket, f"challenge_{challenge_id}")
    start_time = datetime.now()
    CHALLENGE_TIMEOUT = 10  # seconds
    
    try:
        while True:
            # Create a new session for each check
            db = next(get_db())
            try:
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

@app.post("/game/{game_id}/move")
async def make_game_move(
    game_id: int,
    move: GameMoveRequest,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> GameMoveSuccessResponse:
    try:
        service = GameService(db)
        result = service.make_move(game_id, move.x, move.y, current_user.id)
        # Broadcast the move to all connected clients for this game
        await manager.broadcast_to_game(game_id, WebSocketMessage(
            type=WebSocketMessageType.GAME_STATE,
            data=result
        ).dict())
        
        return {"status": "success"}
        
    except InvalidMoveError as e:
        print(f"Invalid move: {e}")
        print(traceback.format_exc())
        raise HTTPException(status_code=400, detail=str(e))
    except KoViolationError as e:
        print(f"Ko violation: {e}")
        print(traceback.format_exc())
        raise HTTPException(status_code=400, detail=str(e))
    except SuicideMoveError as e:
        print(f"Suicide move: {e}")
        print(traceback.format_exc())
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        print(f"Error making move: {e}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/game/{game_id}/state")
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

@app.post("/game/{game_id}/resign")
async def resign_game(
    game_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    gs = GameService(db)
    game = gs.get_game(game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    
    # Verify the user is a player in this game
    if current_user.id not in [game.black_player_id, game.white_player_id]:
        raise HTTPException(status_code=403, detail="Not a player in this game")
    is_black_player = current_user.id == game.black_player_id
    game.status = GameStatus.BLACK_WON_RESIGNATION if not is_black_player else GameStatus.WHITE_WON_RESIGNATION
    game.resigned = True
    
    db.commit()
    message = WebSocketMessage(
        type=WebSocketMessageType.RESIGN,
        data=gs.to_response(game)
    )

    await manager.broadcast_to_game(game.id, message.dict())
    return message.dict()

@app.get("/games/active", response_model=ActiveGamesResponse)
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

@app.post("/game/{game_id}/offer_draw")
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
    
    # Notify the other player via websocket
    gs = GameService(db)
    message = WebSocketMessage(
        type=WebSocketMessageType.DRAW_OFFER,
        data=gs.to_response(game)
    )
    await manager.broadcast_to_game(game_id, message.dict())
    
    return DrawOfferResponse(
        status="success",
        message="Draw offer sent"
    )

@app.post("/game/{game_id}/accept_draw")
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
    
    # Notify both players via websocket
    gs = GameService(db)
    message = WebSocketMessage(
        type=WebSocketMessageType.DRAW_ACCEPTED,
        data=gs.to_response(game)
    )
    await manager.broadcast_to_game(game_id, message.dict())
    
    return DrawAcceptResponse(
        status="success",
        message="Draw accepted, game ended in a draw"
    )

@app.on_event("startup")
async def start_cleanup_task():
    asyncio.create_task(cleanup_stale_challenges())
    asyncio.create_task(cleanup_stale_games())