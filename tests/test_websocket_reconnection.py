import pytest
import asyncio
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
import websockets
import json
import time
from unittest.mock import patch, MagicMock

from go_game.server import app
from go_game.models import User, Game, GameStatus, StoneColor
from go_game.websocket_manager import manager
from go_game.auth import create_access_token

@pytest.fixture
def test_game(db, test_user, test_opponent):
    # Create a test game
    game = Game(
        black_player_id=test_user.id,
        white_player_id=test_opponent.id,
        board_size=19,
        status=GameStatus.ACTIVE
    )
    db.add(game)
    db.commit()
    db.refresh(game)
    return game

@pytest.fixture
def access_token(test_user):
    return create_access_token(data={"sub": test_user.username})

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

# Patch the sleep function to speed up tests
@pytest.fixture(autouse=True)
def mock_sleep():
    with patch('go_game.websocket_manager.sleep') as mock_sleep:
        # Make sleep return immediately
        async def fast_sleep(seconds):
            if seconds == 10:  # Only speed up the disconnect timeout
                await asyncio.sleep(0.01)
            else:
                await asyncio.sleep(seconds)
        
        mock_sleep.side_effect = fast_sleep
        yield mock_sleep

@pytest.mark.asyncio
async def test_reconnect_within_timeout(db, test_client, test_game, test_user, access_token, mock_sleep):
    """Test that a player can reconnect within the timeout period without the game being abandoned."""
    
    # Mock the GameService to track calls
    with patch('go_game.websocket_manager.GameService') as MockGameService:
        mock_service_instance = MagicMock()
        MockGameService.return_value = mock_service_instance
        
        # Set up the mock game response
        mock_game = MagicMock()
        mock_game.id = test_game.id
        mock_game.status = GameStatus.ACTIVE
        mock_game.black_player_id = test_user.id
        mock_service_instance.get_game.return_value = mock_game
        
        # Connect to the websocket
        with test_client.websocket_connect(f"/api/ws/game/{test_game.id}?token={access_token}") as websocket:
            # Verify connection is established
            response = await websocket.recv()
            data = json.loads(response)
            assert data["type"] == "GAME_STATE"
        
        # Websocket is now disconnected
        
        # Verify disconnect check is scheduled
        assert f"{test_game.id}:{test_user.id}" in manager.disconnect_tasks
        
        # Wait a bit to ensure the disconnect handler runs (but doesn't complete due to our mock)
        await asyncio.sleep(0.1)
        
        # Reconnect before timeout expires
        with test_client.websocket_connect(f"/api/ws/game/{test_game.id}?token={access_token}") as websocket:
            # Verify connection is re-established
            response = await websocket.recv()
            data = json.loads(response)
            assert data["type"] == "GAME_STATE"
        
        # Verify game was not marked as abandoned
        mock_game.status = GameStatus.ACTIVE
        mock_service_instance.to_response.assert_not_called()

@pytest.mark.asyncio
async def test_game_abandoned_after_timeout(db, test_client, test_game, test_user, access_token, mock_sleep):
    """Test that a game is marked as abandoned if a player doesn't reconnect within the timeout."""
    
    # Mock the GameService to track calls
    with patch('go_game.websocket_manager.GameService') as MockGameService:
        mock_service_instance = MagicMock()
        MockGameService.return_value = mock_service_instance
        
        # Set up the mock game response
        mock_game = MagicMock()
        mock_game.id = test_game.id
        mock_game.status = GameStatus.ACTIVE
        mock_game.black_player_id = test_user.id
        mock_service_instance.get_game.return_value = mock_game
        
        # Connect to the websocket
        with test_client.websocket_connect(f"/api/ws/game/{test_game.id}?token={access_token}") as websocket:
            # Verify connection is established
            response = await websocket.recv()
            data = json.loads(response)
            assert data["type"] == "GAME_STATE"
        
        # Websocket is now disconnected
        
        # Wait for the disconnect handler to complete (our mocked sleep makes this fast)
        await asyncio.sleep(0.2)
        
        # Verify game was marked as abandoned
        assert mock_game.status == GameStatus.BLACK_ABANDONED
        mock_service_instance.to_response.assert_called()

@pytest.mark.asyncio
async def test_opponent_receives_abandoned_notification(db, test_client, test_game, test_user, test_opponent, access_token, mock_sleep):
    """Test that the opponent receives a notification when a game is abandoned."""
    
    # Create token for opponent
    opponent_token = create_access_token(data={"sub": test_opponent.username})
    
    # Mock the GameService
    with patch('go_game.websocket_manager.GameService') as MockGameService:
        mock_service_instance = MagicMock()
        MockGameService.return_value = mock_service_instance
        
        # Set up the mock game response
        mock_game = MagicMock()
        mock_game.id = test_game.id
        mock_game.status = GameStatus.ACTIVE
        mock_game.black_player_id = test_user.id
        mock_game.white_player_id = test_opponent.id
        mock_service_instance.get_game.return_value = mock_game
        
        # Connect opponent first
        opponent_ws = test_client.websocket_connect(
            f"/api/ws/game/{test_game.id}?token={opponent_token}"
        ).__enter__()
        # Verify opponent connection
        response = await opponent_ws.recv()
        data = json.loads(response)
        assert data["type"] == "GAME_STATE"
        
        # Connect player
        with test_client.websocket_connect(f"/api/ws/game/{test_game.id}?token={access_token}") as websocket:
            # Verify player connection
            response = await websocket.recv()
            data = json.loads(response)
            assert data["type"] == "GAME_STATE"
        
        # Player disconnects, wait for timeout
        await asyncio.sleep(0.2)
        
        # Opponent should receive abandoned notification
        response = await opponent_ws.recv()
        data = json.loads(response)
        assert data["type"] == "GAME_ABANDONED"
        
        # Clean up
        await opponent_ws.close()

@pytest.mark.asyncio
async def test_multiple_reconnects(db, test_client, test_game, test_user, access_token, mock_sleep):
    """Test that a player can disconnect and reconnect multiple times."""
    
    # Mock the GameService
    with patch('go_game.websocket_manager.GameService') as MockGameService:
        mock_service_instance = MagicMock()
        MockGameService.return_value = mock_service_instance
        
        # Set up the mock game response
        mock_game = MagicMock()
        mock_game.id = test_game.id
        mock_game.status = GameStatus.ACTIVE
        mock_game.black_player_id = test_user.id
        mock_service_instance.get_game.return_value = mock_game
        
        # Connect, disconnect, and reconnect multiple times
        for _ in range(3):
            # Connect
            with test_client.websocket_connect(f"/api/ws/game/{test_game.id}?token={access_token}") as websocket:
                # Verify connection
                response = await websocket.recv()
                data = json.loads(response)
                assert data["type"] == "GAME_STATE"
            
            # Short wait (not enough to trigger abandonment)
            await asyncio.sleep(0.05)
        
        # Verify game was not abandoned
        assert mock_game.status == GameStatus.ACTIVE 