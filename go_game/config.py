import os
from typing import Optional

class Settings:
    PROJECT_NAME: str = "Go Game API"
    VERSION: str = "1.0.0"
    API_V1_STR: str = "/api/v1"
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    DATABASE_URL: str = os.getenv("ALCHEMY_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/go_game")
    
    # JWT settings
    SECRET_KEY: str = os.getenv("SECRET_KEY", "your-secret-key-for-development")
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    
    # Game settings
    CHALLENGE_TIMEOUT: int = 10  # seconds
    DISCONNECT_TIMEOUT: int = 20  # seconds

    # Mailgun settings
    MAILGUN_API_KEY: str = os.getenv("MAILGUN_API_KEY", "your-mailgun-api-key")
    MAILGUN_DOMAIN: str = os.getenv("MAILGUN_DOMAIN", "your-domain.com")

    # Apple settings
    APPLE_CLIENT_ID: str = "tonsil.go"

settings = Settings() 
