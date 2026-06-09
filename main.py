from fastapi import FastAPI, Request, Form, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import random
import uuid
from typing import Annotated
from starlette.middleware.sessions import SessionMiddleware
import os
from dotenv import load_dotenv
# Import database functions
from db import auth_user, add_user, leaderboard, update_score, check_user_id

load_dotenv()

# ==========================================
# App Initialization & Middleware
# ==========================================
app = FastAPI()

# Session middleware for handling user authentication state
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SESSION_KEY"),
    session_cookie=os.getenv("COOKIE_KEY")
)

# Mount static files and templates
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# ==========================================
# Global State (Multi-Room Matchmaking)
# ==========================================
class GameRoom:
    def __init__(self, room_id: str):
        self.room_id = room_id
        self.players: list[WebSocket] = []
        self.scores: dict[WebSocket, int] = {}
        self.ques: int = 0
        self.game_over: bool = False
        
        # The currently active math equation for this room
        self.current_m_id: int = None
        self.current_sum: int = None
        
        # To prevent race conditions if both users answer simultaneously
        self.winner_ws: WebSocket = None

# Users waiting to be paired
waiting_queue: list[WebSocket] = []

# Active matches: map of room_id to GameRoom
rooms: dict[str, GameRoom] = {}

# Map of a player's WebSocket to their current room_id
websocket_to_room: dict[WebSocket, str] = {}


# ==========================================
# Helper Functions
# ==========================================
def get_random():
    """Generates two random integers between 1 and 100 for the math equation."""
    a = random.randint(1, 100)
    b = random.randint(1, 100)
    return a, b

