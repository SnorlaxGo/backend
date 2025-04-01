import asyncio
import websockets
import requests
import json
import random
from datetime import datetime
import sys
import argparse

BLACK = 1
WHITE = 2
STAGING_URL = "go-backend-124a4c405325.herokuapp.com"
STAGING_HTTP_PROTOCOL = "https"
STAGING_WS_PROTOCOL = "wss"

DEVELOPMENT_URL = "localhost:8080"
DEVELOPMENT_HTTP_PROTOCOL = "http"
DEVELOPMENT_WS_PROTOCOL = "ws"

class AutoPlayer:
    def __init__(self, base_url="http://localhost:8080", http_protocol="http", ws_protocol="ws"):
        self.base_url = base_url
        self.token = None
        self.board = None
        self.board_size = 7  # MINI board
        self.my_color = None
        self.game_id = None
        self.port = 8080
        self.http_protocol = http_protocol
        self.ws_protocol = ws_protocol
        
    def login(self, username, password):
        response = requests.post(
            f"{self.http_protocol}://{self.base_url}/token",
            data={"username": username, "password": password}
        )
        if response.status_code == 200:
            self.token = response.json()["access_token"]
            print(f"Logged in successfully as {username}")
            return True
        print(f"Login failed: {response.text}")
        return False
    
    def create_challenge(self):
        headers = {"Authorization": f"Bearer {self.token}"}
        response = requests.post(
            f"{self.http_protocol}://{self.base_url}/challenge/open",
            headers=headers,
            json={
                "boardSize": self.board_size,
                "timeControl": 300  # BLITZ (5 minutes)
            }
        )
        if response.status_code == 200:
            data = response.json()
            if data["status"] == "waiting":
                self.challenge_id = data["challenge_id"]
                print(f"Created challenge {self.challenge_id}, waiting for opponent...")
                return True
            elif data["status"] == "matched":
                self.game_id = data["game_id"]
                self.my_color = data["color"]
                print(f"Immediately matched! Game ID: {self.game_id}, playing as {self.my_color}")
                return True
        print(f"Failed to create challenge: {response.text}")
        return False
    
    def make_move(self, x, y):
        headers = {"Authorization": f"Bearer {self.token}"}
        response = requests.post(
            f"{self.http_protocol}://{self.base_url}/game/{self.game_id}/move",
            headers=headers,
            json={"x": x, "y": y}
        )
        if response.status_code == 200:
            print(f"Made move at ({x}, {y})")
            return True
        print(f"Move failed at ({x}, {y}): {response.text}")
        return False
    
    def print_board(self):
        if not self.board:
            return
        
        print("board:")
        print(self.board)
        
        print("\n  " + " ".join([str(i).rjust(2) for i in range(self.board_size)]))
        for y in range(self.board_size):
            row = str(y).rjust(2) + " "
            for x in range(self.board_size):
                if self.board[y][x] == 1:
                    row += "B "
                elif self.board[y][x] == 2:
                    row += "W "
                else:
                    row += ". "
            print(row)
        print()
    
    def find_empty_space(self):
        empty_spaces = []
        for y in range(self.board_size):
            for x in range(self.board_size):
                if self.board[y][x] == 0:
                    empty_spaces.append((x, y))
        return random.choice(empty_spaces) if empty_spaces else None

    async def play_game(self, test_disconnect=False, disconnect_after=30, reconnect_after=5):
        """
        Play a game, with optional disconnect/reconnect testing.
        
        Args:
            test_disconnect: Whether to test disconnection during the game
            disconnect_after: Seconds to wait before disconnecting
            reconnect_after: Seconds to wait before reconnecting
        """
        if not self.game_id:
            ws_uri = f"{self.ws_protocol}://{self.base_url}/ws/challenge/{self.challenge_id}"
            async with websockets.connect(ws_uri) as websocket:
                print("Connected to challenge websocket, waiting for match...")
                
                while True:
                    message = await websocket.recv()
                    data = json.loads(message)
                    
                    if data["status"] == "matched":
                        self.game_id = data["game_id"]
                        self.my_color = data["color"]
                        print(f"Matched! Game ID: {self.game_id}, playing as {self.my_color}")
                        break
                    elif data["status"] == "expired":
                        print("Challenge expired")
                        return
        
        # Variables to track disconnect testing
        disconnect_timer = None
        should_disconnect = test_disconnect
        forced_disconnect = False
        connection_attempts = 0
        max_connection_attempts = 2  # Initial connection + 1 reconnection attempt
        
        # Now connect to game websocket
        print(f"Connecting to game websocket for game {self.game_id}")
        ws_uri = f"{self.ws_protocol}://{self.base_url}/ws/game/{self.game_id}?token={self.token}"
        
        # Main connection loop - handles initial connection and reconnection
        while connection_attempts < max_connection_attempts:
            connection_attempts += 1
            
            if forced_disconnect:
                print(f"Disconnected. Waiting {reconnect_after} seconds before reconnecting...")
                await asyncio.sleep(reconnect_after)
                print("Attempting to reconnect...")
                forced_disconnect = False
            
            try:
                async with websockets.connect(ws_uri) as websocket:
                    if connection_attempts == 1:
                        print("Connected to game websocket")
                        # Start disconnect timer if testing
                        if should_disconnect:
                            disconnect_timer = datetime.now()
                            print(f"Will disconnect after {disconnect_after} seconds of gameplay")
                    else:
                        print("Reconnected to game websocket")
                    
                    # Game message loop
                    while True:
                        try:
                            # Check if it's time to disconnect for testing
                            if should_disconnect and disconnect_timer and connection_attempts == 1:
                                elapsed = (datetime.now() - disconnect_timer).total_seconds()
                                if elapsed >= disconnect_after:
                                    print(f"Triggering test disconnect after {elapsed:.1f} seconds")
                                    forced_disconnect = True
                                    should_disconnect = False  # Only disconnect once
                                    break  # Exit the websocket context to disconnect
                            
                            message = await websocket.recv()
                            data = json.loads(message)
                            print(f"Received message: {data['type']}")  # Debug print
                            
                            if data["type"] in ["game_abandoned", "timeout", "resign"]:
                                print(f"Game {data['type']}!")
                                print(data)
                                return  # Exit the function completely
                            
                            if data["type"] in ["game_state", 'move']:
                                self.board = data["data"]["board"]
                                self.print_board()
                                
                                # Check if it's our turn
                                is_black_turn = data["data"]["color"] == BLACK
                                print(f"color: {data['data']['color']}, is_black_turn: {is_black_turn}")
                                is_my_turn = (is_black_turn and self.my_color == BLACK) or \
                                            (not is_black_turn and self.my_color == WHITE)
                                
                                print(f"is_my_turn: {is_my_turn}")
                                
                                if is_my_turn:
                                    print("It's my turn!")
                                    await asyncio.sleep(5)  # Delay 6 seconds
                                    
                                    move = self.find_empty_space()
                                    if move:
                                        x, y = move
                                        if self.make_move(x, y):
                                            print(f"Made move at ({x}, {y})")
                                        else:
                                            print("Failed to make move, will try another spot next turn")
                                    else:
                                        print("No empty spaces left!")
                                        return  # Exit the function completely
                            
                            elif data["type"] == "game_over":
                                print("Game over!")
                                print(f"Winner: {data.get('winner', 'unknown')}")
                                return  # Exit the function completely
                            
                        except Exception as e:
                            print(f"Error processing message: {e}")
                            break  # Break out of the inner loop, but may reconnect
                    
                    # If we're not forcing a disconnect for testing, break the outer loop too
                    if not forced_disconnect:
                        break
                    
            except Exception as e:
                print(f"Connection error: {e}")
                
                # If this wasn't a forced disconnect for testing, try to reconnect
                if not forced_disconnect and connection_attempts < max_connection_attempts:
                    print("Connection lost. Attempting to reconnect...")
                    await asyncio.sleep(1)  # Brief delay before reconnection attempt
                else:
                    print("Failed to connect or reconnect")
                    break
        
        print("Game session ended")

