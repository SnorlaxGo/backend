import pytest
import asyncio
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
import websockets
import json
from unittest.mock import patch, MagicMock

from go_game.server import app
from go_game.models import User, Challenge, ChallengeStatus, GameStatus
from go_game.websocket_manager import challenge_manager

@pytest.fixture
def test_challenge(db, test_user):
    # Create a test challenge with unique values
    import uuid
    unique_id = str(uuid.uuid4())[:8]
    challenge = Challenge(
        challenger_id=test_user.id,
        board_size=19,
        time_control="REAL_TIME",
        status=ChallengeStatus.WAITING
    )
    db.add(challenge)
    db.commit()
    db.refresh(challenge)
    return challenge

# Mock Redis for testing
@pytest.fixture(autouse=True)
def mock_redis():
    with patch('go_game.websocket_manager.redis.from_url') as mock:
        # Create a mock Redis client
        mock_redis_client = MagicMock()
        mock_redis_client.publish = MagicMock(return_value=asyncio.Future())
        mock_redis_client.pubsub.return_value.subscribe = MagicMock(return_value=asyncio.Future())
        mock_redis_client.pubsub.return_value.run = MagicMock(return_value=asyncio.Future())
        mock.return_value = asyncio.Future()
        mock.return_value.set_result(mock_redis_client)
        yield mock

@pytest.mark.asyncio
async def test_challenge_timeout(db, test_client, test_challenge):
    """Test that a challenge times out after the specified period."""
    
    # Patch the sleep function to speed up the test
    with patch('asyncio.sleep') as mock_sleep:
        mock_sleep.side_effect = lambda x: asyncio.sleep(0.01)
        
        # Connect to the challenge websocket
        async with websockets.connect(
            f"ws://testserver/api/ws/challenge/{test_challenge.id}"
        ) as websocket:
            # First message should be "waiting"
            response = await websocket.recv()
            data = json.loads(response)
            assert data["status"] == ChallengeStatus.WAITING
            
            # Wait for timeout
            response = await websocket.recv()
            data = json.loads(response)
            assert data["status"] == ChallengeStatus.EXPIRED
        
        # Verify challenge was deleted
        challenge = db.query(Challenge).filter(Challenge.id == test_challenge.id).first()
        assert challenge is None

@pytest.mark.asyncio
async def test_challenge_matched(db, test_client, test_challenge, test_user):
    """Test that a challenge can be matched."""
    
    # Patch the sleep function
    with patch('asyncio.sleep') as mock_sleep:
        mock_sleep.side_effect = lambda x: asyncio.sleep(0.01)
        
        # Connect to the challenge websocket
        ws_task = asyncio.create_task(
            websockets.connect(f"ws://testserver/api/ws/challenge/{test_challenge.id}")
        )
        
        # Wait a bit for the connection to establish
        await asyncio.sleep(0.1)
        websocket = await ws_task
        
        # First message should be "waiting"
        response = await websocket.recv()
        data = json.loads(response)
        assert data["status"] == ChallengeStatus.WAITING
        
        # Update challenge to matched
        challenge = db.query(Challenge).filter(Challenge.id == test_challenge.id).first()
        challenge.status = ChallengeStatus.MATCHED
        
        # Create a game for the challenge
        game = Game(
            black_player_id=test_user.id,
            white_player_id=test_user.id + 1,  # Some other user
            board_size=19,
            status=GameStatus.ACTIVE
        )
        db.add(game)
        db.commit()
        
        # Next message should be "matched"
        response = await websocket.recv()
        data = json.loads(response)
        assert data["status"] == ChallengeStatus.MATCHED
        assert "game_id" in data
        
        # Clean up
        await websocket.close() 