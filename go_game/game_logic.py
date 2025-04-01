from sqlalchemy.orm import Session
from .models import Game, Move, StoneColor, GameStatus
from .schemas import GameStateResponse
from .database import SessionLocal
from typing import List, Dict, Any, Optional
from datetime import datetime
from .loggers import game_logger as logger  # Import the logger
from enum import Enum
from dataclasses import dataclass
from .models import TimeControl
from .event_manager import redis_manager
import asyncio
from .timer_service import timer_service  # Import your timer service instance

class MoveResultType(Enum):
    """
    Represents the result of a move operation in the game logic.
    This is an internal enum used by the game service.
    """
    SUCCESS = "success"  # Move was successful
    TIMEOUT = "timeout"  # Player ran out of time
    GAME_OVER = "game_over"  # Game has already ended
    INVALID = "invalid"  # Move was invalid (could add more specific types)

@dataclass
class MoveResult:
    type: MoveResultType
    game: dict  # The game state response
    player_color: Optional[StoneColor] = None  # For timeout/resignation
    message: Optional[str] = None

class GameService:
    def __init__(self, db: Session):
        self.db = db
    
    def get_game(self, game_id: int) -> Game:
        logger.debug("Fetching game with ID: %d", game_id)
        game = self.db.query(Game).filter(Game.id == game_id).first()
        if not game:
            logger.warning("Game not found: %d", game_id)
            raise Exception("Game not found")
        return game
    
    def to_response(self, game, player_color: Optional[StoneColor] = None) -> GameStateResponse:
        """Convert internal game state to API response format"""
        logger.debug("Converting game %d to response format", game.id)
        result = self.get_game_state(game)
        return GameStateResponse(
            success=True,
            board=result["board"],
            captured=[],
            black_captures=game.black_captures,
            white_captures=game.white_captures,
            black_time_used=game.black_time_remaining,
            white_time_used=game.white_time_remaining,
            color=StoneColor.BLACK.value if game.is_black_turn else StoneColor.WHITE.value,
            status=game.status,
            move_number=game.move_count
        )
    def get_game_state(self, game: Game) -> dict:
        """Pure function to compute game state from a game object"""
        logger.debug("Computing game state for game %d", game.id)
        # Get last move efficiently
        last_move = (
            self.db.query(Move)
            .filter(Move.game_id == game.id)
            .order_by(Move.move_number.desc())
            .first()
        )

        # Get board from game state or create new one
        if game.board_state:
            board = game.board_state
        else:
            logger.debug("Creating new board for game %d with size %d", game.id, game.board_size)
            board = [[0 for _ in range(game.board_size)] for _ in range(game.board_size)]
            game.board_state = board

        if not last_move:
            color = StoneColor.WHITE
        else:
            color = StoneColor.BLACK if StoneColor(last_move.color) == StoneColor.WHITE else StoneColor.BLACK
        
        return {
            "board": board,
            "board_size": game.board_size,
            "last_move": last_move,
            "move_count": game.move_count,
            "black_captures": game.black_captures,
            "white_captures": game.white_captures,
            "black_territory": game.black_territory,
            "white_territory": game.white_territory,
            "black_points": game.black_points,
            "white_points": game.white_points,
            "black_time_used": game.black_time_remaining,
            "white_time_used": game.white_time_remaining,
            "status": game.status,
            "color": color
        }

    def make_move(self, game_id: int, x: int, y: int, player_id: int) -> MoveResult:
        """Process a move for a given game"""
        logger.info("Processing move for game %d: player %d at position (%d, %d)", 
                   game_id, player_id, x, y)
        
        game = self.get_game(game_id)
        self._validate_game_status(game)
        
        game_state = self.get_game_state(game)
        current_time = datetime.utcnow()
        player_color = StoneColor.BLACK if game.black_player_id == player_id else StoneColor.WHITE
        logger.debug("Player color: %s", player_color)
        
        # Verify it's the player's turn
        self._validate_player_turn(game, player_color)
        
        # Check if player has exceeded their time limit
        timeout_result = self._check_time_limits(game)
        if timeout_result:
            return timeout_result

        # Process the move
        try:
            return self._process_valid_move(game, game_state, x, y, player_color, current_time)
        except Exception as e:
            logger.error("Error processing move in game %d: %s", game_id, str(e), exc_info=True)
            raise

    def _validate_game_status(self, game: Game) -> None:
        """Validate that the game is active"""
        if game.status != GameStatus.ACTIVE:
            logger.warning("Attempted move on inactive game %d (status: %s)", 
                          game.id, game.status)
            raise InvalidMoveError("Game is not active")

    def _validate_player_turn(self, game: Game, player_color: StoneColor) -> None:
        """Validate that it's the player's turn"""
        logger.debug("Game %d: move_count=%d, player_color=%s, is_black_turn=%s", 
                    game.id, game.move_count, player_color, game.is_black_turn)
                    
        if (game.is_black_turn and player_color == StoneColor.WHITE) or \
           (not game.is_black_turn and player_color == StoneColor.BLACK):
            logger.warning("Not player's turn in game %d: color=%s, is_black_turn=%s", 
                          game.id, player_color, game.is_black_turn)
            raise InvalidMoveError("Not your turn")

    def _check_time_limits(self, game: Game) -> Optional[MoveResult]:
        """Check if player has exceeded their time limit"""
        if not game.time_control:
            return None
        
        black_time = game.black_time_remaining or 0
        white_time = game.white_time_remaining or 0
        
        if game.is_black_turn and black_time >= game.time_control:
            logger.warning("Black player out of time in game %d: %d seconds used", 
                          game.id, black_time)
            game.status = GameStatus.BLACK_TIMEOUT
            self.db.commit()
            return MoveResult(
                type=MoveResultType.TIMEOUT,
                player_color=StoneColor.BLACK,
                game=self.to_response(game),
                message="Black player has run out of time"
            )
        elif not game.is_black_turn and white_time >= game.time_control:
            logger.warning("White player out of time in game %d: %d seconds used", 
                          game.id, white_time)
            game.status = GameStatus.WHITE_TIMEOUT
            self.db.commit()
            return MoveResult(
                type=MoveResultType.TIMEOUT,
                player_color=StoneColor.WHITE,
                game=self.to_response(game),
                message="White player has run out of time"
            )
        
        return None

    def _process_valid_move(self, game: Game, game_state: dict, x: int, y: int, 
                            player_color: StoneColor, current_time: datetime) -> MoveResult:
        """Process a valid move and update the game state"""
        result = process_move(game_state["board"], x, y, player_color, game_state["last_move"])
        if not result["success"]:
            logger.warning("Invalid move in game %d: %s", game.id, result.get("error", "Unknown error"))
            raise InvalidMoveError(result.get("error", "Invalid move"))
            
        logger.info("Move successful in game %d: captured %d stones", 
                   game.id, len(result["captured"]))
                   
        # Create new move record
        new_move = Move(
            game_id=game.id,
            move_number=game.move_count,
            x=x,
            y=y,
            color=player_color,
            captured_positions=result["captured"],
            resulting_board_state=result["board"]
        )
        self.db.add(new_move)

        # Update game state
        self._update_game_state(game, result, current_time)
        
        # Schedule timeout task if needed
        self._schedule_timeout_task(game)
        
        logger.debug("Game %d updated: move_count=%d, black_captures=%d, white_captures=%d",
                    game.id, game.move_count, game.black_captures, game.white_captures)
                    
        return MoveResult(
            type=MoveResultType.SUCCESS,
            game=self.to_response(game)
        )

    def _update_game_state(self, game: Game, result: dict, current_time: datetime) -> None:
        """Update the game state after a successful move"""
        game.board_state = result["board"]
        game.black_captures += result["black_captures"]
        game.white_captures += result["white_captures"]
        game.last_move_at = current_time
        game.update_time_remaining(current_time)
        game.move_count += 1
        self.db.add(game)
        self.db.commit()

    def _schedule_timeout_task(self, game: Game) -> None:
        """Schedule a timeout task for the next player if needed"""
        if game.time_control and game.time_control != TimeControl.CORRESPONDENCE:
            current_player_color = StoneColor.WHITE if game.is_black_turn else StoneColor.BLACK
            next_player_color = StoneColor.BLACK if game.is_black_turn else StoneColor.WHITE
            time_used = game.black_time_remaining if game.is_black_turn else game.white_time_remaining
            time_remaining = game.time_control - time_used
            
            # Use the timer service to set the timer
            asyncio.create_task(
                timer_service.cancel_timer(game.id, current_player_color)
            )
            asyncio.create_task(
                timer_service.set_timer(game.id, next_player_color, time_remaining)
            )
            
            logger.info(f"Scheduled timer for game {game.id}, player {next_player_color.value}: {time_remaining}s")

