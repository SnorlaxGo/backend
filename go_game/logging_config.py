import logging
import os
import sys
import json
from datetime import datetime
from logging.handlers import RotatingFileHandler

# Determine if we're running in production
IS_PRODUCTION = os.environ.get("ENVIRONMENT", "development").lower() == "production"

# Configure log format based on environment
if IS_PRODUCTION:
    # JSON format for production (easier to parse by log aggregation tools)
    class JsonFormatter(logging.Formatter):
        def format(self, record):
            log_record = {
                "timestamp": datetime.utcnow().isoformat(),
                "level": record.levelname,
                "message": record.getMessage(),
                "module": record.module,
                "function": record.funcName,
                "line": record.lineno,
            }
            
            # Include exception info if available
            if record.exc_info:
                log_record["exception"] = self.formatException(record.exc_info)
                
            # Include any extra attributes
            for key, value in record.__dict__.items():
                if key not in ["args", "asctime", "created", "exc_info", "exc_text", 
                              "filename", "funcName", "id", "levelname", "levelno", 
                              "lineno", "module", "msecs", "message", "msg", 
                              "name", "pathname", "process", "processName", 
                              "relativeCreated", "stack_info", "thread", "threadName"]:
                    log_record[key] = value
                    
            return json.dumps(log_record)
    
    log_formatter = JsonFormatter()
else:
    # Human-readable format for development
    log_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s [%(filename)s:%(lineno)d]'
    )

def setup_logging(app_name="go_game"):
    """Configure application-wide logging"""
    logger = logging.getLogger(app_name)
    
    # Set default level
    log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logger.setLevel(getattr(logging, log_level))
    
    # Clear any existing handlers
    if logger.handlers:
        logger.handlers.clear()
    
    # Always log to console
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(log_formatter)
    logger.addHandler(console_handler)
    
    # In development, also log to file
    if not IS_PRODUCTION:
        log_dir = os.path.join(os.getcwd(), "logs")
        os.makedirs(log_dir, exist_ok=True)
        
        file_handler = RotatingFileHandler(
            os.path.join(log_dir, f"{app_name}.log"),
            maxBytes=10485760,  # 10MB
            backupCount=5
        )
        file_handler.setFormatter(log_formatter)
        logger.addHandler(file_handler)
    
    # Prevent propagation to root logger
    logger.propagate = False
    
    return logger

# Create a default logger instance
logger = setup_logging()

# Create specialized loggers for different components
def get_logger(name):
    """Get a logger for a specific component"""
    component_logger = logging.getLogger(f"go_game.{name}")
    
    # Inherit level from root logger
    component_logger.setLevel(logger.level)
    
    return component_logger 