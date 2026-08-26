import socket
import chess
import chess.engine

class ChessRobot:
    """Handles all socket communication and physical math for RobotStudio."""
    def __init__(self, host="192.168.125.1", port=5000, square_size_mm=42.1):
        self.square_size = square_size_mm
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.sock.connect((host, port))
            print("Connected to GoFa!")
        except Exception as e:
            print(f"Warning: Could not connect to RobotStudio. {e}")

    def square_to_coords(self, square_str):
        file_char = square_str[0].lower()
        rank_char = square_str[1]
        file_index = ord('h') - ord(file_char)
        rank_index = 8 - int(rank_char)
        half_square = self.square_size / 2.0
        x_mm = (file_index * self.square_size) + half_square
        y_mm = (rank_index * self.square_size) + half_square
        return float(x_mm), float(y_mm)

    def send_action(self, action_type, x_mm, y_mm):
        cmd_str = f"[{action_type}, {x_mm:.1f}, {y_mm:.1f}]"
        try:
            self.sock.sendall(cmd_str.encode('utf-8'))
            response = self.sock.recv(1024).decode('utf-8')
            if response != "DONE":
                print(f"Error from robot: {response}")
        except:
            print(f"Simulated command: {cmd_str}")

    def execute_chess_move(self, move, current_board):
        uci_move = move.uci()
        from_square = uci_move[:2]
        to_square = uci_move[2:4]
        
        pick_x, pick_y = self.square_to_coords(from_square)
        place_x, place_y = self.square_to_coords(to_square)
        graveyard_x, graveyard_y = -100.0, 200.0

        # 1. CHECK FOR CASTLING
        if current_board.is_castling(move):
            # Map the King's move to the corresponding Rook's move
            rook_moves = {
                "e1g1": ("h1", "f1"), # White Kingside
                "e1c1": ("a1", "d1"), # White Queenside
                "e8g8": ("h8", "f8"), # Black Kingside
                "e8c8": ("a8", "d8")  # Black Queenside
            }
            
            # Move the King
            print(f"Castling! Moving King from {from_square} to {to_square}...")
            self.send_action(1, pick_x, pick_y)
            self.send_action(2, place_x, place_y)
            
            # Move the Rook
            if uci_move in rook_moves:
                r_from, r_to = rook_moves[uci_move]
                rx_pick, ry_pick = self.square_to_coords(r_from)
                rx_place, ry_place = self.square_to_coords(r_to)
                print(f"Castling! Moving Rook from {r_from} to {r_to}...")
                self.send_action(1, rx_pick, ry_pick)
                self.send_action(2, rx_place, ry_place)
            
            # Exit early so we don't move the King a second time!
            return 

        # 2. CHECK FOR EN PASSANT CAPTURE
        if current_board.is_en_passant(move):
            victim_square = to_square[0] + from_square[1]
            vic_x, vic_y = self.square_to_coords(victim_square)
            self.send_action(1, vic_x, vic_y)
            self.send_action(2, graveyard_x, graveyard_y)
            
        # 3. CHECK FOR NORMAL CAPTURE
        elif current_board.is_capture(move):
            self.send_action(1, place_x, place_y)
            self.send_action(2, graveyard_x, graveyard_y)
        
        # 4. EXECUTE THE MAIN MOVE
        print(f"Robot moving piece from {from_square} to {to_square}...")
        self.send_action(1, pick_x, pick_y)
        self.send_action(2, place_x, place_y)


class GameManager:
    """Manages the chess rules, board state, and Stockfish engine."""
    def __init__(self, engine_path, square_size=50.0):
        self.board = chess.Board()
        self.engine = chess.engine.SimpleEngine.popen_uci(engine_path)
        self.robot = ChessRobot(square_size_mm=square_size)

    def reset_game(self):
        """Resets the internal chessboard."""
        self.board = chess.Board()

    def process_human_move(self, uci_move_str):
        """Validates and applies the human's move."""
        move = chess.Move.from_uci(uci_move_str)
        if move in self.board.legal_moves:
            self.board.push(move)
            return True
        return False

    def process_robot_move(self):
        """Asks Stockfish for a move and commands the physical robot."""
        if self.board.is_game_over():
            return None
            
        result = self.engine.play(self.board, chess.engine.Limit(time=1.0))
        best_move = result.move

        # Describe the move BEFORE pushing, while board state still matches "before"
        move_info = self._describe_move(best_move, self.board)

        # Command physical robot FIRST, using the board state BEFORE pushing
        self.robot.execute_chess_move(best_move, self.board)
        
        # Push Stockfish move to Python logic
        self.board.push(best_move)
        return move_info

    def _describe_move(self, move, board):
        """Build a JSON-serializable description of a move's effect on the
        board (source/destination squares, captures, en passant, castling).

        This lets external systems (like the vision script) update their own
        board-memory model deterministically once a move is known, instead of
        having to re-photograph and re-detect the board after the robot acts.
        """
        from_sq = chess.square_name(move.from_square)
        to_sq = chess.square_name(move.to_square)

        info = {
            "uci": move.uci(),
            "from": from_sq,
            "to": to_sq,
            "is_capture": board.is_capture(move),
            "is_en_passant": board.is_en_passant(move),
            "is_castling": board.is_castling(move),
            "en_passant_victim": None,
            "rook_from": None,
            "rook_to": None,
        }

        if info["is_en_passant"]:
            # The captured pawn sits on the "to" file but the "from" rank.
            info["en_passant_victim"] = to_sq[0] + from_sq[1]

        if info["is_castling"]:
            rook_moves = {
                "e1g1": ("h1", "f1"),  # White Kingside
                "e1c1": ("a1", "d1"),  # White Queenside
                "e8g8": ("h8", "f8"),  # Black Kingside
                "e8c8": ("a8", "d8"),  # Black Queenside
            }
            if move.uci() in rook_moves:
                info["rook_from"], info["rook_to"] = rook_moves[move.uci()]

        return info

    def is_game_over(self):
        return self.board.is_game_over()
        
    def get_fen(self):
        return self.board.fen()

    def get_game_status(self):
        """Generates a text status based on the exact board state."""
        if self.board.is_checkmate():
            winner = "White" if self.board.turn == chess.BLACK else "Black"
            return f"Checkmate! {winner} wins."
        if self.board.is_stalemate():
            return "Stalemate! Game is a draw."
        if self.board.is_insufficient_material():
            return "Draw! Insufficient material."
        if self.board.is_check():
            turn = "White" if self.board.turn == chess.WHITE else "Black"
            return f"Check! {turn}'s turn."
        
        turn = "White" if self.board.turn == chess.WHITE else "Black"
        return f"{turn}'s turn."