from pydantic import BaseModel, Field
from typing import List, Optional, Literal, List, Tuple
from datetime import datetime
from enum import Enum
from .models import StoneColor, ChallengeStatus, GameStatus

class WebSocketMessageType(str, Enum):
    GAME_ABANDONED = "game_abandoned"
    GAME_STATE = "game_state"
    MOVE = "move"
    ERROR = "error"
    TIMEOUT = "timeout"
    RESIGN = "resign"


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
    data: GameStateResponse

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