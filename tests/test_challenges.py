import unittest
from unittest.mock import MagicMock, patch
from fastapi.websockets import WebSocketDisconnect
from go_game.models import Challenge

class TestChallenges(unittest.TestCase):
    def setUp(self):
        self.client = self.authenticated_client

    def test_create_direct_challenge(self):
        response = self.authenticated_client.post(
            "/challenge/direct",
            json={
                "board_size": 19,
                "time_control": 30,
                "challenged_user_id": 2
            }
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("game_id", data)
        self.assertEqual(data["status"], "challenge_sent")

    def test_create_open_challenge(self):
        # Create first open challenge
        response1 = self.authenticated_client.post(
            "/challenge/open",
            json={
                "board_size": 19,
                "time_control": 30
            }
        )
        self.assertEqual(response1.status_code, 200)
        data1 = response1.json()
        self.assertEqual(data1["status"], "waiting")
        
        # Create second matching challenge
        response2 = self.authenticated_client.post(
            "/challenge/open",
            json={
                "board_size": 19,
                "time_control": 30
            }
        )
        self.assertEqual(response2.status_code, 200)
        data2 = response2.json()
        self.assertEqual(data2["status"], "matched")
        self.assertIn("game_id", data2)

    def test_accept_challenge(self):
        # First create a challenge
        challenge = Challenge(
            challenger_id=1,
            board_size=19,
            time_control=30,
            status="pending"
        )
        self.test_db.add(challenge)
        self.test_db.commit()
        
        response = self.authenticated_client.post(f"/challenge/{challenge.id}/accept")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "game_created")
        self.assertIn("game_id", data)

    def test_accept_nonexistent_challenge(self):
        response = self.authenticated_client.post("/challenge/999/accept")
        self.assertEqual(response.status_code, 404)

    def test_accept_already_matched_challenge(self):
        # Create an already matched challenge
        challenge = Challenge(
            challenger_id=1,
            board_size=19,
            time_control=30,
            status="matched"
        )
        self.test_db.add(challenge)
        self.test_db.commit()
        
        response = self.authenticated_client.post(f"/challenge/{challenge.id}/accept")
        self.assertEqual(response.status_code, 400)

    def test_anonymous_challenge_matching(self):
        # First anonymous player creates a challenge
        response1 = self.client.post(
            "/anonymous/challenge",
            json={"board_size": 13, "time_control": 600}
        )
        self.assertEqual(response1.status_code, 200)
        data1 = response1.json()
        self.assertEqual(data1["status"], "waiting")
        self.assertIn("player_id", data1)
        self.assertEqual(data1["color"], "black")

        # Second anonymous player creates a matching challenge
        response2 = self.client.post(
            "/anonymous/challenge",
            json={"board_size": 13, "time_control": 600}
        )
        self.assertEqual(response2.status_code, 200)
        data2 = response2.json()
        self.assertEqual(data2["status"], "matched")
        self.assertIn("game_id", data2)
        self.assertIn("player_id", data2)
        self.assertEqual(data2["color"], "white")

    def test_challenge_status_websocket(self):
        # First create a challenge
        response = self.client.post(
            "/anonymous/challenge",
            json={"board_size": 13, "time_control": 600}
        )
        data = response.json()
        challenge_id = data["challenge_id"]
        
        # Set up the websocket mock
        mock_websocket = MagicMock()
        mock_websocket.receive_json.side_effect = WebSocketDisconnect()
        mock_manager = MagicMock()
        
        # Test the status check
        with patch('go_game.main.get_db') as mock_db:
            # Mock the database session and query
            mock_session = MagicMock()
            mock_db.return_value = iter([mock_session])
            
            # Mock the challenge query
            mock_challenge = MagicMock()
            mock_challenge.id = challenge_id
            mock_challenge.challenger_id = data["player_id"]
            mock_challenge.status = "matched"
            mock_session.query.return_value.filter.return_value.first.return_value = mock_challenge
            
            # Mock the game query
            mock_game = MagicMock()
            mock_game.id = 1
            mock_game.black_player_id = data["player_id"]
            mock_session.query.return_value.filter.return_value.order_by.return_value.first.return_value = mock_game