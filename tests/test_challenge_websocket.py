import pytest
from unittest.mock import patch, MagicMock
from go_game.models import Challenge, ChallengeStatus, GameStatus, TimeControl
from go_game.models import Game
from datetime import timedelta
from go_game.auth import create_access_token
import time

@pytest.fixture
def test_challenge(db, test_user):
    # Create a test challenge with unique values
    import uuid
    unique_id = str(uuid.uuid4())[:8]
    challenge = Challenge(
        challenger_id=test_user.id,
        board_size=19,
        time_control=TimeControl.BLITZ,
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
        mock_redis_client.publish = MagicMock()
        mock_redis_client.pubsub.return_value.subscribe = MagicMock()
        mock_redis_client.pubsub.return_value.run = MagicMock()
        mock.return_value = mock_redis_client
        yield mock

def test_challenge_timeout(db, test_client, test_challenge, test_user):
    """Test that a challenge times out after the specified period."""
    
    # Print challenge details for debugging
    print(f"Testing challenge with ID: {test_challenge.id}")
    print(f"Challenge creator: {test_challenge.challenger_id}")
    print(f"Challenge status: {test_challenge.status}")
    
    # Create an access token for the test user
    access_token = create_access_token(
        data={"sub": str(test_user.id)}, 
        expires_delta=timedelta(minutes=30)
    )
    
    # Patch the sleep function to speed up the test
    with patch('asyncio.sleep', return_value=None):
        # Use the test_client to connect to the websocket with authentication
        with test_client.websocket_connect(
            f"/ws/challenge/{test_challenge.id}",
            headers={"Authorization": f"Bearer {access_token}"}
        ) as websocket:
            # First message should be "waiting"
            response = websocket.receive_json()
            assert response["status"] == ChallengeStatus.WAITING
            
            # Wait for timeout
            response = websocket.receive_json()
            assert response["status"] == ChallengeStatus.EXPIRED
        
        # Verify challenge was deleted
        challenge = db.query(Challenge).filter(Challenge.id == test_challenge.id).first()
        assert challenge is None

def test_challenge_matched(db, test_client, test_challenge, test_user, test_opponent):
    """Test that a challenge can be matched."""
    
    # Patch the sleep function
    with patch('asyncio.sleep', return_value=None):
        # Use the test_client to connect to the websocket
        with test_client.websocket_connect(f"/ws/challenge/{test_challenge.id}") as websocket:
            # First message should be "waiting"
            response = websocket.receive_json()
            assert response["status"] == ChallengeStatus.WAITING
            
            # Update challenge to matched
            challenge = db.query(Challenge).filter(Challenge.
            id == test_challenge.id).first()
            challenge.status = ChallengeStatus.MATCHED
            
            
            # Create a game for the challenge
            game = Game(
                black_player_id=test_user.id,
                white_player_id=test_opponent.id,  # Use the test_opponent fixture
                board_size=19,
                status=GameStatus.ACTIVE
            )
            db.add(game)
            db.commit()

            time.sleep(2)
            # Next message should be "matched"
            response = websocket.receive_json()
            print(response)
            assert response["status"] == ChallengeStatus.MATCHED
            assert "game_id" in response

if __name__ == "__main__":
    import sys
    
    # Check if there are any command-line arguments
    if len(sys.argv) > 1 and sys.argv[1] == "-k":
        # Run only the specified test
        test_name = sys.argv[2]
        pytest.main([__file__, '-k', test_name, '-v'])
    else:
        # Run all tests in this file
        pytest.main([__file__, '-v'])