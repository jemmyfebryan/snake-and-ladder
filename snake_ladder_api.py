import sqlite3
import json
import random
import string
import uuid
import os
from typing import List, Optional, Dict, Any
from fastapi import FastAPI, HTTPException, Body, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

app = FastAPI()

app.mount("/static", StaticFiles(directory="static"), name="static")

templates = Jinja2Templates(directory="templates")

# Enable CORS (still useful if you decide to host frontend elsewhere later)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Configuration ---
PAWNS_PER_PLAYER = 3
WINNING_TILE = 100
DB_FILE = "snakeladder.db"

# --- Database Setup ---
def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS games (
                room_id TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

init_db()

# --- Models ---
class GameState(BaseModel):
    room_id: str
    status: str  # 'waiting', 'playing', 'finished'
    players: List[str]
    turn_index: int
    board_config: Dict[str, Dict[str, int]]
    positions: List[List[int]] 
    finished_pawns: List[List[bool]]
    last_roll: Optional[int] = None
    phase: str = "ROLL"
    winner: Optional[int] = None
    log: List[str] = []

class CreateGameResponse(BaseModel):
    room_id: str
    player_token: str

class JoinGameResponse(BaseModel):
    player_token: str

class PlayComputerResponse(BaseModel):
    player_token: str
    
class PlayComputerPayload(BaseModel):
    room_id: str
    computer_difficulty: str
    
class ActionRequest(BaseModel):
    room_id: str
    player_token: str
    pawn_index: Optional[int] = None
    modifier: Optional[dict] = {
        "dice_prob": None
    }

# --- Helpers ---
def generate_room_id():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

def generate_board():
    snakes = {}
    ladders = {}
    
    # Create Ladders
    while len(ladders) < 5:
        start = random.randint(2, 80)
        end = random.randint(start + 10, 94)
        if (
            str(start) not in ladders
            and end not in ladders.values()
            and start not in ladders.values()
            and str(end) not in ladders
        ):
            ladders[str(start)] = end
            
    # Create Snakes
    while len(snakes) < 4:
        start = random.randint(15, 97)
        end = random.randint(2, start - 10)
        if (
            str(start) not in ladders
            and str(start) not in snakes
            and start not in ladders.values()
            and str(end) not in ladders
            and end not in snakes.values()
            and start not in snakes.values()
            and str(end) not in snakes
        ):
            snakes[str(start)] = end
    
    # One snake guaranteed to be within 96-99
    start = random.randint(98, 99)
    end = random.randint(50, start - 10)
    if (
        str(start) not in ladders
        and str(start) not in snakes
        and start not in ladders.values()
        and str(end) not in ladders
        and end not in snakes.values()
        and start not in snakes.values()
        and str(end) not in snakes
    ):
        snakes[str(start)] = end
    
            
    return {"snakes": snakes, "ladders": ladders}

def save_game(state: GameState):
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO games (room_id, state) VALUES (?, ?)",
            (state.room_id, state.json())
        )

def load_game(room_id: str) -> Optional[GameState]:
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.execute("SELECT state FROM games WHERE room_id = ?", (room_id,))
        row = cursor.fetchone()
        if row:
            return GameState(**json.loads(row[0]))
    return None

# --- Endpoints ---

@app.get("/", response_class=HTMLResponse)
def read_root(request: Request):
    """Serves the frontend HTML file."""
    return templates.TemplateResponse("index.html", {"request": request})

    # if os.path.exists("index.html"):
    #     with open("index.html", "r", encoding="utf-8") as f:
    #         return f.read()
    # return "<h1>Error: index.html not found in the same directory.</h1>"

@app.post("/create", response_model=CreateGameResponse)
def create_game():
    room_id = generate_room_id()
    player_token = str(uuid.uuid4())
    
    initial_state = GameState(
        room_id=room_id,
        status="waiting",
        players=[player_token],
        turn_index=0,
        board_config=generate_board(),
        positions=[[0] * PAWNS_PER_PLAYER, [0] * PAWNS_PER_PLAYER],
        finished_pawns=[[False] * PAWNS_PER_PLAYER, [False] * PAWNS_PER_PLAYER]
    )
    
    save_game(initial_state)
    return {"room_id": room_id, "player_token": player_token}

