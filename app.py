from flask import Flask, render_template, request, jsonify
from chess_backend import GameManager

app = Flask(__name__)

# --- INITIALIZE THE BACKEND ---
ENGINE_PATH = r"C:\Users\Abdul Maajid Aga\Desktop\stockfish-windows-x86-64-avx2\stockfish\stockfish-windows-x86-64-avx2.exe"
SQUARE_SIZE_MM = 42.1

# This single object now holds the Robot, Stockfish, and the Board!
game = GameManager(engine_path=ENGINE_PATH, square_size=SQUARE_SIZE_MM)

# --- WEB ROUTES ---
@app.route('/')
def index():
    game.reset_game()
    return render_template('index.html')

@app.route('/move', methods=['POST'])
def make_move():
    data = request.json
    
    # Process Human Move
    is_legal = game.process_human_move(data['move'])
    if not is_legal:
        return jsonify({"error": "Illegal move"}), 400

    # Check if human ended the game
    if game.is_game_over():
        return jsonify({"fen": game.get_fen(), "status": game.get_game_status(), "robot_move": None})

    # Process Robot Turn
    robot_move = game.process_robot_move()
    
    # Return new board state, dynamic text (Check, Checkmate, etc.), and exactly
    # what the robot just did so the vision client can update its board memory
    # without needing to re-detect it from a camera frame.
    return jsonify({
        "fen": game.get_fen(), 
        "status": game.get_game_status(),
        "robot_move": robot_move
    })

@app.route('/state', methods=['GET'])
def get_state():
    # Allows the spectator UI to fetch the live board state
    return jsonify({
        "fen": game.get_fen(), 
        "status": game.get_game_status()
    })

if __name__ == '__main__':
    # Run the web server with the reloader disabled to stop the double-connection!
    app.run(host='0.0.0.0', port=5050, debug=True, use_reloader=False)