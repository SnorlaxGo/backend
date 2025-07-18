from sqlalchemy import Column, Integer, String, ForeignKey, Enum, JSON, DateTime, Boolean, UniqueConstraint
from sqlalchemy.orm import relationship
from enum import IntEnum
from datetime import datetime
from typing import Optional

from .database import Base
from .loggers import game_logger as logger  # Import the logger

class ChallengeStatus(str, Enum):
    MATCHED = "matched"
    WAITING = "waiting"
    EXPIRED = "expired"
    ERROR = "error"

class UserRole(str, Enum):
    ADMIN = "admin"
    MODERATOR = "moderator"
    USER = "user"

class TimeControl(IntEnum):
    BLITZ = 300          # 5 minutes in seconds
    RAPID = 600         # 10 minutes
    NORMAL = 1200       # 20 minutes
    LONG = 1800         # 30 minutes
    CORRESPONDENCE = 259200  # 3 days in seconds
    
class BoardSize(IntEnum):
    MINI = 5        # 5x5 board for quick games
    SMALL = 7       # 7x7 board for beginners
    MEDIUM = 9      # 9x9 board for intermediate
    BIG = 13        # 13x13 board for advanced
    STANDARD = 19   # 19x19 traditional board size

class GameStatus(IntEnum):
    """
    Represents the persistent status of a game in the database.
    This enum is used for database storage and querying.
    """
    ACTIVE = 1
    BLACK_WON = 2
    WHITE_WON = 3
    DRAW = 4
    BLACK_ABANDONED = 5
    WHITE_ABANDONED = 6
    BLACK_WON_TIMEOUT = 7
    WHITE_WON_TIMEOUT = 8
    BLACK_WON_RESIGNATION = 9
    WHITE_WON_RESIGNATION = 10

    @property
    def winner_id(self, game: 'Game') -> Optional[int]:
        if self in [GameStatus.BLACK_WON, GameStatus.BLACK_WON_TIMEOUT, GameStatus.BLACK_WON_RESIGNATION, GameStatus.WHITE_ABANDONED]:
            return game.black_player_id
        elif self in [GameStatus.WHITE_WON, GameStatus.WHITE_WON_TIMEOUT, GameStatus.WHITE_WON_RESIGNATION, GameStatus.BLACK_ABANDONED]:
            return game.white_player_id
        return None

class Game(Base):
    __tablename__ = "games"

    id = Column(Integer, primary_key=True, index=True)
    black_player_id = Column(Integer, ForeignKey("users.id"))
    white_player_id = Column(Integer, ForeignKey("users.id"))
    board_size = Column(Integer, Enum(BoardSize))
    time_control = Column(Integer, Enum(TimeControl), nullable=True)
    black_player = relationship("User", foreign_keys=[black_player_id], back_populates="games_as_black")
    white_player = relationship("User", foreign_keys=[white_player_id], back_populates="games_as_white")
    moves = relationship("Move", back_populates="game", order_by="Move.move_number")
    black_points = Column(Integer, default=0)
    white_points = Column(Integer, default=0)
    black_captures = Column(Integer, default=0)
    white_captures = Column(Integer, default=0)
    black_territory = Column(Integer, default=0)
    white_territory = Column(Integer, default=0)
    board_state = Column(JSON)  # This will store a 2D array of the current board
    status = Column(Integer, default=GameStatus.ACTIVE)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_move_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    move_count = Column(Integer, default=0)
    
    # Time tracking fields
    black_time_remaining = Column(Integer, default=0)  # Time used in seconds
    white_time_remaining = Column(Integer, default=0)  # Time used in seconds
    black_last_move_at = Column(DateTime)
    white_last_move_at = Column(DateTime)

    # Add these new fields after the existing columns
    draw_offered_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    draw_offered_at = Column(DateTime, nullable=True)

    def offer_draw(self, player_id: int) -> bool:
        """
        Offer a draw. Returns True if the offer was accepted, False otherwise.
        """
        logger.info("Draw offered in game %d by player %d", self.id, player_id)
        
        if self.status != GameStatus.ACTIVE:
            logger.warning("Draw offer rejected: game %d is not active (status: %s)", 
                          self.id, self.status)
            return False
        
        # Check if player is in the game
        if player_id not in (self.black_player_id, self.white_player_id):
            logger.warning("Draw offer rejected: player %d is not in game %d", 
                          player_id, self.id)
            return False
            
        self.draw_offered_by_id = player_id
        self.draw_offered_at = datetime.utcnow()
        logger.info("Draw offer recorded in game %d", self.id)
        return False

    def accept_draw(self, player_id: int) -> bool:
        """
        Accept a draw offer. Returns True if successful.
        """
        logger.info("Draw acceptance attempt in game %d by player %d", self.id, player_id)
        
        if not self.draw_offered_by_id or self.status != GameStatus.ACTIVE:
            logger.warning("Draw acceptance rejected: no active draw offer in game %d", self.id)
            return False
            
        # Can't accept your own draw offer
        if player_id == self.draw_offered_by_id:
            logger.warning("Draw acceptance rejected: player %d trying to accept their own offer", 
                          player_id)
            return False
            
        # Check if player is in the game
        if player_id not in (self.black_player_id, self.white_player_id):
            logger.warning("Draw acceptance rejected: player %d is not in game %d", 
                          player_id, self.id)
            return False
            
        self.status = GameStatus.DRAW
        self.clear_draw_offer()
        logger.info("Draw accepted in game %d", self.id)
        return True
    
    def clear_draw_offer(self) -> None:
        """Clear any existing draw offer"""
        if self.draw_offered_by_id:
            logger.debug("Clearing draw offer in game %d", self.id)
            self.draw_offered_by_id = None
            self.draw_offered_at = None

    def update_time_remaining(self, current_time: datetime = None) -> None:
        """Update time used by the player who just moved"""
        # Clear any draw offers when a move is made
        self.clear_draw_offer()
        
        if not current_time:
            current_time = datetime.utcnow()
            
        #if self.move_count <= 1:  # First move doesn't count against time
        if self.black_last_move_at is None:
            self.black_last_move_at = current_time
        if self.white_last_move_at is None:
            self.white_last_move_at = current_time

        is_black_turn = self.is_black_turn
        
        if not is_black_turn:  #before move increment white time
            if self.white_last_move_at:
                elapsed = int((current_time - self.black_last_move_at).total_seconds())
                self.white_time_remaining = (self.white_time_remaining or 0) + elapsed
                logger.debug("Game %d: Updated white time remaining to %d seconds", 
                            self.id, self.white_time_remaining)
            self.white_last_move_at = current_time
        else:  # Black just moved
            if self.black_last_move_at:
                elapsed = int((current_time - self.white_last_move_at).total_seconds())
                self.black_time_remaining = (self.black_time_remaining or 0) + elapsed
                logger.debug("Game %d: Updated black time remaining to %d seconds", 
                            self.id, self.black_time_remaining)
            self.black_last_move_at = current_time

        # Check for timeout
        if (self.time_control and 
            (self.black_time_remaining >= self.time_control or 
             self.white_time_remaining >= self.time_control)):
            timeout_status = GameStatus.BLACK_WON_TIMEOUT if self.is_black_turn else GameStatus.WHITE_WON_TIMEOUT
            logger.info("Game %d: Player timed out, setting status to %s", self.id, timeout_status)
            self.status = timeout_status
        
        logger.debug("Game %d: black_time_remaining=%d, white_time_remaining=%d", 
                    self.id, self.black_time_remaining, self.white_time_remaining)

    @property
    def is_black_turn(self) -> bool:
        """Determine if it's black's turn to play"""
        return self.move_count % 2 == 1  # Black plays on odd moves (1, 3, 5...)

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    email = Column(String, unique=True, index=True)
    hashed_password = Column(String, nullable=True)
    role = Column(String, default=UserRole.USER)
    is_anonymous = Column(Boolean, default=False)
    reset_token = Column(String, unique=True, nullable=True, index=True)
    reset_token_expires = Column(DateTime, nullable=True)
    # Replace single elo_rating with relationship to multiple ratings
    #ratings = relationship("PlayerRating", back_populates="user")
    
    # Keep the game relationships
    games_as_black = relationship("Game", foreign_keys=[Game.black_player_id], back_populates="black_player")
    games_as_white = relationship("Game", foreign_keys=[Game.white_player_id], back_populates="white_player")
    auth_providers = relationship("AuthProvider", back_populates="user")


