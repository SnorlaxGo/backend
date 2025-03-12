import pytest
import asyncio
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from fastapi.testclient import TestClient
import uuid

# Import your app and models
from go_game.server import app
from go_game.database import Base, get_db
from go_game.models import User, Game, Challenge

# Create a PostgreSQL test database
# Use environment variables or default to a test database
TEST_DB_NAME = f"go_game_test_{uuid.uuid4().hex[:8]}"  # Generate unique test DB name
TEST_POSTGRES_URL = os.environ.get(
    "TEST_DATABASE_URL", 
    f"postgresql://postgres:postgres@localhost/{TEST_DB_NAME}"
)

# Create a new database for testing
def create_test_database():
    from sqlalchemy_utils import database_exists, create_database, drop_database
    
    # Connect to the default postgres database to create/drop test database
    default_engine = create_engine(os.environ.get(
        "DATABASE_URL", 
        "postgresql://postgres:postgres@localhost/postgres"
    ))
    
    # Create test database if it doesn't exist
    if not database_exists(TEST_POSTGRES_URL):
        create_database(TEST_POSTGRES_URL)
    
    # Return the engine connected to the test database
    return create_engine(TEST_POSTGRES_URL)

@pytest.fixture(scope="session")
def test_engine():
    # Create the test database and get engine
    engine = create_test_database()
    
    # Create all tables
    Base.metadata.create_all(bind=engine)
    
    yield engine
    
    # Drop the test database after all tests
    from sqlalchemy_utils import drop_database
    drop_database(TEST_POSTGRES_URL)

@pytest.fixture(scope="function")
def db(test_engine):
    # Create a new session for each test
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    db = TestingSessionLocal()
    
    # Start with a clean slate for each test
    for table in reversed(Base.metadata.sorted_tables):
        db.execute(table.delete())
    db.commit()
    
    # Override the get_db dependency
    def override_get_db():
        try:
            yield db
        finally:
            pass
    
    app.dependency_overrides[get_db] = override_get_db
    
    yield db
    
    # Clean up after the test
    db.close()

@pytest.fixture
def test_client():
    return TestClient(app)

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
    for task_key, task in list(manager.disconnect_tasks.items()):
        if not task.done():
            task.cancel()
    manager.disconnect_tasks = {}
    
    challenge_manager.active_connections = {}
    
    # Reset Redis if connected
    if hasattr(redis_manager, 'redis_conn') and redis_manager.redis_conn:
        await redis_manager.disconnect()
    redis_manager.redis_conn = None
    redis_manager.pubsub = None
    redis_manager.listener_task = None