class InvalidMoveError(Exception):
    code = "INVALID_MOVE"
    def __init__(self, message="Invalid move"):
        self.message = message
        super().__init__(self.message)

class KoViolationError(InvalidMoveError):
    code = "KO_VIOLATION"
    def __init__(self, message="Move violates the ko rule"):
        super().__init__(message)

class SuicideMoveError(InvalidMoveError):
    code = "SUICIDE_MOVE"
    def __init__(self, message="Move would result in suicide"):
        super().__init__(message)

class OccupiedPointError(InvalidMoveError):
    code = "POINT_OCCUPIED"
    def __init__(self, message="Position is already occupied"):
        super().__init__(message)

class OutOfBoundsError(InvalidMoveError):
    code = "OUT_OF_BOUNDS"
    def __init__(self, message="Position is outside the board"):
        super().__init__(message)

def validate_move(board: List[List[StoneColor]], x: int, y: int, color: StoneColor, last_move: Move):
    # Check if this move matches the expected stone color based on last move
    if last_move:
        last_color = StoneColor(last_move.color)
        if last_color == color:
            raise InvalidMoveError(f"It is not {color.name}'s turn")
    
    elif color != StoneColor.WHITE:
        logger.debug("First move color: %s", color)
        raise InvalidMoveError("White must make the first move")

    if not board:
        raise InvalidMoveError("No board provided")
    
    if x < 0 or x >= len(board) or y < 0 or y >= len(board):
        raise OutOfBoundsError(f"Position ({x}, {y}) is outside the board")
    
    if is_ko_violation(board, last_move, x, y, color):
        raise KoViolationError(f"Move at ({x}, {y}) violates the ko rule")
    
    if is_suicide_move(board, x, y, color) and not can_capture(board, x, y, color):
        raise SuicideMoveError(f"Move at ({x}, {y}) would be suicide")
    
    return True