class StoneColor(IntEnum):
    BLACK = 1
    WHITE = 2

class Move(Base):
    __tablename__ = "moves"

    id = Column(Integer, primary_key=True, index=True)
    game_id = Column(Integer, ForeignKey("games.id"))
    move_number = Column(Integer, index=True)
    x = Column(Integer)
    y = Column(Integer)
    color = Column(Enum(StoneColor))
    game = relationship("Game", back_populates="moves")
    resulting_board_state = Column(JSON)  # Store the full board state after this move
    captured_positions = Column(JSON)  # Store positions of any stones captured by this move
    is_pass = Column(Boolean, default=False)

class Challenge(Base):
    __tablename__ = "challenges"

    id = Column(Integer, primary_key=True, index=True)
    challenger_id = Column(Integer, ForeignKey("users.id"))
    board_size = Column(Integer)
    time_control = Column(Integer, nullable=True)
    status = Column(String)  # "open", "pending", "matched", "accepted", "expired"
    created_at = Column(DateTime, default=datetime.utcnow)
    is_anonymous = Column(Boolean, default=False)

"""
class PlayerRating(Base):
    __tablename__ = "player_ratings"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    board_size = Column(Integer, Enum(BoardSize))  # Track ratings for different board sizes
    time_control = Column(Integer, Enum(TimeControl), nullable=True)  # Optional time control category
    rating = Column(Integer, default=1500)
    games_played = Column(Integer, default=0)
    wins = Column(Integer, default=0)
    losses = Column(Integer, default=0)
    draws = Column(Integer, default=0)
    last_updated = Column(DateTime, default=datetime.utcnow)
    
    # Relationship to User
    user = relationship("User", back_populates="ratings")
    
    __table_args__ = (
        # Ensure each user has only one rating per board size and time control combination
        UniqueConstraint('user_id', 'board_size', 'time_control', name='unique_user_rating'),
    )
"""

from enum import Enum as PyEnum  # Rename to avoid conflict with SQLAlchemy's Enum

# Add this enum class with your other enums
class AuthProviderType(str, PyEnum):
    APPLE = "apple"
    GOOGLE = "google"
    EMAIL = "email"
    FACEBOOK = "facebook"
    GITHUB = "github"

class AuthProvider(Base):
    """Model for storing authentication provider information"""
    __tablename__ = "auth_providers"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    provider = Column(String, Enum(AuthProviderType), nullable=False)  # Use the enum
    provider_user_id = Column(String, nullable=False)  # Provider's unique identifier
    provider_email = Column(String, nullable=True)  # Email from the provider
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationship to User
    user = relationship("User", back_populates="auth_providers")
    
    # Composite unique constraint
    __table_args__ = (
        UniqueConstraint('provider', 'provider_user_id', name='uix_provider_id'),
    )
