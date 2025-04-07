from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from passlib.context import CryptContext
import jwt
from jwt import PyJWTError
from .database import get_db
from . import models
from pydantic import BaseModel
from typing import Union, List, Dict, Optional, Tuple
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
import os
from functools import wraps
import json
import requests
from .config import settings
from .schemas import Token, TokenData, User, UserInDB

load_dotenv()

# Remove the hardcoded SECRET_KEY and get it from environment
SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    raise ValueError("No SECRET_KEY set in environment variables")

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
REFRESH_TOKEN_EXPIRE_DAYS = 7

APPLE_PUBLIC_KEYS_URL = "https://appleid.apple.com/auth/keys"
APPLE_CLIENT_ID = settings.APPLE_CLIENT_ID

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

credentials_exception = HTTPException(
    status_code=401,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)

def validate_token(token: str) -> dict:
    """Core token validation logic, returns payload with username and token type"""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        token_type: str = payload.get("type", "access")  # Default to access for backward compatibility
        if username is None:
            raise credentials_exception
        return {"username": username, "type": token_type}
    except PyJWTError:
        raise credentials_exception

async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> models.User:
    payload = validate_token(token)
    username = payload["username"]
    # Only allow access tokens for API authentication
    if payload["type"] != "access":
        raise HTTPException(
            status_code=401,
            detail="Invalid token type",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = db.query(models.User).filter(models.User.username == username).first()
    if user is None:
        raise credentials_exception
    return user

async def get_current_user_ws(token: bytes, db: Session) -> models.User:
    payload = validate_token(token.decode())
    username = payload["username"]
    user = db.query(models.User).filter(models.User.username == username).first()
    if user is None:
        raise credentials_exception
    return user

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password):
    return pwd_context.hash(password)
def create_access_token(data: dict, expires_delta: Union[timedelta, None] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire, "type": "access"})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def create_refresh_token(data: dict):
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire, "type": "refresh"})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def get_user(db, username: str):
    if username in db:
        user_dict = db[username]
        return UserInDB(**user_dict)
    
def authenticate_user(db: Session, username: str, password: str):
    user = db.query(models.User).filter(models.User.username == username).first()
    if not user:
        return False
    if not verify_password(password, user.hashed_password):
        return False
    return user

def check_permissions(allowed_roles: List[str]):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, current_user: models.User = Depends(get_current_user), **kwargs):
            if current_user.role not in allowed_roles:
                raise HTTPException(
                    status_code=403,
                    detail="You don't have permission to perform this action"
                )
            return await func(*args, current_user=current_user, **kwargs)
        return wrapper
    return decorator

def get_apple_public_keys() -> Dict:
    """Fetch Apple's public keys for token verification"""
    try:
        response = requests.get(APPLE_PUBLIC_KEYS_URL)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        raise ValueError(f"Failed to fetch Apple public keys: {str(e)}")

def verify_apple_token(identity_token: str) -> Tuple[bool, Optional[Dict], Optional[str]]:
    """
    Verify an Apple identity token
    
    Args:
        identity_token: The identity token from Apple Sign In
        
    Returns:
        Tuple containing:
        - Boolean indicating if verification was successful
        - Dictionary with token claims if successful, None otherwise
        - Error message if verification failed, None otherwise
    """
    if not APPLE_CLIENT_ID:
        return False, None, "APPLE_CLIENT_ID environment variable not set"
    
    try:
        # Get the Apple public keys
        apple_keys = get_apple_public_keys()
        
        # Get the kid (Key ID) from the token header
        token_headers = jwt.get_unverified_header(identity_token)
        kid = token_headers.get('kid')
        
        if not kid:
            return False, None, "No key ID found in token header"
        
        # Find the matching public key
        public_key = None
        for key in apple_keys.get('keys', []):
            if key.get('kid') == kid:
                # Convert JWK to PEM format
                public_key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(key))
                break
        
        if not public_key:
            return False, None, "No matching public key found"
        
        # Verify the token
        payload = jwt.decode(
            identity_token,
            public_key,
            algorithms=['RS256'],
            audience=APPLE_CLIENT_ID,  # Your app's client ID
            options={"verify_exp": True}
        )
        
        # Verify issuer is Apple
        if payload.get('iss') != 'https://appleid.apple.com':
            return False, None, "Invalid token issuer"
        
        return True, payload, None
        
    except jwt.ExpiredSignatureError:
        return False, None, "Token has expired"
    except jwt.InvalidAudienceError:
        return False, None, "Token has invalid audience"
    except jwt.DecodeError:
        return False, None, "Token signature verification failed"
    except Exception as e:
        return False, None, f"Token verification failed: {str(e)}"

