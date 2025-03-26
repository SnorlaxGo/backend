import pytest
import asyncio
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from fastapi.testclient import TestClient
import uuid

# Use environment variables or default to a test database with Docker settings
DB_NAME = "go_game"
POSTGRES_URL = os.environ.get(
    "DATABASE_URL", 
    f"postgresql://postgres:postgres@localhost:5432/{DB_NAME}"
)

# Create the database if it doesn't exist
def setup_database():
    from sqlalchemy_utils import database_exists, create_database
    
    # Connect to the default postgres database
    default_engine = create_engine(
        "postgresql://postgres:postgres@localhost:5432/postgres"
    )
    
    # Create database if it doesn't exist
    if not database_exists(POSTGRES_URL):
        with default_engine.connect() as conn:
            conn.execute("COMMIT")  # Close any open transaction
            conn.execute(f"CREATE DATABASE {DB_NAME}")
    
    # Return the engine connected to the database
    return create_engine(POSTGRES_URL)

# Create the database before importing the app
setup_database()

# Now import your app and models
from go_game.server import app
from go_game.database import Base, get_db
from go_game.models import User, Game, Challenge

@pytest.fixture(scope="session")
def db_engine():
    engine = create_engine(POSTGRES_URL)
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)

@pytest.fixture
def db_session(db_engine):
    Session = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)
    session = Session()
    
    # Start with a clean slate for each test
    for table in reversed(Base.metadata.sorted_tables):
        session.execute(table.delete())
    session.commit()
    
    # Override the get_db dependency
    def override_get_db():
        try:
            yield session
        finally:
            pass
    
    app.dependency_overrides[get_db] = override_get_db
    
    try:
        yield session
    finally:
        session.close()

@pytest.fixture
def db(db_session):
    yield db_session

@pytest.fixture
async def test_client():
    """Create a test client for the FastAPI app."""
    # Override the get_db dependency to use the test database
    def override_get_db():
        try:
            db = next(get_db())
            yield db
        finally:
            pass
    
    app.dependency_overrides[get_db] = override_get_db
    
    # Use TestClient for WebSocket tests
    from fastapi.testclient import TestClient
    client = TestClient(app)
    yield client
    
    # Clean up
    app.dependency_overrides.clear()

@pytest.fixture
def test_user(db):
    # Create a test user
    unique_id = uuid.uuid4().hex[:8]
    user = User(
        username=f"testuser_{unique_id}",
        email=f"test_{unique_id}@example.com",
        hashed_password="$2b$12$EixZaYVK1fsbw1ZfbX3OXePaWxn96p36WQoeG6Lruj3vjPGga31lW"  # "password"
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user

@pytest.fixture
def test_opponent(db):
    # Create an opponent
    unique_id = uuid.uuid4().hex[:8]
    user = User(
        username=f"opponent_{unique_id}",
        email=f"opponent_{unique_id}@example.com",
        hashed_password="$2b$12$EixZaYVK1fsbw1ZfbX3OXePaWxn96p36WQoeG6Lruj3vjPGga31lW"  # "password"
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user

@pytest.fixture(autouse=True)
async def reset_manager():
    """Reset the WebSocket manager state between tests"""
    from go_game.websocket_manager import manager, challenge_manager, redis_manager
    
    # Reset state for test
    manager.active_connections = {}
    manager.player_game_connections = {}
    
    # Cancel any pending disconnect tasks
    print("cancelling disconnect tasks", flush=True)
    for task_key, task in list(manager.disconnect_tasks.items()):
        if not task.done():
            task.cancel()
    manager.disconnect_tasks = {}
    
    challenge_manager.active_connections = {}
    
    # Reset Redis if connected
    if hasattr(redis_manager, 'redis_conn') and redis_manager.redis_conn:
        print("disconnecting redis", flush=True)
        await redis_manager.disconnect()
    redis_manager.redis_conn = None
    redis_manager.pubsub = None
    redis_manager.listener_task = None

@pytest.fixture(autouse=True)
async def cleanup_websocket_manager():
    """Clean up the WebSocket manager state after each test."""
    yield
    
    # Import here to avoid circular imports
    from go_game.websocket_manager import manager
    
    # Cancel any pending tasks
    for task_key, task in list(manager.disconnect_tasks.items()):
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    
    # Clear the manager state
    manager.active_connections = {}
    manager.player_game_connections = {}
    manager.disconnect_tasks = {}