@app.post("/join", response_model=JoinGameResponse)
def join_game(room_id: str = Body(..., embed=True)):
    game = load_game(room_id)
    if not game:
        raise HTTPException(404, "Room not found")
    
    if len(game.players) >= 2:
        raise HTTPException(400, "Room is full")
        
    player_token = str(uuid.uuid4())
    game.players.append(player_token)
    game.status = "playing"
    game.log.append("Player 2 joined. Game Start!")
    
    save_game(game)
    return {"player_token": player_token}

@app.post("/play_computer", response_model=PlayComputerResponse)
def play_computer(payload: PlayComputerPayload):
    # print(payload)
    room_id = payload.room_id
    computer_difficulty = payload.computer_difficulty
    
    if computer_difficulty not in ["easy", "normal", "hard", "extreme"]:
        raise HTTPException(500, "difficulty invalid, only accepts 'easy', 'normal', 'hard', 'extreme'")
    
    game = load_game(room_id)
    if not game:
        raise HTTPException(404, "Room not found")
    
    if len(game.players) >= 2:
        raise HTTPException(400, "Room is full")
    
    player_token = f"computer_{computer_difficulty}"
    game.players.append(player_token)
    game.status = "playing"
    game.log.append(f"Computer {computer_difficulty} joined. Game Start!")
    print(game.players)
    save_game(game)
    return {"player_token": player_token}

