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
import httpx

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
PAWNS_PER_PLAYER = 2
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

# --- Helpers ---
def generate_room_id():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

def generate_board():
    snakes = {}
    ladders = {}
    
    # Create Ladders
    for _ in range(6):
        start = random.randint(2, 80)
        end = random.randint(start + 10, 98)
        if start not in ladders and end not in ladders.values():
            ladders[str(start)] = end
            
    # Create Snakes
    for _ in range(6):
        start = random.randint(15, 98)
        end = random.randint(2, start - 10)
        if (str(start) not in ladders and str(start) not in snakes and 
            end not in ladders and end not in snakes.values()):
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
    

@app.get("/state/{room_id}")
def get_state(room_id: str):
    game = load_game(room_id)
    
    # When plays against computer, roll dice and move random pawn
    if game.players[1].startswith("computer") and game.turn_index == 1:
        room_id = game.room_id
        computer_token = game.players[1]
        pawn_index = [pi for pi in range(PAWNS_PER_PLAYER) if not game.finished_pawns[1][pi]]
        if pawn_index:
            pawn_index = random.choice(pawn_index)
        else:
            pawn_index = 0
        # Roll a Dice
        with httpx.Client() as client:
            response = client.post("http://127.0.0.1:8080/roll", json={
                "room_id": room_id,
                "player_token": computer_token
            })
            response.raise_for_status()
            response = client.post("http://127.0.0.1:8080/move", json={
                "room_id": room_id,
                "player_token": computer_token,
                "pawn_index": pawn_index
            })
            response.raise_for_status()
    
    if not game:
        raise HTTPException(404, "Room not found")
    return game

@app.post("/roll")
def roll_dice(req: ActionRequest):
    game = load_game(req.room_id)
    if not game:
        raise HTTPException(404, "Room not found")
        
    if game.status != "playing":
        raise HTTPException(400, "Game not active")
        
    if game.players[game.turn_index] != req.player_token:
        raise HTTPException(400, "Not your turn")
        
    if game.phase != "ROLL":
        raise HTTPException(400, "Already rolled, waiting for move")
    
    entity = "Computer" if game.players[game.turn_index].startswith("computer") else "Player"
        
    roll = random.randint(1, 6)
    game.last_roll = roll
    game.phase = "MOVE"
    game.log.append(f"{entity} {game.turn_index + 1} rolled a {roll}")
    
    save_game(game)
    return game

@app.post("/move")
def move_pawn(req: ActionRequest):
    game = load_game(req.room_id)
    if not game or req.pawn_index is None:
        raise HTTPException(400, "Invalid request")
        
    if game.phase != "MOVE":
        raise HTTPException(400, "Must roll first")
        
    if game.players[game.turn_index] != req.player_token:
        raise HTTPException(400, "Not your turn")
        
    pawn_idx = req.pawn_index
    current_player = game.turn_index
    entity = "Computer" if game.players[current_player].startswith("computer") else "Player"
    
    if pawn_idx < 0 or pawn_idx >= PAWNS_PER_PLAYER:
        raise HTTPException(400, "Invalid pawn")
        
    current_pos = game.positions[current_player][pawn_idx]
    
    if game.finished_pawns[current_player][pawn_idx]:
        raise HTTPException(400, "Pawn already finished")
        
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
    save_game(game)
    return game