import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.websockets import WebSocket, WebSocketDisconnect

@pytest.fixture
def mock_websocket():
    websocket = AsyncMock(spec=WebSocket)
    websocket.receive_json = AsyncMock()
    websocket.send_json = AsyncMock()
    websocket.accept = AsyncMock()
    websocket.close = AsyncMock()
    return websocket

@pytest.fixture
def mock_manager():
    with patch('go_game.main.manager') as mock:
        mock.connect = AsyncMock()
        mock.disconnect = AsyncMock()
        mock.broadcast_to_game = AsyncMock()
        yield mock

@pytest.mark.asyncio
async def test_websocket_connection(mock_websocket):
    # Import inside the test to ensure the mock is applied
    with patch('go_game.main.manager') as mock_manager:
        # Setup the mock with AsyncMock for async methods
        mock_manager.connect = AsyncMock()
        mock_manager.broadcast_to_game = AsyncMock()
        mock_manager.disconnect = AsyncMock()  # Changed from MagicMock to AsyncMock
        
        from go_game.main import handle_game_socket
        
        # Set up the websocket to disconnect after connection
        mock_websocket.receive_json.side_effect = WebSocketDisconnect()
        
        # Call the actual handler
        await handle_game_socket(mock_websocket, 1)
                # Debug prints
        # Verify the connection was handled by the manager
        mock_manager.connect.assert_called_once_with(mock_websocket, 1)
        # Verify disconnect was called when the WebSocketDisconnect was raised
        mock_manager.disconnect.assert_called_once_with(mock_websocket, 1)

@pytest.mark.asyncio
async def test_game_moves(mock_websocket, mock_manager):
    # Set up the receive_json to first return a move, then raise WebSocketDisconnect
    mock_websocket.receive_json.side_effect = [
        {
            "type": "move",
            "x": 3,
            "y": 3,
            "color": "black"
        },
        WebSocketDisconnect()  # This will break the infinite loop
    ]
    
    with patch('go_game.main.make_move', return_value=True), \
         patch('go_game.main.get_game_state', return_value={"board": []}):
        
        from go_game.main import handle_game_socket
        await handle_game_socket(mock_websocket, 1)
        
        # Verify the broadcast was called before the disconnect
        mock_manager.broadcast_to_game.assert_any_call(
            1,
            {
                "type": "game_state",
                "data": {"board": []}
            }
        )

@pytest.mark.asyncio
async def test_invalid_move(mock_websocket, mock_manager):
    mock_websocket.receive_json.side_effect = [
        {
            "type": "move",
            "x": 3,
            "y": 3,
            "color": "black"
        },
        WebSocketDisconnect()  # Break the loop after first move
    ]
    
    with patch('go_game.main.make_move', return_value=False):
        from go_game.main import handle_game_socket
        await handle_game_socket(mock_websocket, 1)
        
        mock_websocket.send_json.assert_called_with({
            "type": "error",
            "message": "Invalid move"
        })

@pytest.mark.asyncio
async def test_multiple_clients():
    # Create mock websockets with async methods
    websocket1 = AsyncMock(spec=WebSocket)
    websocket2 = AsyncMock(spec=WebSocket)
    
    # Import and use the actual ConnectionManager class
    from go_game.main import ConnectionManager
    
    # Create a new manager instance
    manager = ConnectionManager()
    
    # Add the websockets to the manager's active connections
    manager.active_connections[1] = [websocket1, websocket2]
    
    # Broadcast a message
    message = {
        "type": "game_state",
        "data": {"board": []}
    }
    
    # Use the actual broadcast method
    await manager.broadcast_to_game(1, message)
    
    # Verify both websockets received the message
    websocket1.send_json.assert_called_once_with(message)
    websocket2.send_json.assert_called_once_with(message)