async def main():
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Auto player for Go game')
    parser.add_argument('username', help='Username for login')
    parser.add_argument('password', help='Password for login')
    parser.add_argument('--env', choices=['dev', 'staging'], default='dev', help='Environment to connect to')
    parser.add_argument('--test-disconnect', action='store_true', help='Test disconnect/reconnect during game')
    parser.add_argument('--disconnect-after', type=int, default=30, help='Seconds to wait before disconnecting')
    parser.add_argument('--reconnect-after', type=int, default=5, help='Seconds to wait before reconnecting')
    
    args = parser.parse_args()
    
    # Set up the player based on environment
    if args.env == 'staging':
        player = AutoPlayer(base_url=STAGING_URL, http_protocol=STAGING_HTTP_PROTOCOL, ws_protocol=STAGING_WS_PROTOCOL)
    else:
        player = AutoPlayer(base_url=DEVELOPMENT_URL, http_protocol=DEVELOPMENT_HTTP_PROTOCOL, ws_protocol=DEVELOPMENT_WS_PROTOCOL)
    
    # Login
    if not player.login(args.username, args.password):
        print("Login failed, exiting")
        return
    
    # Create a challenge
    if not player.create_challenge():
        print("Failed to create challenge, exiting")
        return
    
    # Play the game with optional disconnect testing
    await player.play_game(
        test_disconnect=args.test_disconnect,
        disconnect_after=args.disconnect_after,
        reconnect_after=args.reconnect_after
    )

if __name__ == "__main__":
    asyncio.run(main())