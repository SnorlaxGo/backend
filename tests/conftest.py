import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import Session

from go_game.main import app
from go_game.database import Base, get_db
from go_game.models import User

# Create test database
SQLALCHEMY_TEST_DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(
    SQLALCHEMY_TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@pytest.fixture
def test_db(): 
    # Create tables
    Base.metadata.create_all(bind=engine)
    
    # Create test users
    db = TestingSessionLocal()
    test_user1 = User(username="testuser1", email="test1@example.com")
    test_user2 = User(username="testuser2", email="test2@example.com")
    db.add(test_user1)
    db.add(test_user2)
    db.commit()
    
    yield db  # Run the tests
    
    # Cleanup
    Base.metadata.drop_all(bind=engine)
    db.close()

@pytest.fixture
def client(test_db):
    def override_get_db():
        try:
            yield test_db
        finally:
            test_db.close()
    
    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app) 

@pytest.fixture
def authenticated_client(client, test_db: Session):
    # Create a test token
    test_token = "test_token"
    test_user = User(
        id=3,
        username="testuser"
    )
    test_db.add(test_user)
    test_db.commit()

    # Modify client to include auth header in all requests
    def override_request(*args, **kwargs):
        headers = kwargs.pop('headers', {})
        headers['Authorization'] = f'Bearer {test_token}'
        kwargs['headers'] = headers
        return client.request(*args, **kwargs)

    client.request = override_request
    return client