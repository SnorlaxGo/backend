from pydantic import BaseModel, Field, EmailStr, validator
from typing import List, Optional, Literal, List, Tuple, Dict, Any, Union
from datetime import datetime
from enum import Enum
from .models import StoneColor, ChallengeStatus, GameStatus
import re

class UserInfoResponse(BaseModel):
    id: int
    username: str
    email: str
    is_anonymous: bool = False

class WebSocketMessageType(str, Enum):
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
    black_player_name: Optional[str] = None
    white_player_name: Optional[str] = None

    class Config:
        from_attributes = True

class WebSocketMessage(BaseModel):
    type: WebSocketMessageType
    data: Any

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

class MoveResponse(BaseModel):
    success: bool
    board: list[list[int]]
    captured: list[tuple[int, int]]
    black_captures: int
    white_captures: int
    black_time_used: Optional[int] = None
    white_time_used: Optional[int] = None

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

class ActiveGameInfo(BaseModel):
    game_id: int
    opponent_name: str
    color: StoneColor
    board_size: int
    time_control: Optional[int] = None  # None for correspondence games
    black_time_used: Optional[int] = None
    white_time_used: Optional[int] = None
    last_move_at: datetime
    game_type: GameType
    your_turn: bool

class ActiveGamesResponse(BaseModel):
    games: List[ActiveGameInfo]
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

class PlayerDisconnectedMessage(WebSocketMessage):
    type: WebSocketMessageType = WebSocketMessageType.PLAYER_DISCONNECTED
    data: PlayerConnectionEvent

class PlayerReconnectedMessage(WebSocketMessage):
    type: WebSocketMessageType = WebSocketMessageType.PLAYER_RECONNECTED
    data: PlayerConnectionEvent

# Redis message types
class RedisConnectionEvent(BaseModel):
    action: str
    game_id: int
    player_id: int
    message: Optional[Dict] = None
    source_id: Optional[int] = None

class RedisGameUpdate(BaseModel):
    game_id: int
    message: Dict
    source_id: Optional[int] = None

class TimeoutData(BaseModel):
    """Data for a timeout event"""
    timeout_player: StoneColor  # The player who timed out
    status: GameStatus     # The resulting game status
    game_id: int                # The game ID

class TimeoutMessage(WebSocketMessage):
    """Message sent when a player times out"""
    type: WebSocketMessageType = WebSocketMessageType.TIMEOUT
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
