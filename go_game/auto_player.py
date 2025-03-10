import asyncio
import websockets
import requests
import json
import random
from datetime import datetime
import sys

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

    async def play_game(self):
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
        
        # Now connect to game websocket
        print(f"Connecting to game websocket for game {self.game_id}")
        ws_uri = f"{self.ws_protocol}://{self.base_url}/ws/game/{self.game_id}?token={self.token}"
        async with websockets.connect(ws_uri) as websocket:
            print("Connected to game websocket")
            
            while True:
                try:
                    message = await websocket.recv()
                    data = json.loads(message)
                    print(f"Received message: {data['type']}")  # Debug print
                    if data["type"] == "draw_offer":
                        print("Draw offer received!")
                        # Accept the draw offer
                        accept_draw_url = f"{self.http_protocol}://{self.base_url}/game/{self.game_id}/accept_draw"
                        headers = {"Authorization": f"Bearer {self.token}"}
                        response = requests.post(accept_draw_url, headers=headers)
                        if response.status_code == 200:
                            print("Draw offer accepted!")
                        else:
                            print(f"Failed to accept draw offer: {response.status_code}")
                        break
                    if data["type"] in ["game_abandoned", "timeout", "resign"]:
                        print(f"Game {data['type']}!")
                        break
                    if data["type"] in ["game_state", 'move']:
                        self.board = data["data"]["board"]
                        self.print_board()
                        
                        # Check if it's our turn
                        is_black_turn = data["data"]["color"] == BLACK
                        print(self.my_color)
                        print(f"color: {data['data']['color']}, is_black_turn: {is_black_turn}")
                        is_my_turn = (is_black_turn and self.my_color == BLACK) or \
                                    (not is_black_turn and self.my_color == WHITE)
                        
                        print(f"is_my_turn: {is_my_turn}")
                        
                        if is_my_turn:
                            print("It's my turn!")
                            await asyncio.sleep(1)  # Delay 1 second
                            
                            move = self.find_empty_space()
                            if move:
                                x, y = move
                                if self.make_move(x, y):
                                    print(f"Made move at ({x}, {y})")
                                else:
                                    print("Failed to make move, will try another spot next turn")
                            else:
                                print("No empty spaces left!")
                                break
                    
                    elif data["type"] == "game_over":
                        print("Game over!")
                        print(f"Winner: {data.get('winner', 'unknown')}")
                        break
                    
                    elif data["type"] == "move":
                        self.board = data["board"]
                        self.print_board()
                        # ... handle move updates ...
                except Exception as e:
                    print(f"Error processing message: {e}")
                    break

async def main():
    #player = AutoPlayer(base_url=STAGING_URL, http_protocol=STAGING_HTTP_PROTOCOL, ws_protocol=STAGING_WS_PROTOCOL)
    player = AutoPlayer(base_url=DEVELOPMENT_URL, http_protocol=DEVELOPMENT_HTTP_PROTOCOL, ws_protocol=DEVELOPMENT_WS_PROTOCOL)
    if player.login(sys.argv[1], sys.argv[2]):
        if player.create_challenge():
            await player.play_game()

if __name__ == "__main__":
    asyncio.run(main())