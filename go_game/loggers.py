from .logging_config import get_logger

# Create specialized loggers for different components
game_logger = get_logger("game")
auth_logger = get_logger("auth")
api_logger = get_logger("api")
db_logger = get_logger("db")
elo_logger = get_logger("elo")

# Add more specialized loggers as needed 