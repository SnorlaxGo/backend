from typing import List, Optional
from ..models import Game, Move, StoneColor

def visualize_board(board_or_game):
    """
    Creates an ASCII representation of a Go board using the current board state
    Example output for 9x9:
    
    9 . . . . . . . . .
    8 . . . . . . . . .
    7 . . . ● . ○ . . .
    6 . . . . . . . . .
    5 . . . . . . . . .
    4 . . . . . . . . .
    3 . . . ○ . ● . . .
    2 . . . . . . . . .
    1 . . . . . . . . .
      a b c d e f g h i
    """
    # Handle both Game objects and raw board lists
    if hasattr(board_or_game, 'board_size'):
        # It's a Game object
        game = board_or_game
        size = game.board_size
        board = game.board_state or [[0] * size for _ in range(size)]
    else:
        # It's a board list
        board = board_or_game
        size = len(board)
    
    # Create the string representation
    result = []
    
    # Add board rows with coordinates
    for i in range(size):
        row_num = size - i
        row_symbols = []
        for j in range(size):
            cell = board[i][j]
            if cell == 0:
                symbol = '.'
            elif cell == StoneColor.WHITE.value:
                symbol = '●'
            else:
                symbol = '○'
            row_symbols.append(symbol)
        row = f"{row_num:2d} {' '.join(row_symbols)}"
        result.append(row)
    
    # Add column coordinates
    col_coords = '   ' + ' '.join(chr(ord('a') + i) for i in range(size))
    result.append(col_coords)
    
    return '\n'.join(result)

def visualize_game(game: Game) -> str:
    """
    Creates a visualization of a specific game state
    """
    board = visualize_board(game)
    
    # Add game info header
    header = [
        f"Game ID: {game.id}",
        f"Black: {game.black_player.username}",
        f"White: {game.white_player.username}",
        f"Moves: {len(game.moves)}",
        f"Black captures: {game.black_captures}",
        f"White captures: {game.white_captures}",
        "",
        board
    ]
    
    return '\n'.join(header)

# Example usage in Python shell:
"""
from go_game.database import SessionLocal
from go_game.models import Game
from go_game.utils.board_visualizer import visualize_game

db = SessionLocal()
game = db.query(Game).first()
print(visualize_game(game))
""" 