def process_move(board: List[List[StoneColor]], x: int, y: int, color: StoneColor, last_move: Move):
    """Pure game logic for processing a move, without database interactions"""
    # Create a copy of the board to avoid modifying the original
    if board[y][x] != 0:
        logger.debug("board[y][x]: %s", board[y][x])
        raise OccupiedPointError(f"Position ({x}, {y}) is already occupied")
    
    new_board = [row[:] for row in board]
    logger.debug("old board: %s", board)
    # Place the stone
    new_board[y][x] = color.value
    
    # Will raise InvalidMoveError if move is invalid
    validate_move(new_board, x, y, color, last_move)
    
    # Process captures
    captured = capture_stones(new_board, x, y, color)
    
    # Calculate captures for each color
    black_captures = len(captured) if color == StoneColor.BLACK else 0
    white_captures = len(captured) if color == StoneColor.WHITE else 0
    logger.debug("new_board: %s", new_board)
    return {
        "board": new_board,
        "captured": captured,
        "black_captures": black_captures,
        "white_captures": white_captures,
        "success": True
    }

def is_ko_violation(board: List[List[StoneColor]], last_move: Move, x: int, y: int, color: StoneColor) -> bool:
    """
    Check if a move would violate the ko rule
    Args:
        board: Current board state
        last_move: The previous move including its captured positions
        x, y: Coordinates of the proposed move
        color: Color of the proposed move
    """
    if not last_move or last_move.color == color:
        return False

    # If exactly one stone was captured in the last move
    if hasattr(last_move, 'captured_positions') and len(last_move.captured_positions) == 1:
        # Check if current move is at the position of the captured stone
        cx, cy = last_move.captured_positions[0]
        if x == cx and y == cy:
            # Simulate the move and check if it would capture the last played stone
            temp_board = [row[:] for row in board]
            temp_board[y][x] = color.value
            if not has_liberties(temp_board, x, y):
                return True

    return False

def get_captured_stones(board: List[List[StoneColor]], x: int, y: int, color: StoneColor):
    captured = []
    opponent_color = StoneColor.WHITE if color == StoneColor.BLACK else StoneColor.BLACK
    for dx, dy in [(0, 1), (1, 0), (0, -1), (-1, 0)]:
        nx, ny = x + dx, y + dy
        if 0 <= nx < len(board) and 0 <= ny < len(board) and board[ny][nx] == opponent_color.value:
            if not has_liberties(board, nx, ny):
                captured.append((nx, ny))
    return captured

