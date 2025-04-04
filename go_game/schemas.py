from pydantic import BaseModel, Field, EmailStr, validator
from typing import List, Optional, List, Tuple, Dict, Any
from datetime import datetime
from enum import Enum
from .models import StoneColor, ChallengeStatus, GameStatus
import re

class UserInfoResponse(BaseModel):
    id: int
    username: str
    email: str
    is_anonymous: bool = False

class WebSocketResponseType(str, Enum):
    """
    Represents the types of messages that can be sent over WebSockets.
    This is used for client-server communication protocol.
    """
    GAME_ABANDONED = "game_abandoned"
    GAME_STATE = "game_state"
    MOVE = "move"
    ERROR = "error"
    TIMEOUT = "timeout"
    RESIGN = "resign"
    DRAW_OFFER = "draw_offer"
    DRAW_ACCEPTED = "draw_accepted"
    PLAYER_DISCONNECTED = "player_disconnected"
    PLAYER_RECONNECTED = "player_reconnected"
    PONG = "pong"
    GAME_OVER = "game_over"
    PASS = "pass"

class WebSocketRequestType(str, Enum):
    MOVE = "move"
    PING = "ping"
    RESIGN = "resign"
    OFFER_DRAW = "offer_draw"
    ACCEPT_DRAW = "accept_draw"

class WebSocketRequest(BaseModel):
    type: WebSocketRequestType
    data: Optional[Dict[str, Any]] = None

class PingData(BaseModel):
    move_number: int

class MoveData(BaseModel):
    x: int
    y: int
    
class WebSocketResponse(BaseModel):
    type: WebSocketResponseType
    data: Any

class PongResponse(WebSocketResponse):
    type: WebSocketResponseType = WebSocketResponseType.PONG
    
class GameStateResponse(BaseModel):
    success: bool = True
    board: List[List[int]]
    captured: List[Tuple[int, int]] = []
    black_captures: int
    white_captures: int
    black_time_used: Optional[int]
    white_time_used: Optional[int]
    color: StoneColor  # current turn color
    status: GameStatus
    move_number: int
    black_player_name: Optional[str] = None
    white_player_name: Optional[str] = None

    class Config:
        from_attributes = True


class PlayerBase(BaseModel):
    username: str
    email: str

class PlayerCreate(PlayerBase):
    password: str  # For registration, not stored in DB directly

class Player(PlayerBase):
    id: int
    
    class Config:
        from_attributes = True  # Allows conversion from SQLAlchemy models


class ChallengeBase(BaseModel):
    board_size: int = 19
    time_control: Optional[int] = None  # in minutes, None for no time limit

class DirectChallenge(ChallengeBase):
    challenged_user_id: int

class OpenChallenge(ChallengeBase):
    board_size: int = Field(alias="boardSize", default=19)
    time_control: Optional[int] = Field(alias="timeControl", default=None)

class AnonymousChallenge(BaseModel):
    board_size: int = 19
    time_control: Optional[int] = None

class ChallengeCreate(ChallengeBase):
    pass

class Challenge(ChallengeBase):
    id: int
    challenger_id: Optional[int]
    status: str
    created_at: datetime

    class Config:
        from_attributes = True 

class OpenChallengeResponse(BaseModel):
    challenge_id: int
    status: ChallengeStatus
    game_id: Optional[int] = None
    color: Optional[StoneColor] = None
    message: Optional[str] = None

class GameMatchResponse(BaseModel):
    game_id: int
    status: str
    challenge_id: int
    color: StoneColor

class GameMoveRequest(BaseModel):
    x: int
    y: int

class Token(BaseModel):
    access_token: str
    token_type: str

class GameMoveSuccessResponse(BaseModel):
    status: str
    board: list[list[int]] 


class GameType(Enum):
    REAL_TIME = "real_time"
    CORRESPONDENCE = "correspondence"

class MoveResponse(BaseModel):
    x: int
    y: int
    color: StoneColor
    move_number: int

class GameHistory(BaseModel):
    moves: List[MoveResponse]
    black_player_name: str
    white_player_name: str
    board_size: int
    game_id: int

class GameSummary(BaseModel):
    id: int
    opponent: str
    date: datetime
    result: str
    score: str
    board_size: int

class GameHistoryResponse(BaseModel):
    games: List[GameSummary]
    count: int

class DrawOfferRequest(BaseModel):
    game_id: int

class DrawOfferResponse(BaseModel):
    status: str
    message: str
    
class DrawAcceptResponse(BaseModel):
    status: str
    message: str 

# Connection event messages
class PlayerConnectionEvent(BaseModel):
    player_id: int
    game_id: int

class PlayerDisconnectedMessage(WebSocketResponse):
    type: WebSocketResponseType = WebSocketResponseType.PLAYER_DISCONNECTED
    data: PlayerConnectionEvent

class PlayerReconnectedMessage(WebSocketResponse):
    type: WebSocketResponseType = WebSocketResponseType.PLAYER_RECONNECTED
    data: PlayerConnectionEvent

# Redis message types
class RedisConnectionEvent(BaseModel):
    action: str
    game_id: int
    player_id: int
    message: Optional[Dict] = None

class RedisGameUpdate(BaseModel):
    game_id: int
    message: Dict
    target_id: Optional[int] = None

class TimeoutData(BaseModel):
    """Data for a timeout event"""
    timeout_player: StoneColor  # The player who timed out
    status: GameStatus     # The resulting game status
    game_id: int                # The game ID

class TimeoutMessage(WebSocketResponse):
    """Message sent when a player times out"""
    type: WebSocketResponseType = WebSocketResponseType.TIMEOUT
    data: TimeoutData

class UserCreate(BaseModel):
    username: str
    password: str
    email: EmailStr  # This validates email format
    
    @validator('username')
    def username_must_be_valid(cls, v):
        if not re.match(r'^[a-zA-Z0-9_-]{3,20}$', v):
            raise ValueError('Username must be 3-20 characters and contain only letters, numbers, underscores, and hyphens')
        return v
    
    @validator('password')
    def password_must_be_strong(cls, v):
        if len(v) < 8:
            raise ValueError('Password must be at least 8 characters')
        # Add more password strength checks as needed
        return v
    
    class Config:
        orm_mode = True

class PasswordResetRequest(BaseModel):
    email: EmailStr

class PasswordResetWithCode(BaseModel):
    email: EmailStr
    reset_code: str
    new_password: str

    @validator('new_password')
    def password_must_be_strong(cls, v):
        if len(v) < 8:
            raise ValueError('Password must be at least 8 characters')
        return v