# ==========================================
# HTTP Routes
# ==========================================
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Renders the home page, leaderboard, and authentication popups."""
    top_5 = leaderboard()
    if "auth" not in request.session:
        request.session["auth"] = False

    auth_error = request.session.pop("auth_error", None)
    auth_tab = request.session.pop("auth_tab", "login")

    return templates.TemplateResponse(
        request=request, 
        name="index.html", 
        context={
            "auth": request.session.get("auth", False), 
            "player_id": request.session.get("player_id"),
            "leaderboard": top_5,
            "auth_error": auth_error,
            "auth_tab": auth_tab
        }
    )

@app.get("/play", response_class=HTMLResponse)
async def read_file(request: Request):
    """Renders the match interface. The actual logic is handled via WebSockets."""
    # Initialize basic session variables
    if "score" not in request.session:
        request.session["score"] = 0   
    if "ques" not in request.session: 
        request.session["ques"] = 0

    # Generate initial dummy equation (will be overwritten by WebSocket)
    a, b = get_random()
    m_id = random.randint(1000, 9999)

    return templates.TemplateResponse(
        request=request,
        name="match.html",
        context={
            "a": a,
            "b": b,
            "id": m_id, 
            "score": request.session.get("score", 0), 
            "ques": request.session.get("ques", 0)
        }
    )

@app.post("/start")
def start_match(request: Request):
    """Initializes a new game session and redirects to /play."""
    request.session["score"] = 0
    request.session["ques"] = 0
    request.session["game_over"] = False

    return RedirectResponse(url="/play", status_code=303)

@app.post("/login")
def login(request: Request, user_id: Annotated[int, Form()], user_pass: Annotated[str, Form()]):
    """Handles user login and updates session authentication state."""
    if auth_user(user_id, user_pass):
        request.session["auth"] = True
        request.session["player_id"] = user_id
        request.session.pop("auth_error", None)
        request.session.pop("auth_tab", None)
    else:
        request.session["auth_error"] = "Invalid credentials. Please try again."
        request.session["auth_tab"] = "login"

    return RedirectResponse(url="/", status_code=303)

@app.post("/register")
def register(request: Request, user_id: Annotated[int, Form()], user_pass: Annotated[str, Form()]):
    """Handles new user registration and logs them in."""
    if check_user_id(user_id):
        request.session["auth_error"] = f"User ID {user_id} is already registered."
        request.session["auth_tab"] = "register"
        return RedirectResponse(url="/", status_code=303)

    if add_user(user_id, user_pass):
        request.session["auth"] = True
        request.session["player_id"] = user_id
        request.session.pop("auth_error", None)
        request.session.pop("auth_tab", None)
    else:
        request.session["auth_error"] = "Error creating account. Please try again."
        request.session["auth_tab"] = "register"
    return RedirectResponse(url="/", status_code=303)

@app.post("/test-auth")
def test_auth(request: Request):
    """Revokes user authentication (Logout functionality)."""
    request.session["auth"] = False
    return RedirectResponse(url="/", status_code=303)

# ==========================================
# WebSocket Routes (Multiplayer Logic)
# ==========================================
@app.websocket("/ws")
async def webs(websocket: WebSocket):
    """
    Handles real-time multiplayer logic.
    Coordinates 2 players, sends math questions, tracks scores, and announces the winner.
    """
    await websocket.accept()
    
    # 1. Add user to matchmaking queue
    waiting_queue.append(websocket)
    
    # 2. Matchmaking Logic
    if len(waiting_queue) >= 2:
        player1 = waiting_queue.pop(0)
        player2 = waiting_queue.pop(0)
        
        # Create a unique room
        room_id = str(uuid.uuid4())
        room = GameRoom(room_id)
        room.players = [player1, player2]
        room.scores[player1] = 0
        room.scores[player2] = 0
        
        # Register the room and mappings
        rooms[room_id] = room
        websocket_to_room[player1] = room_id
        websocket_to_room[player2] = room_id
        
        # Generate the first question
        a, b = get_random()
        room.current_m_id = random.randint(101, 999)
        room.current_sum = a + b
        
        # Broadcast start signal
        for p in room.players:
            await p.send_text("2 Players joined. Starting match...")
            await p.send_json({"a": a, "b": b, "m-id": room.current_m_id})
    else:
        # Waiting for an opponent
        await websocket.send_text("1 Player in the room. Waiting for one more player...")

    # 3. Main Gameplay Loop
    try:
        while True:
            data = await websocket.receive_json()
            
            # Fetch the player's specific room
            room_id = websocket_to_room.get(websocket)
            if not room_id or room_id not in rooms:
                continue
            
            room = rooms[room_id]
            
            # Parse submission
            try:
                m_id = int(data.get("mID"))
                submitted_sum = int(data.get("sum"))
            except (ValueError, TypeError):
                await websocket.send_text("Invalid input submitted!")
                continue

            # Verify Answer
            if m_id == room.current_m_id and submitted_sum == room.current_sum:
                # Ensure only the first correct answer per round is processed
                if room.winner_ws is None:
                    room.winner_ws = websocket
                    player_num = room.players.index(websocket) + 1

                    # Increment scores
                    room.scores[websocket] += 1
                    room.ques += 1

                    # Notify players of round outcome
                    for c in room.players:
                        c_num = room.players.index(c) + 1
                        opp = [p for p in room.players if p != c][0]
                        if c == websocket:
                            await c.send_text(f"Player {player_num} has won.")
                        else:
                            await c.send_text(f"Player {player_num} has won. You came second!")
                        
                        # Send score update to each player
                        await c.send_json({
                            "type": "score_update",
                            "your_score": room.scores[c],
                            "opp_score": room.scores[opp],
                            "question": room.ques,
                            "total_questions": 15
                        })

                    # End of game condition (15 questions)
                    if room.ques >= 15:
                        room.game_over = True
                        res = ["Game over! Final Scores:"]
                        
                        highest_score = -1
                        winners = []
                        
                        # Calculate and format final scores
                        for i, player_ws in enumerate(room.players):
                            score = room.scores.get(player_ws, 0)
                            res.append(f"Player {i+1}: {score} points")
                            
                            if score > highest_score:
                                highest_score = score
                                winners = [f"Player {i+1}"]
                            elif score == highest_score:
                                winners.append(f"Player {i+1}")
                                
                            # Database Update
                            player_id = player_ws.session.get("player_id")
                            if player_id is not None:
                                update_score(player_id, 15)

                        # Send personalized final messages
                        for i, c in enumerate(room.players):
                            player_name = f"Player {i+1}"
                            personal_res = list(res)
                            if len(winners) == 1:
                                if player_name in winners:
                                    personal_res.append("Result: You won! ")
                                else:
                                    personal_res.append("Result: You lost! ")
                            else:
                                personal_res.append("Result: It's a draw! ")
                                
                            await c.send_text(" | ".join(personal_res))
                            
                        # Cleanup room since game is over
                        del rooms[room_id]
                        for c in room.players:
                            if c in websocket_to_room:
                                del websocket_to_room[c]
                    else: 
                        # Next Round
                        a, b = get_random()
                        room.current_m_id = random.randint(101, 999)
                        room.current_sum = a + b

                        for c in room.players:
                            await c.send_json({"a": a, "b": b, "m-id": room.current_m_id})

                    # Reset round winner
                    room.winner_ws = None
                else:
                    await websocket.send_text("You came second!")
            else:
                await websocket.send_text("Wrong answer! Try again.")

    except WebSocketDisconnect:
        # Handle disconnects
        pass
    finally:
        # 4. Cleanup
        if websocket in waiting_queue:
            waiting_queue.remove(websocket)
            
        room_id = websocket_to_room.get(websocket)
        if room_id and room_id in rooms:
            room = rooms[room_id]
            
            # If the game wasn't naturally over, the other player wins by default
            if not room.game_over:
                room.game_over = True
                remaining_players = [p for p in room.players if p != websocket]
                for p in remaining_players:
                    try:
                        await p.send_text("Opponent left the match. You win by default! 🎉")
                        # We could optionally update score for default win
                        player_id = p.session.get("player_id")
                        if player_id is not None:
                            update_score(player_id, room.scores.get(p, 0) + 1)
                    except Exception:
                        pass
                
            # Remove room mappings
            for player_ws in room.players:
                if player_ws in websocket_to_room:
                    del websocket_to_room[player_ws]
            if room_id in rooms:
                del rooms[room_id]
