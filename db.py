import os
import psycopg2
from dotenv import load_dotenv
import bcrypt

# Load environment variables from .env file
load_dotenv()

# Global database connection object
conn = None

# Establish the database connection on startup
try:
    db_str = os.getenv("DB_CONNECTOR")
    if not db_str:
        raise ValueError("DB_CONNECTOR environment variable is missing from the configuration.")
    conn = psycopg2.connect(db_str)
    conn.autocommit = True
except Exception as e:
    print(f"Database connection failed: {e}")

def check_user_id(user_id: int) -> bool:
    """Checks if a given user ID already exists in the database."""
    if conn is None:
        print("Database connection is not established.")
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM math_user WHERE user_id = %s LIMIT 1", (user_id,))
            user = cur.fetchone()
            return user is not None
    except Exception as e:
        print(f"Error checking user ID {user_id}: {e}")
        return False

def add_user(user_id: int, user_pass: str) -> bool:
    """Registers a new user by securely hashing their password and storing it."""
    if conn is None:
        print("Database connection is not established.")
        return False
    try:
        if not isinstance(user_id, int):
            raise TypeError("user_id must be an integer.")
        if not isinstance(user_pass, str):
            raise TypeError("user_pass must be a string.")

        command = """ 
        INSERT INTO math_user(user_id, user_pass)
        VALUES (%s, %s);
        """
        # Hash the password using bcrypt for security
        hashed_pass = bcrypt.hashpw(user_pass.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

        add_values = (user_id, hashed_pass)
        with conn.cursor() as cur:
            cur.execute(command, add_values)

        # Verify successful insertion
        if check_user_id(user_id):
            return True
        else:
            return False
    except psycopg2.errors.UniqueViolation:
        print(f"User with ID {user_id} already exists.")
        return False
    except Exception as e:
        print(f"Error adding user: {e}")
        return False
        
def auth_user(user_id: int, user_pass: str) -> bool:
    """Authenticates a user by checking the provided password against the stored hash."""
    if conn is None:
        print("Database connection is not established.")
        return False
    try:
        if not isinstance(user_id, int):
            raise TypeError("user_id must be an integer.")
        if not isinstance(user_pass, str):
            raise TypeError("user_pass must be a string.")

        with conn.cursor() as cur:
            cur.execute("SELECT user_pass FROM math_user WHERE user_id = %s", (user_id,))
            row = cur.fetchone()
            if row is None:
                print(f"User ID {user_id} does not exist.")
                return False
            
            up = row[0]
            # Validate the password with bcrypt
            check_pass = bcrypt.checkpw(user_pass.encode("utf-8"), up.encode("utf-8"))
            return check_pass
    except ValueError as e:
        print(f"Invalid password hash format: {e}")
        return False
    except Exception as e:
        print(f"Error authenticating user: {e}")
        return False

def update_score(user_id: int, score: int) -> bool:
    """Adds the newly obtained score to the user's total score in the database."""
    if conn is None:
        print("Database connection is not established.")
        return False
    try:
        if not isinstance(user_id, int):
            raise TypeError("user_id must be an integer.")
        if not isinstance(score, int):
            raise TypeError("score must be an integer.")

        command = """
        UPDATE math_user
        SET user_score = user_score + %s
        WHERE user_id = %s
        """
        add_value = (score, user_id)
        with conn.cursor() as cur:
            cur.execute(command, add_value)
            if cur.rowcount == 0:
                print(f"User ID {user_id} does not exist.")
                return False
            return True
    except Exception as e:
        print(f"Error updating score for user {user_id}: {e}")
        return False

def leaderboard():
    """Retrieves the top 5 players based on their total score."""
    with conn.cursor() as cur:
        cur.execute("select user_id, user_score from math_user order by user_score desc")
        score = cur.fetchall()
        # Format and limit results to top 5
        values = [[u_id, score] for [u_id,score] in score][:5]
        return values