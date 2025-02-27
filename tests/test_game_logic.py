import unittest
from go_game.game_logic import process_move, has_liberties, get_connected_stones, is_ko_violation, is_suicide_move
from go_game.models import StoneColor, Move

class TestGameLogic(unittest.TestCase):
    def setUp(self):
        # Create a 7x7 board matching the scenario
        self.board = [[0 for _ in range(7)] for _ in range(7)]
        
        # Set up the board state:
        # Black stones (●) at (0,1), (1,2), (0,3), (2,1)
        # White stones (○) at (1,1), (6,3), (5,2)
        self.board[6][0] = StoneColor.BLACK.value  # a1
        self.board[6][1] = StoneColor.WHITE.value  # b1
        self.board[6][2] = StoneColor.BLACK.value  # c1
        self.board[5][5] = StoneColor.WHITE.value  # f2
        self.board[4][0] = StoneColor.BLACK.value  # a3
        self.board[3][6] = StoneColor.WHITE.value  # g3

    def test_white_stone_should_be_captured(self):
        """Test that White stone at b1 is captured when surrounded"""
        print("\nInitial board:")
        self.print_board(self.board)
        
        result = process_move(self.board, 1, 5, StoneColor.BLACK)
        new_board = result["board"]
        captured = result["captured"]
        
        print("\nAfter capture:")
        self.print_board(new_board)
        
        # Should capture exactly one stone at b1
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0], (1, 6))  # b1 coordinates
        self.assertEqual(new_board[6][1], 0)

    def test_has_liberties_surrounded_stone(self):
        """Test that a surrounded stone has no liberties"""
        # Check White stone at b1
        self.board[5][1] = StoneColor.BLACK.value
        has_libs = has_liberties(self.board, 1, 6)
        self.board[5][1] = 0
        self.assertFalse(has_libs)

    def test_has_liberties_edge_stone(self):
        """Test that a stone on the edge can still have liberties"""
        # Check Black stone at a1
        has_libs = has_liberties(self.board, 0, 6)
        self.assertTrue(has_libs)

    def test_get_connected_stones_single(self):
        """Test getting connected stones for an isolated stone"""
        # White stone at b1 should be alone
        connected = get_connected_stones(self.board, 1, 6)
        self.assertEqual(len(connected), 1)
        self.assertEqual(connected[0], (1, 6))

    def print_board(self, board):
        """Helper method to print the board state for debugging"""
        from go_game.utils.board_visualizer import visualize_board
        print(visualize_board(board))

    def test_ko_rule_violation(self):
        """Test that ko rule prevents immediate recapture"""
        # Set up a ko situation
        test_board = [[0 for _ in range(7)] for _ in range(7)]
        
        # Place stones to create ko position:
        #    d e f
        # 3  ○ ● ○
        # 4  ● . ●  <- White captures at 'e4'
        # 5  ○ ● ○
        
        test_board[3][3] = StoneColor.WHITE.value  # d3
        test_board[3][4] = StoneColor.BLACK.value  # e3
        test_board[3][5] = StoneColor.WHITE.value  # f3
        test_board[4][3] = StoneColor.BLACK.value  # d4
        test_board[4][4] = StoneColor.WHITE.value  # e4 - this is the stone that will be captured
        #test_board[4][5] = StoneColor.BLACK.value  # f4
        test_board[4][2] = StoneColor.WHITE.value  # f4
        test_board[5][3] = StoneColor.WHITE.value  # d5
        test_board[5][4] = StoneColor.BLACK.value  # e5
        test_board[5][5] = StoneColor.WHITE.value  # f5

        print("\nInitial ko position:")
        self.print_board(test_board)
        
        # Black captures white stone at e4
        result = process_move(test_board, 5, 4, StoneColor.BLACK)
        test_board = result["board"]
        
        print("\nAfter Black capture:")
        self.print_board(test_board)
        
        # White should not be able to immediately recapture
        last_move = Move(
            x=5, 
            y=4, 
            color=StoneColor.BLACK,
            captured_positions=[(4, 4)]  # The position that was captured
        )
        
        is_ko = is_ko_violation(test_board, last_move, 4, 4, StoneColor.WHITE)
        self.assertTrue(is_ko)

    def test_eye_capture_prevention(self):
        """Test various eye-related scenarios: suicide moves, double eyes preventing capture,
        and successful eye captures when the group is fully surrounded"""
        test_board = [[0 for _ in range(7)] for _ in range(7)]
        
        # Test 1: Basic eye suicide scenario
        # Create black group with eye:
        #    c d e
        # 3  ● ● ●
        # 4  ● . ●
        # 5  ● ● ●
        
        test_board[3][2] = StoneColor.BLACK.value  # c3
        test_board[3][3] = StoneColor.BLACK.value  # d3
        test_board[3][4] = StoneColor.BLACK.value  # e3
        test_board[4][2] = StoneColor.BLACK.value  # c4
        test_board[4][4] = StoneColor.BLACK.value  # e4
        test_board[5][2] = StoneColor.BLACK.value  # c5
        test_board[5][3] = StoneColor.BLACK.value  # d5
        test_board[5][4] = StoneColor.BLACK.value  # e5

        print("\nBasic eye position:")
        self.print_board(test_board)
        
        # White trying to play in the eye should be suicide
        is_suicide = is_suicide_move(test_board, 3, 4, StoneColor.WHITE)
        self.assertTrue(is_suicide)
        
        # Test 2: Double eye prevents capture
        test_board = [[0 for _ in range(7)] for _ in range(7)]
        
        # Create black group with two eyes:
        #    b c d e f
        # 2  ● ● ● ● ●
        # 3  ● . ● . ●
        # 4  ● ● ● ● ●
        
        for x in range(1, 6):  # b-f
            test_board[2][x] = StoneColor.BLACK.value  # row 2
            test_board[4][x] = StoneColor.BLACK.value  # row 4
        test_board[3][1] = StoneColor.BLACK.value  # b3
        test_board[3][3] = StoneColor.BLACK.value  # d3
        test_board[3][5] = StoneColor.BLACK.value  # f3

        print("\nDouble eye position:")
        self.print_board(test_board)
        
        # Surround the group with white stones
        for x in range(0, 7):
            if x not in [2, 4]:  # Skip the eyes
                test_board[3][x] = StoneColor.WHITE.value
        
        # Group should still have liberties due to double eyes
        has_libs = has_liberties(test_board, 1, 2)  # Check from b2
        self.assertTrue(has_libs)
        
        # Test 3: Successful eye capture
        test_board = [[0 for _ in range(7)] for _ in range(7)]
        
        # Create black group surrounded by white:
        #    c d e
        # 3  ● ● ●
        # 4  ● . ●
        # 5  ● ● ●
        # All adjacent spaces are white
        
        # Place black stones
        for coords in [(2,3), (3,3), (4,3), (2,4), (4,4), (2,5), (3,5), (4,5)]:
            test_board[coords[0]][coords[1]] = StoneColor.BLACK.value
            
        # Surround with white stones
        for coords in [(1,2), (2,2), (3,2), (4,2), (5,2),  # Top
                      (1,3), (5,3),  # Sides
                      (1,4), (5,4),
                      (1,5), (5,5),
                      (1,6), (2,6), (3,6), (4,6), (5,6)]:  # Bottom
            test_board[coords[0]][coords[1]] = StoneColor.WHITE.value

        print("\nSurrounded group position:")
        self.print_board(test_board)
        
        # White should be able to capture by playing in the eye
        result = process_move(test_board, 4, 3, StoneColor.WHITE)
        self.print_board(result["board"])
        self.assertTrue(len(result["captured"]) > 0)

if __name__ == "__main__":
    unittest.main()