@pytest.mark.asyncio
async def test_disconnect_notification(mock_websocket, mock_manager):
    mock_websocket.receive_json.side_effect = WebSocketDisconnect()
    
    from go_game.main import handle_game_socket
    await handle_game_socket(mock_websocket, 1)
    
    mock_manager.disconnect.assert_called_once_with(mock_websocket, 1)
    mock_manager.broadcast_to_game.assert_called_once_with(
        1,
        {
            "type": "player_disconnect",
            "message": "A player has disconnected"
        }
    )

@pytest.mark.asyncio
async def test_challenge_status_websocket_match(mock_websocket, mock_manager):
    """Test that websocket correctly notifies when a challenge is matched"""
    with patch('go_game.main.get_db') as mock_db:
        # Mock the database session
        mock_session = MagicMock()
        mock_db.return_value = iter([mock_session])
        
        # Mock a matched challenge
        mock_challenge = MagicMock()
        mock_challenge.id = 1
        mock_challenge.challenger_id = 100
        mock_challenge.status = "matched"
        mock_session.query.return_value.filter.return_value.first.return_value = mock_challenge
        
        # Mock the associated game
        mock_game = MagicMock()
        mock_game.id = 1
        mock_game.black_player_id = 100  # Same as challenger_id
        mock_session.query.return_value.filter.return_value.order_by.return_value.first.return_value = mock_game
        
        # Set up websocket to disconnect after receiving message
        mock_websocket.receive_json.side_effect = WebSocketDisconnect()
        
        # Call the websocket handler
        from go_game.main import challenge_status
        await challenge_status(mock_websocket, 1)
        
        # Verify the match notification was sent
        mock_websocket.send_json.assert_called_with({
            "type": "matched",
            "game_id": 1,
            "color": "black"
        })

@pytest.mark.asyncio
async def test_challenge_status_websocket_not_found(mock_websocket, mock_manager):
    """Test handling of non-existent challenges"""
    with patch('go_game.main.get_db') as mock_db:
        mock_session = MagicMock()
        mock_db.return_value = iter([mock_session])
        
        # Mock no challenge found
        mock_session.query.return_value.filter.return_value.first.return_value = None
        
        # Call the websocket handler
        from go_game.main import challenge_status
        await challenge_status(mock_websocket, 999)
        
        # Verify error message was sent
        mock_websocket.send_json.assert_called_with({
            "type": "error",
            "message": "Challenge not found"
        })

@pytest.mark.asyncio
async def test_challenge_status_websocket_waiting(mock_websocket, mock_manager):
    """Test behavior while waiting for a match"""
    with patch('go_game.main.get_db') as mock_get_db:
        # Create a mock session that can be used as an async context manager
        mock_session = MagicMock()
        mock_session.__iter__ = lambda _: iter([mock_session])
        mock_get_db.return_value = mock_session
        
        # Mock an open challenge
        mock_challenge = MagicMock()
        mock_challenge.status = "open"
        mock_session.query.return_value.filter.return_value.first.return_value = mock_challenge
        
        # Set up websocket to disconnect after one check
        mock_websocket.receive_json.side_effect = WebSocketDisconnect()
        
        # Call the websocket handler
        from go_game.main import challenge_status
        await challenge_status(mock_websocket, 1)
        
        # Verify no match message was sent
        for call in mock_websocket.send_json.call_args_list:
            args = call[0][0]
            assert args.get("type") != "matched"

@pytest.mark.asyncio
async def test_challenge_status_websocket_disconnect(mock_websocket, mock_manager):
    """Test proper cleanup on websocket disconnect"""
    with patch('go_game.main.get_db') as mock_db:
        mock_session = MagicMock()
        mock_db.return_value = iter([mock_session])
        
        # Force a disconnect
        mock_websocket.receive_json.side_effect = WebSocketDisconnect()
        
        # Call the websocket handler
        from go_game.main import challenge_status
        await challenge_status(mock_websocket, 1)
        
        # Verify disconnect was handled
        mock_manager.disconnect.assert_called_once_with(
            mock_websocket, 
            "challenge_1"  # Note the prefix for challenge connections
        )