def computer_turn(
    game: GameState,
    room_id: str,
    computer_token: str
):
    # Loop to handle rolling a 6 and getting another turn
    while game.status == "playing" and game.players[game.turn_index] == computer_token:
        computer_difficulty = computer_token.split(sep="_")[-1]
        player_index = 1
        current_positions = game.positions[player_index]
        print(current_positions)
            
        if computer_difficulty == "easy":
            dice_prob = {
                1: 0.2857,
                2: 0.2381,
                3: 0.1905,
                4: 0.1429,
                5: 0.0952,
                6: 0.0476,
            }
        elif computer_difficulty == "hard":
            # Double prob to win when just one dice away
            if min(current_positions) >= 94:
                dice_to_win = 100 - min(current_positions)
                dice_prob = {
                    1: 0.1666,
                    2: 0.1666,
                    3: 0.1667,
                    4: 0.1667,
                    5: 0.1667,
                    6: 0.1667,
                }
                dice_prob[dice_to_win] *= 2
            else:
                dice_prob = {
                    1: 0.0476,
                    2: 0.0952,
                    3: 0.1429,
                    4: 0.1905,
                    5: 0.2381,
                    6: 0.2857,
                }
        elif computer_difficulty == "extreme":
            # triple prob to win when just one dice away
            # Even unfair dice (quadratic)
            if min(current_positions) >= 94:
                dice_to_win = 100 - min(current_positions)
                dice_prob = {
                    1: 0.1666,
                    2: 0.1666,
                    3: 0.1667,
                    4: 0.1667,
                    5: 0.1667,
                    6: 0.1667,
                }
                dice_prob[dice_to_win] *= 3
            else:
                dice_prob = {
                    1: 0.0110,
                    2: 0.0440,
                    3: 0.0989,
                    4: 0.1758,
                    5: 0.2747,
                    6: 0.3956,
                }
        else:
            dice_prob = None
        
        # --- ROLL Action ---
        roll_req = ActionRequest(
            room_id=room_id,
            player_token=computer_token,
            modifier={"dice_prob": dice_prob}
        )
        # Call the logic directly, avoiding HTTP
        try:
            game = process_roll_logic(game, roll_req)
        except ValueError as e:
            # If for some reason the roll is invalid, break the loop
            print(f"Computer Roll Error: {e}")
            break
        
        save_game(game)
        
        # After saving the roll, reload the state to ensure the logic works on a fresh view 
        # (Though not strictly necessary in this direct call flow, it's good practice
        # if the logic was more complex or involved dependencies)
        # game = load_game(room_id)
        
        last_roll = game.last_roll
        
        if computer_difficulty == "easy":
            pawn_index = [pi for pi in range(PAWNS_PER_PLAYER) if not game.finished_pawns[player_index][pi]]
            pawn_to_move = random.choice(pawn_index) if pawn_index else 0
        elif computer_difficulty == "normal" or computer_difficulty == "hard" or computer_difficulty == "extreme":
            # Normal Difficulty = move but prevent pawn with snake,
            # prioritize pawn to the next ladder,
            # prevent one pawn to be finished first, wait all pawn on the 90-100 area
            
            # Hard roll higher dice more likely
            
            # 1. Identify non-finished pawns
            available_pawns = [
                pi for pi in range(PAWNS_PER_PLAYER)
                if not game.finished_pawns[player_index][pi]
            ]

            if not available_pawns:
                pawn_to_move = 0 # Should not happen if game is still active
            else:
                best_move = -1
                max_priority = -1
                
                # --- Move Evaluation ---
                for pawn_idx in available_pawns:
                    current_pos = current_positions[pawn_idx]
                    new_pos = current_pos + last_roll
                    priority = 0

                    # Check if moving is even possible (not overshooting 100)
                    if new_pos > WINNING_TILE:
                        # Lowest priority: stay put
                        priority = 0
                    else:
                        # Check the tile landed on after the move
                        landed_pos = new_pos
                        
                        # 1. Base Priority: Progress (Simply moving forward)
                        # priority = new_pos 
                        
                        str_new_pos = str(new_pos)
                        
                        # 2. Prevent Snake: Check if the *new_pos* is a snake head
                        if str_new_pos in game.board_config['snakes']:
                            priority -= 100 # Heavy penalty for landing on a snake
                            landed_pos = game.board_config['snakes'][str_new_pos]
                        
                        # 3. Prioritize Ladder: Check if the *new_pos* is a ladder bottom
                        elif str_new_pos in game.board_config['ladders']:
                            priority += 100 # Strong bonus for landing on a ladder
                            landed_pos = game.board_config['ladders'][str_new_pos]
                        
                        # 4. End Game Management: Prioritize moving all pawns to 90-99 area,
                        #    and only finish one if all others are also near the end.
                        #    Note: This simplified version will mainly focus on the "don't finish first" part,
                        #          and prioritizing progress to 90+ area for all.
                        
                        finished_count = sum(game.finished_pawns[player_index])
                        all_near_end = all(p >= 90 for i, p in enumerate(current_positions) if not game.finished_pawns[player_index][i] or i == pawn_idx)

                        if new_pos == WINNING_TILE:
                            # Prevent one pawn to be finished first
                            if finished_count == 0 and not all_near_end:
                                # Heavy penalty if this is the first finish and others aren't near the end (90+)
                                priority -= 200
                            else:
                                # High bonus if it's safe to finish
                                priority += 300 
                        
                        # If this move brings the pawn close to the end (90+), give a moderate bonus
                        elif new_pos >= 90 and current_pos < 90:
                            priority += 20
                            
                        # The final position after snake/ladder also affects priority
                        # if landed_pos > priority:
                        #     priority = landed_pos

                    if priority > max_priority:
                        max_priority = priority
                        pawn_to_move = pawn_idx
                    elif priority == max_priority and pawn_idx in available_pawns:
                        # Tie-breaker: choose the pawn that is currently furthest back,
                        # to keep the pawns clustered for the end-game waiting strategy.
                        if current_positions[pawn_idx] < current_positions[pawn_to_move]:
                            pawn_to_move = pawn_idx
                    else:
                        pawn_to_move = 0

            pawn_index = pawn_to_move
        
        # --- MOVE Action ---
        move_req = ActionRequest(
            room_id=room_id,
            player_token=computer_token,
            pawn_index=pawn_to_move
        )

        # Call the logic directly, avoiding HTTP
        try:
            game = process_move_logic(game, move_req)
        except ValueError as e:
            print(f"Computer Move Error: {e}")
            break

        save_game(game)
        # Reload the game state to check if turn has changed for the next loop iteration
        game = load_game(room_id)
        
        # The loop condition will now check if it's still the computer's turn 
        # (i.e., if last_roll was 6 or if the game ended)
    return game # The function returns the final state after the computer's turn(s)

@app.get("/state/{room_id}")
def get_state(room_id: str):
    game = load_game(room_id)
    
    # When plays against computer, roll dice and move random pawn
    if len(game.players) > 1:
        if game.players[1].startswith("computer") and game.turn_index == 1:
            computer_token = game.players[1]
            computer_turn(
                game=game,
                room_id=room_id,
                computer_token=computer_token,
            )
    
    if not game:
        raise HTTPException(404, "Room not found")
    return game

# --- Helpers for Core Game Logic ---