def is_suicide_move(board: List[List[StoneColor]], x: int, y: int, color: StoneColor):
    temp_board = [row[:] for row in board]
    temp_board[y][x] = color.value
    if has_liberties(temp_board, x, y):
        return False
    return True

def can_capture(board: List[List[StoneColor]], x: int, y: int, color: StoneColor):
    opponent_color = StoneColor.WHITE if color == StoneColor.BLACK else StoneColor.BLACK
    directions = [(0, 1), (1, 0), (0, -1), (-1, 0)]
    
    for dx, dy in directions:
        nx, ny = x + dx, y + dy
        if 0 <= nx < len(board) and 0 <= ny < len(board) and board[ny][nx] == opponent_color.value:
            if not has_liberties(board, nx, ny):
                return True
    return False

def has_liberties(board: List[List[StoneColor]], x: int, y: int):
    color = board[y][x]
    visited = set()
    stack = [(x, y)]
    
    while stack:
        cx, cy = stack.pop()
        if (cx, cy) in visited:
            continue
        visited.add((cx, cy))
        
        for dx, dy in [(0, 1), (1, 0), (0, -1), (-1, 0)]:
            nx, ny = cx + dx, cy + dy
            if 0 <= nx < len(board) and 0 <= ny < len(board):
                if board[ny][nx] == 0:
                    logger.debug(f"Liberty found at {ny}, {nx}, color: {board[ny][nx]}")
                    return True
                if board[ny][nx] == color and (nx, ny) not in visited:
                    stack.append((nx, ny))
    return False

def capture_stones(board: List[List[StoneColor]], x: int, y: int, color: StoneColor):
    captured = []
    logger.debug(f"Checking captures at {x}, {y}, color: {color}")
    opponent_color = StoneColor.WHITE if color == StoneColor.BLACK.value else StoneColor.BLACK
    logger.debug(f"Opponent color: {opponent_color}")
    for dx, dy in [(0, 1), (1, 0), (0, -1), (-1, 0)]:
        nx, ny = x + dx, y + dy
        if 0 <= nx < len(board) and 0 <= ny < len(board) and board[ny][nx] == opponent_color.value:
            group = get_connected_stones(board, nx, ny)
            if not any(has_liberties(board, gx, gy) for gx, gy in group):
                captured.extend(group)
                for gx, gy in group:
                    board[gy][gx] = 0  # Remove captured stones
    return captured

def get_connected_stones(board: List[List[StoneColor]], x: int, y: int):
    visited = set()
    color = board[y][x]
    stack = [(x, y)]
    
    connected = []
    
    while stack:
        cx, cy = stack.pop()
        if (cx, cy) in visited:
            continue
        visited.add((cx, cy))
        
        connected.append((cx, cy))
        
        for dx, dy in [(0, 1), (1, 0), (0, -1), (-1, 0)]:
            nx, ny = cx + dx, cy + dy
            if 0 <= nx < len(board) and 0 <= ny < len(board):
                if board[ny][nx] == color:
                    stack.append((nx, ny))

    return connected

def calculate_territory(board: List[List[StoneColor]]):
    territory = {StoneColor.BLACK: 0, StoneColor.WHITE: 0}
    visited = set()

    for y in range(len(board)):
        for x in range(len(board)):
            if (x, y) not in visited and board[y][x] == 0:
                area, color = flood_fill(board, x, y, visited)
                if color in (StoneColor.BLACK, StoneColor.WHITE):
                    territory[color] += area

    return territory

def flood_fill(board: List[List[StoneColor]], x: int, y: int, visited: set):
    queue = [(x, y)]
    area = 0
    colors = set()

    while queue:
        x, y = queue.pop(0)
        if (x, y) in visited:
            continue

        visited.add((x, y))
        area += 1

        for dx, dy in [(0, 1), (1, 0), (0, -1), (-1, 0)]:
            nx, ny = x + dx, y + dy
            if 0 <= nx < len(board) and 0 <= ny < len(board):
                if board[ny][nx] == 0:
                    queue.append((nx, ny))
                elif board[ny][nx] in (StoneColor.BLACK.value, StoneColor.WHITE.value):
                    colors.add(StoneColor(board[ny][nx]))

    if len(colors) == 1:
        return area, colors.pop()
    return 0, None