# New helper for the roll logic (no API/request handling)
def process_roll_logic(game: GameState, req: ActionRequest) -> GameState:
    """Calculates the roll and updates game state without HTTP logic."""
    
    # Validation checks from the original /roll (simplified for internal use)
    if game.status != "playing":
        raise ValueError("Game not active")
    if game.players[game.turn_index] != req.player_token:
        # This check is what's causing the 400 error in your current setup!
        raise ValueError("Not computer's turn") 
    if game.phase != "ROLL":
        raise ValueError("Already rolled, waiting for move")

    entity = "Computer" if game.players[game.turn_index].startswith("computer") else "Player"
    
    if req.modifier.get("dice_prob", {}):
        values = list(req.modifier.get("dice_prob").keys())
        probs = list(req.modifier.get("dice_prob").values())
        # Convert keys to int, as they were strings in the original dict
        roll = random.choices([int(v) for v in values], probs)[0] 
    else:
        roll = random.randint(1, 6)
    game.last_roll = roll
    game.phase = "MOVE"
    game.log.append(f"{entity} {game.turn_index + 1} rolled a {roll}")
    
    return game

# New helper for the move logic (no API/request handling)
def process_move_logic(game: GameState, req: ActionRequest) -> GameState:
    """Calculates the move and updates game state without HTTP logic."""
    
    # Validation checks from the original /move (simplified for internal use)
    if req.pawn_index is None:
        raise ValueError("Invalid request: pawn_index missing")
    if game.phase != "MOVE":
        raise ValueError("Must roll first")
    if game.players[game.turn_index] != req.player_token:
        # This check is what's causing the 400 error in your current setup!
        raise ValueError("Not computer's turn") 
        
    pawn_idx = req.pawn_index
    current_player = game.turn_index
    entity = "Computer" if game.players[current_player].startswith("computer") else "Player"
    
    # ... (rest of the /move logic from line 419)
    if pawn_idx < 0 or pawn_idx >= PAWNS_PER_PLAYER:
        raise ValueError("Invalid pawn")
        
    current_pos = game.positions[current_player][pawn_idx]
    
    if game.finished_pawns[current_player][pawn_idx]:
        raise ValueError("Pawn already finished")
        
    roll = game.last_roll
    new_pos = current_pos + roll
    
    if new_pos > WINNING_TILE:
        game.log.append(f"Pawn {pawn_idx+1} needs exact roll. Stayed at {current_pos}.")
    else:
        landed_msg = ""
        str_pos = str(new_pos)
        
        if str_pos in game.board_config['snakes']:
            final_pos = game.board_config['snakes'][str_pos]
            landed_msg = f"(Snake! {new_pos}->{final_pos})"
            new_pos = final_pos
        elif str_pos in game.board_config['ladders']:
            final_pos = game.board_config['ladders'][str_pos]
            landed_msg = f"(Ladder! {new_pos}->{final_pos})"
            new_pos = final_pos
            
        game.positions[current_player][pawn_idx] = new_pos
        game.log.append(f"{entity} {current_player+1} moved Pawn {pawn_idx+1} to {new_pos} {landed_msg}")

        if new_pos == WINNING_TILE:
            game.finished_pawns[current_player][pawn_idx] = True
            game.log.append(f"{entity} {current_player+1}'s Pawn {pawn_idx+1} Finished!")

    if all(game.finished_pawns[current_player]):
        game.status = "finished"
        game.winner = current_player
        game.log.append(f"{entity.upper()} {current_player+1} WINS!")
    
    if game.status != "finished":
        if game.last_roll != 6:
            game.turn_index = 1 - game.turn_index
        else:
            game.log.append(f"{entity} {current_player+1} rolled 6, goes again!")
            
    game.phase = "ROLL"
    game.last_roll = None

    return game

@app.post("/roll")
def roll_dice(req: ActionRequest):
    game = load_game(req.room_id)
    if not game:
        raise HTTPException(404, "Room not found")
    
    # Use the logic helper
    try:
        game = process_roll_logic(game, req)
    except ValueError as e:
        raise HTTPException(400, str(e))
        
    save_game(game)
    return game

@app.post("/move")
def move_pawn(req: ActionRequest):
    game = load_game(req.room_id)
    if not game:
        raise HTTPException(404, "Room not found")
    
    # Use the logic helper
    try:
        game = process_move_logic(game, req)
    except ValueError as e:
        raise HTTPException(400, str(e))
        
    save_game(game)
    return game