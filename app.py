from flask import Flask, render_template, request, jsonify, session
import sqlite3
import random
import os
import time
import requests
from datetime import date, timedelta
from dotenv import load_dotenv


# ============================================================
# ENVIRONMENT / APINEX CONFIGURATION
# ============================================================

load_dotenv()

APINEX_API_KEY = os.getenv("APINEX_API_KEY")

APINEX_URL = "https://api.apinex.bond/v1/chat/completions"

MODEL = "free/deepseek-v4-pro-0813"

DB = "promptify.db"

# ============================================================
# FLASK APP
# ============================================================

app = Flask(__name__)

app.secret_key = os.getenv(
    "FLASK_SECRET_KEY",
    "replace-this-with-a-secure-random-key-in-production"
)


# ============================================================
# ROLES
# ============================================================

ROLE = {
    "coding": "Senior Software Engineer",
    "writing": "Professional Writer",
    "study": "Expert Educator",
    "marketing": "Marketing Strategist",
    "custom": "Helpful AI Assistant",
}


# ============================================================
# PROMPT BANK
# ============================================================

BANK = {
    "coding": [
        "Write a Python Bank Management System using OOP.",
        "Create a Flask REST API with CRUD operations.",
    ],

    "writing": [
        "Write an article about Artificial Intelligence.",
        "Write a motivational speech for students.",
    ],

    "study": [
        "Explain Morphology in Natural Language Processing.",
        "Explain Machine Learning with examples.",
    ],

    "marketing": [
        "Write Instagram ads for a clothing brand.",
        "Create SEO content for an AI website.",
    ],
}


# ============================================================
# SIMPLE LANGUAGE RULES
# ============================================================

SIMPLE_LANGUAGE_RULES = """
- Use simple, everyday English.
- Write for beginners and freshers.
- Use short and clear sentences.
- Prefer common words over difficult words.
- Avoid unnecessary jargon.
- Avoid repetition.
- Make the request practical and easy to understand.
- Keep the final prompt concise.
- Keep the final prompt between 40 and 120 words when possible.
"""


# ============================================================
# DATABASE
# ============================================================

def db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_column(
    conn,
    table,
    column,
    coltype="TEXT"
):
    """
    Add a column to an existing table
    if it does not already exist.
    """

    cols = [
        r["name"]
        for r in conn.execute(
            f"PRAGMA table_info({table})"
        ).fetchall()
    ]

    if column not in cols:

        conn.execute(
            f"""
            ALTER TABLE {table}
            ADD COLUMN {column} {coltype}
            """
        )


def init_db():

    conn = None

    try:

        conn = db()

        # ----------------------------------------------------
        # USERS
        # ----------------------------------------------------

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE,
                password TEXT,
                name TEXT,
                email TEXT,
                gender TEXT,
                streak INTEGER DEFAULT 0,
                last_active_date TEXT
            )
            """
        )

        # ----------------------------------------------------
        # HISTORY
        # ----------------------------------------------------

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS history(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                prompt TEXT,
                category TEXT,
                action TEXT,
                source_prompt TEXT,
                created_at TIMESTAMP
                    DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        # ----------------------------------------------------
        # FAVORITES
        # ----------------------------------------------------

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS favorites(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                prompt TEXT,
                category TEXT,
                source_prompt TEXT,
                created_at TIMESTAMP
                    DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        # ----------------------------------------------------
        # SAVED
        # ----------------------------------------------------

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS saved(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                prompt TEXT,
                category TEXT,
                source_prompt TEXT,
                created_at TIMESTAMP
                    DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        # ----------------------------------------------------
        # UPGRADE OLD DATABASES
        # ----------------------------------------------------

        _ensure_column(
            conn,
            "history",
            "source_prompt",
            "TEXT"
        )

        _ensure_column(
            conn,
            "favorites",
            "source_prompt",
            "TEXT"
        )

        _ensure_column(
            conn,
            "saved",
            "source_prompt",
            "TEXT"
        )

        _ensure_column(
            conn,
            "users",
            "streak",
            "INTEGER DEFAULT 0"
        )

        _ensure_column(
            conn,
            "users",
            "last_active_date",
            "TEXT"
        )

        # ----------------------------------------------------
        # UNIQUE FAVORITES
        # ----------------------------------------------------

        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS
            uq_favorites_user_prompt
            ON favorites(user_id, prompt)
            """
        )

        # ----------------------------------------------------
        # UNIQUE SAVED
        # ----------------------------------------------------

        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS
            uq_saved_user_prompt
            ON saved(user_id, prompt)
            """
        )

        conn.commit()

    except sqlite3.Error as e:

        print(
            f"Database system initialization error: {e}"
        )

    finally:

        if conn:
            conn.close()


# ============================================================
# DAILY STREAK
# ============================================================

def update_user_streak(user_id):

    today = date.today()
    today_str = today.isoformat()

    conn = None

    try:

        conn = db()

        user = conn.execute(
            """
            SELECT streak, last_active_date
            FROM users
            WHERE id = ?
            """,
            (user_id,),
        ).fetchone()

        if not user:
            return 0

        streak = user["streak"] or 0
        last_active = user["last_active_date"]

        # ----------------------------------------------------
        # FIRST ACTIVITY
        # ----------------------------------------------------

        if not last_active:

            streak = 1

        # ----------------------------------------------------
        # ALREADY ACTIVE TODAY
        # ----------------------------------------------------

        elif last_active == today_str:

            return streak

        # ----------------------------------------------------
        # NEXT CONSECUTIVE DAY
        # ----------------------------------------------------

        else:

            try:

                last_date = date.fromisoformat(
                    last_active
                )

            except ValueError:

                streak = 1

            else:

                if today == (
                    last_date + timedelta(days=1)
                ):

                    streak += 1

                else:

                    streak = 1

        # ----------------------------------------------------
        # SAVE
        # ----------------------------------------------------

        conn.execute(
            """
            UPDATE users
            SET streak = ?,
                last_active_date = ?
            WHERE id = ?
            """,
            (
                streak,
                today_str,
                user_id,
            ),
        )

        conn.commit()

        return streak

    except sqlite3.Error as e:

        print(
            f"Streak update error: {e}"
        )

        return 0

    finally:

        if conn:
            conn.close()


# ============================================================
# TOKEN BUDGET
# ============================================================

def get_max_tokens(text):

    """
    Keep token usage low for short prompts,
    while allowing more output for larger requests.
    """

    length = len(text)

    if length <= 500:

        return 180

    if length <= 1200:

        return 250

    if length <= 2500:

        return 350

    if length <= 5000:

        return 450

    return 550


# ============================================================
# EXTRACT AI TEXT
# ============================================================

def extract_ai_text(result):

    if not isinstance(result, dict):

        return None

    # --------------------------------------------------------
    # STANDARD OPENAI-COMPATIBLE RESPONSE
    # --------------------------------------------------------

    choices = result.get("choices")

    if choices and len(choices) > 0:

        choice = choices[0] or {}

        message = choice.get(
            "message",
            {}
        ) or {}

        content = message.get(
            "content"
        )

        # Normal string content
        if isinstance(content, str):

            content = content.strip()

            if content:

                return content

        # Content returned as a list
        if isinstance(content, list):

            parts = []

            for item in content:

                if not isinstance(
                    item,
                    dict
                ):
                    continue

                text = item.get("text")

                if text:

                    parts.append(
                        str(text)
                    )

            if parts:

                return "".join(
                    parts
                ).strip()

    # --------------------------------------------------------
    # ALTERNATIVE: content
    # --------------------------------------------------------

    content = result.get("content")

    if isinstance(content, str):

        content = content.strip()

        if content:

            return content

    # --------------------------------------------------------
    # ALTERNATIVE: text
    # --------------------------------------------------------

    text = result.get("text")

    if isinstance(text, str):

        text = text.strip()

        if text:

            return text

    return None


# ============================================================
# APINEX AI API
# ============================================================

def call_apinex(instruction):

    if not APINEX_API_KEY:

        raise Exception(
            "APINEX_API_KEY is missing. "
            "Please add it to your .env file."
        )

    headers = {
        "Authorization": (
            f"Bearer {APINEX_API_KEY}"
        ),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    max_tokens = get_max_tokens(
        instruction
    )

    data = {
        "model": MODEL,

        "messages": [
            {
                "role": "user",
                "content": instruction,
            }
        ],

        "temperature": 0.2,

        "max_tokens": max_tokens,
    }

    # --------------------------------------------------------
    # TRY API
    # --------------------------------------------------------

    for attempt in range(2):

        try:

            response = requests.post(
                APINEX_URL,
                headers=headers,
                json=data,
                timeout=30,
            )

        except requests.exceptions.Timeout:

            if attempt == 0:
                continue

            raise Exception(
                "APInex API request timed out. "
                "Please try again."
            )

        except requests.exceptions.ConnectionError:

            if attempt == 0:
                time.sleep(1)
                continue

            raise Exception(
                "Could not connect to the APInex API."
            )

        except requests.exceptions.RequestException as e:

            raise Exception(
                f"APInex API request failed: {e}"
            )

        # ----------------------------------------------------
        # DEBUG
        # ----------------------------------------------------

        print(
            "\n================ APINEX DEBUG ================"
        )

        print(
            "STATUS:",
            response.status_code
        )

        print(
            "MAX TOKENS:",
            max_tokens
        )

        print(
            "RAW RESPONSE:"
        )

        print(
            response.text
        )

        print(
            "===============================================\n"
        )

        # ----------------------------------------------------
        # RATE LIMIT
        # ----------------------------------------------------

        if response.status_code == 429:

            if attempt == 0:

                time.sleep(2)

                continue

            raise Exception(
                "APInex API is temporarily "
                "rate-limited. "
                "Please wait a few seconds "
                "and try again."
            )

        # ----------------------------------------------------
        # OTHER API ERRORS
        # ----------------------------------------------------

        if response.status_code != 200:

            try:

                error_data = response.json()

                error = error_data.get(
                    "error"
                )

                if isinstance(
                    error,
                    dict
                ):

                    message = error.get(
                        "message"
                    )

                    if message:

                        raise Exception(
                            f"APInex API Error "
                            f"{response.status_code}: "
                            f"{message}"
                        )

                if isinstance(
                    error,
                    str
                ):

                    raise Exception(
                        f"APInex API Error "
                        f"{response.status_code}: "
                        f"{error}"
                    )

            except ValueError:

                pass

            raise Exception(
                f"APInex API Error "
                f"{response.status_code}: "
                f"{response.text}"
            )

        # ----------------------------------------------------
        # JSON
        # ----------------------------------------------------

        try:

            result = response.json()

        except ValueError:

            raise Exception(
                "APInex returned invalid JSON."
            )

        print(
            "PARSED APINEX RESPONSE:"
        )

        print(
            result
        )

        # ----------------------------------------------------
        # EXTRACT TEXT
        # ----------------------------------------------------

        content = extract_ai_text(
            result
        )

        if content:

            return content

        # ----------------------------------------------------
        # CHECK FINISH REASON
        # ----------------------------------------------------

        finish_reason = None

        choices = result.get(
            "choices"
        )

        if choices:

            finish_reason = choices[0].get(
                "finish_reason"
            )

        print(
            "FINISH REASON:",
            finish_reason
        )

        # ----------------------------------------------------
        # MODEL HIT TOKEN LIMIT
        # ----------------------------------------------------

        if (
            finish_reason == "length"
            and attempt == 0
        ):

            # Increase only for retry.
            # This avoids using a large budget
            # for every normal request.

            max_tokens = min(
                max_tokens * 2,
                600
            )

            data["max_tokens"] = max_tokens

            print(
                "Retrying with max_tokens:",
                max_tokens
            )

            continue

        # ----------------------------------------------------
        # EMPTY RESPONSE
        # ----------------------------------------------------

        raise Exception(
            "APInex returned a successful response, "
            "but no text was found. "
            f"Finish reason: {finish_reason}. "
            f"Full response: {result}"
        )

    raise Exception(
        "APInex could not generate a response."
    )


# ============================================================
# HOME
# ============================================================

@app.route("/")
def index():

    if "user_id" not in session:

        return render_template(
            "index.html",
            auth_view=True
        )

    return render_template(
        "index.html",
        auth_view=False
    )


# ============================================================
# REGISTER
# ============================================================

@app.route(
    "/api/auth/register",
    methods=["POST"]
)
def register():

    data = request.get_json(
        force=True
    ) or {}

    username = data.get(
        "username",
        ""
    ).strip()

    password = data.get(
        "password",
        ""
    ).strip()

    name = data.get(
        "name",
        ""
    ).strip()

    email = data.get(
        "email",
        ""
    ).strip()

    gender = data.get(
        "gender",
        "Male"
    ).strip()

    if (
        not username
        or not password
        or not name
        or not email
    ):

        return jsonify({
            "error":
                "All profile fields are required."
        }), 400

    conn = None

    try:

        conn = db()

        conn.execute(
            """
            INSERT INTO users
            (username, password, name, email, gender)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                username,
                password,
                name,
                email,
                gender,
            ),
        )

        conn.commit()

        return jsonify({
            "message":
                "Registration successful! "
                "Please login now."
        })

    except sqlite3.IntegrityError:

        return jsonify({
            "error":
                "Username already exists."
        }), 400

    except sqlite3.Error:

        return jsonify({
            "error":
                "Database error. "
                "Please try again later."
        }), 500

    finally:

        if conn:
            conn.close()


# ============================================================
# LOGIN
# ============================================================

@app.route(
    "/api/auth/login",
    methods=["POST"]
)
def login():

    data = request.get_json(
        force=True
    ) or {}

    username = data.get(
        "username",
        ""
    ).strip()

    password = data.get(
        "password",
        ""
    ).strip()

    conn = None

    try:

        conn = db()

        user = conn.execute(
            """
            SELECT *
            FROM users
            WHERE username = ?
            AND password = ?
            """,
            (
                username,
                password,
            ),
        ).fetchone()

        if user:

            session["user_id"] = user["id"]

            session["username"] = (
                user["username"]
            )

            streak = update_user_streak(
                user["id"]
            )

            return jsonify({
                "message":
                    "Login successful!",
                "streak":
                    streak,
            })

        return jsonify({
            "error":
                "Invalid username or password."
        }), 401

    except sqlite3.Error:

        return jsonify({
            "error":
                "Database is temporarily unavailable."
        }), 500

    finally:

        if conn:
            conn.close()


# ============================================================
# LOGOUT
# ============================================================

@app.route(
    "/api/auth/logout",
    methods=["POST"]
)
def logout():

    session.clear()

    return jsonify({
        "message":
            "Logged out successfully"
    })


# ============================================================
# VERIFY USER
# ============================================================

@app.route(
    "/api/auth/verify-user",
    methods=["POST"]
)
def verify_user():

    data = request.get_json(
        force=True
    ) or {}

    username = data.get(
        "username",
        ""
    ).strip()

    email = data.get(
        "email",
        ""
    ).strip()

    if not username or not email:

        return jsonify({
            "error":
                "Username and Email are both required."
        }), 400

    conn = None

    try:

        conn = db()

        user = conn.execute(
            """
            SELECT id
            FROM users
            WHERE username = ?
            AND email = ?
            """,
            (
                username,
                email,
            ),
        ).fetchone()

        if user:

            return jsonify({
                "message":
                    "User credentials verified."
            }), 200

        return jsonify({
            "error":
                "No user matches that username "
                "and email combination."
        }), 404

    except sqlite3.Error:

        return jsonify({
            "error":
                "Database connection error."
        }), 500

    finally:

        if conn:
            conn.close()


# ============================================================
# RESET PASSWORD
# ============================================================

@app.route(
    "/api/auth/reset-password",
    methods=["POST"]
)
def reset_password():

    data = request.get_json(
        force=True
    ) or {}

    username = data.get(
        "username",
        ""
    ).strip()

    password = data.get(
        "password",
        ""
    ).strip()

    if not username or not password:

        return jsonify({
            "error":
                "Missing critical parameters."
        }), 400

    conn = None

    try:

        conn = db()

        conn.execute(
            """
            UPDATE users
            SET password = ?
            WHERE username = ?
            """,
            (
                password,
                username,
            ),
        )

        conn.commit()

        return jsonify({
            "message":
                "Password updated successfully."
        })

    except sqlite3.Error:

        return jsonify({
            "error":
                "Failed to update password "
                "in database."
        }), 500

    finally:

        if conn:
            conn.close()


# ============================================================
# PROFILE GET
# ============================================================

@app.route(
    "/api/profile",
    methods=["GET"]
)
def get_profile():

    if "user_id" not in session:

        return jsonify({
            "error": "Unauthorized"
        }), 401

    conn = None

    try:

        conn = db()

        user = conn.execute(
            """
            SELECT
                username,
                name,
                email,
                gender,
                streak,
                last_active_date
            FROM users
            WHERE id = ?
            """,
            (
                session["user_id"],
            ),
        ).fetchone()

        if not user:

            return jsonify({
                "error":
                    "User not found."
            }), 404

        return jsonify(
            dict(user)
        )

    except sqlite3.Error:

        return jsonify({
            "error":
                "Could not read profile metadata."
        }), 500

    finally:

        if conn:
            conn.close()


# ============================================================
# PROFILE UPDATE
# ============================================================

@app.route(
    "/api/profile",
    methods=["POST"]
)
def update_profile():

    if "user_id" not in session:

        return jsonify({
            "error": "Unauthorized"
        }), 401

    data = request.get_json(
        force=True
    ) or {}

    name = data.get(
        "name",
        ""
    ).strip()

    email = data.get(
        "email",
        ""
    ).strip()

    gender = data.get(
        "gender",
        "Male"
    ).strip()

    password = data.get(
        "password",
        ""
    ).strip()

    if not name or not email:

        return jsonify({
            "error":
                "Name and Email are required."
        }), 400

    conn = None

    try:

        conn = db()

        if password:

            conn.execute(
                """
                UPDATE users
                SET name = ?,
                    email = ?,
                    gender = ?,
                    password = ?
                WHERE id = ?
                """,
                (
                    name,
                    email,
                    gender,
                    password,
                    session["user_id"],
                ),
            )

        else:

            conn.execute(
                """
                UPDATE users
                SET name = ?,
                    email = ?,
                    gender = ?
                WHERE id = ?
                """,
                (
                    name,
                    email,
                    gender,
                    session["user_id"],
                ),
            )

        conn.commit()

        return jsonify({
            "message":
                "Profile updated successfully!"
        })

    except sqlite3.Error:

        return jsonify({
            "error":
                "Could not save profile changes."
        }), 500

    finally:

        if conn:
            conn.close()


# ============================================================
# GENERATOR
# ============================================================

@app.route(
    "/api/generate",
    methods=["POST"]
)
def generate():

    if "user_id" not in session:

        return jsonify({
            "error": "Unauthorized"
        }), 401

    data = request.get_json(
        force=True
    ) or {}

    category = data.get(
        "category",
        "coding"
    )

    topic = (
        data.get("prompt") or ""
    ).strip()

    user_typed = topic

    # --------------------------------------------------------
    # RANDOM PROMPT
    # --------------------------------------------------------

    if not topic:

        topic = random.choice(
            BANK.get(
                category,
                BANK["coding"]
            )
        )

        user_typed = topic

    # --------------------------------------------------------
    # LIMIT EXTREMELY LARGE INPUT
    # --------------------------------------------------------

    if len(topic) > 6000:

        topic = topic[:6000]

    # --------------------------------------------------------
    # SIMPLE GENERATION INSTRUCTION
    # --------------------------------------------------------

    instruction = f"""
Create a high-quality AI prompt from the user's idea.

Category: {category}
Role: {ROLE.get(category, ROLE["custom"])}
User idea: {topic}

Requirements:
{SIMPLE_LANGUAGE_RULES}

Make the prompt specific enough that another AI can give
a useful answer.

Return ONLY the final prompt.
Do not add explanations, notes, headings, or commentary.
"""

    try:

        prompt = call_apinex(
            instruction
        )

    except Exception as e:

        return jsonify({
            "error": str(e)
        }), 500

    if not prompt:

        return jsonify({
            "error":
                "The AI returned an empty prompt. "
                "Please try again."
        }), 500

    # --------------------------------------------------------
    # SAVE HISTORY
    # --------------------------------------------------------

    conn = None

    try:

        conn = db()

        conn.execute(
            """
            INSERT INTO history
            (user_id, prompt, category, action, source_prompt)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                session["user_id"],
                prompt,
                category,
                "generated",
                user_typed,
            ),
        )

        conn.commit()

        return jsonify({
            "prompt": prompt,
            "category": category,
            "source_prompt": user_typed,
        })

    except sqlite3.Error:

        return jsonify({
            "error":
                "Could not save generation history."
        }), 500

    finally:

        if conn:
            conn.close()


# ============================================================
# ENHANCE
# ============================================================

@app.route(
    "/api/enhance",
    methods=["POST"]
)
def enhance():

    if "user_id" not in session:

        return jsonify({
            "error": "Unauthorized"
        }), 401

    data = request.get_json(
        force=True
    ) or {}

    text = (
        data.get("prompt") or ""
    ).strip()

    category = data.get(
        "category",
        "coding"
    )

    if not text:

        return jsonify({
            "error":
                "Prompt text is required"
        }), 400

    # --------------------------------------------------------
    # LIMIT VERY LARGE INPUT
    # --------------------------------------------------------

    if len(text) > 6000:

        text = text[:6000]

    # --------------------------------------------------------
    # IMPROVE PROMPT
    # --------------------------------------------------------

    instruction = f"""
Improve the following AI prompt while keeping its
original meaning.

Category: {category}
Role: {ROLE.get(category, ROLE["custom"])}
Original prompt: {text}

Requirements:
{SIMPLE_LANGUAGE_RULES}

Make the prompt clearer, more specific, and more useful.

Return ONLY the improved prompt.
Do not explain the changes.
Do not add notes or commentary.
"""

    try:

        improved = call_apinex(
            instruction
        )

    except Exception as e:

        return jsonify({
            "error": str(e)
        }), 500

    if not improved:

        return jsonify({
            "error":
                "The AI returned an empty prompt. "
                "Please try again."
        }), 500

    # --------------------------------------------------------
    # SAVE HISTORY
    # --------------------------------------------------------

    conn = None

    try:

        conn = db()

        conn.execute(
            """
            INSERT INTO history
            (user_id, prompt, category, action, source_prompt)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                session["user_id"],
                improved,
                category,
                "enhanced",
                text,
            ),
        )

        conn.commit()

        return jsonify({
            "prompt": improved,
            "category": category,
            "source_prompt": text,
        })

    except sqlite3.Error:

        return jsonify({
            "error":
                "Could not save optimization data."
        }), 500

    finally:

        if conn:
            conn.close()


# ============================================================
# HISTORY
# ============================================================

@app.route(
    "/api/history",
    methods=["GET"]
)
def get_history():

    if "user_id" not in session:

        return jsonify([]), 401

    conn = None

    try:

        conn = db()

        rows = conn.execute(
            """
            SELECT *
            FROM history
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT 50
            """,
            (
                session["user_id"],
            ),
        ).fetchall()

        return jsonify([
            dict(r)
            for r in rows
        ])

    except sqlite3.Error:

        return jsonify([]), 500

    finally:

        if conn:
            conn.close()


# ============================================================
# DELETE HISTORY ITEM
# ============================================================

@app.route(
    "/api/history/<int:hid>",
    methods=["DELETE"]
)
def delete_history_item(hid):

    if "user_id" not in session:

        return jsonify({
            "error": "Unauthorized"
        }), 401

    conn = None

    try:

        conn = db()

        conn.execute(
            """
            DELETE FROM history
            WHERE id = ?
            AND user_id = ?
            """,
            (
                hid,
                session["user_id"],
            ),
        )

        conn.commit()

        return jsonify({
            "message":
                "Removed from History"
        })

    except sqlite3.Error:

        return jsonify({
            "error":
                "Could not delete history item."
        }), 500

    finally:

        if conn:
            conn.close()


# ============================================================
# CLEAR HISTORY
# ============================================================

@app.route(
    "/api/history/clear",
    methods=["DELETE"]
)
def clear_history():

    if "user_id" not in session:

        return jsonify({
            "error": "Unauthorized"
        }), 401

    conn = None

    try:

        conn = db()

        conn.execute(
            """
            DELETE FROM history
            WHERE user_id = ?
            """,
            (
                session["user_id"],
            ),
        )

        conn.commit()

        return jsonify({
            "message":
                "History cleared"
        })

    except sqlite3.Error:

        return jsonify({
            "error":
                "Could not purge database rows."
        }), 500

    finally:

        if conn:
            conn.close()


# ============================================================
# FAVORITES
# ============================================================

@app.route(
    "/api/favorites",
    methods=["GET", "POST"]
)
def handle_favorites():

    if "user_id" not in session:

        return jsonify([]), 401

    conn = None

    try:

        conn = db()

        if request.method == "POST":

            data = request.get_json(
                force=True
            ) or {}

            prompt = data.get(
                "prompt",
                ""
            ).strip()

            if not prompt:

                return jsonify({
                    "error":
                        "Prompt is required."
                }), 400

            cur = conn.execute(
                """
                INSERT OR IGNORE INTO favorites
                (user_id, prompt, category, source_prompt)
                VALUES (?, ?, ?, ?)
                """,
                (
                    session["user_id"],
                    prompt,
                    data.get(
                        "category",
                        "custom"
                    ),
                    data.get(
                        "source_prompt",
                        prompt
                    ),
                ),
            )

            conn.commit()

            already_existed = (
                cur.rowcount == 0
            )

            if already_existed:

                return jsonify({
                    "message":
                        "Already in Favorites",
                    "duplicate": True,
                })

            return jsonify({
                "message":
                    "Added to Favorites",
                "duplicate": False,
            })

        rows = conn.execute(
            """
            SELECT *
            FROM favorites
            WHERE user_id = ?
            ORDER BY id DESC
            """,
            (
                session["user_id"],
            ),
        ).fetchall()

        return jsonify([
            dict(r)
            for r in rows
        ])

    except sqlite3.Error:

        return jsonify({
            "error":
                "Favorites storage connection failed."
        }), 500

    finally:

        if conn:
            conn.close()


# ============================================================
# DELETE FAVORITE
# ============================================================

@app.route(
    "/api/favorites/<int:fid>",
    methods=["DELETE"]
)
def delete_favorite(fid):

    if "user_id" not in session:

        return jsonify({
            "error": "Unauthorized"
        }), 401

    conn = None

    try:

        conn = db()

        conn.execute(
            """
            DELETE FROM favorites
            WHERE id = ?
            AND user_id = ?
            """,
            (
                fid,
                session["user_id"],
            ),
        )

        conn.commit()

        return jsonify({
            "message":
                "Removed from Favorites"
        })

    except sqlite3.Error:

        return jsonify({
            "error":
                "Could not remove favorite item."
        }), 500

    finally:

        if conn:
            conn.close()


# ============================================================
# SAVED
# ============================================================

@app.route(
    "/api/saved",
    methods=["GET", "POST"]
)
def handle_saved():

    if "user_id" not in session:

        return jsonify([]), 401

    conn = None

    try:

        conn = db()

        if request.method == "POST":

            data = request.get_json(
                force=True
            ) or {}

            prompt = data.get(
                "prompt",
                ""
            ).strip()

            if not prompt:

                return jsonify({
                    "error":
                        "Prompt is required."
                }), 400

            cur = conn.execute(
                """
                INSERT OR IGNORE INTO saved
                (user_id, prompt, category, source_prompt)
                VALUES (?, ?, ?, ?)
                """,
                (
                    session["user_id"],
                    prompt,
                    data.get(
                        "category",
                        "custom"
                    ),
                    data.get(
                        "source_prompt",
                        prompt
                    ),
                ),
            )

            conn.commit()

            already_existed = (
                cur.rowcount == 0
            )

            if already_existed:

                return jsonify({
                    "message":
                        "Already in Saved",
                    "duplicate": True,
                })

            return jsonify({
                "message":
                    "Saved successfully",
                "duplicate": False,
            })

        rows = conn.execute(
            """
            SELECT *
            FROM saved
            WHERE user_id = ?
            ORDER BY id DESC
            """,
            (
                session["user_id"],
            ),
        ).fetchall()

        return jsonify([
            dict(r)
            for r in rows
        ])

    except sqlite3.Error:

        return jsonify({
            "error":
                "Saved storage connection failed."
        }), 500

    finally:

        if conn:
            conn.close()


# ============================================================
# DELETE SAVED
# ============================================================

@app.route(
    "/api/saved/<int:sid>",
    methods=["DELETE"]
)
def delete_saved(sid):

    if "user_id" not in session:

        return jsonify({
            "error": "Unauthorized"
        }), 401

    conn = None

    try:

        conn = db()

        conn.execute(
            """
            DELETE FROM saved
            WHERE id = ?
            AND user_id = ?
            """,
            (
                sid,
                session["user_id"],
            ),
        )

        conn.commit()

        return jsonify({
            "message":
                "Removed from Saved"
        })

    except sqlite3.Error:

        return jsonify({
            "error":
                "Could not delete saved item."
        }), 500

    finally:

        if conn:
            conn.close()


# ============================================================
# SETTINGS - PURGE DATABASES
# ============================================================

@app.route(
    "/api/settings/purge-databases",
    methods=["DELETE"]
)
def purge_databases():

    if "user_id" not in session:

        return jsonify({
            "error": "Unauthorized"
        }), 401

    conn = None

    try:

        conn = db()

        uid = session["user_id"]

        conn.execute(
            """
            DELETE FROM history
            WHERE user_id = ?
            """,
            (uid,),
        )

        conn.execute(
            """
            DELETE FROM favorites
            WHERE user_id = ?
            """,
            (uid,),
        )

        conn.execute(
            """
            DELETE FROM saved
            WHERE user_id = ?
            """,
            (uid,),
        )

        conn.commit()

        return jsonify({
            "message":
                "All prompts and collections cleared."
        })

    except sqlite3.Error:

        return jsonify({
            "error":
                "Failed to clear prompt data."
        }), 500

    finally:

        if conn:
            conn.close()


# ============================================================
# SETTINGS - DELETE ACCOUNT
# ============================================================

@app.route(
    "/api/settings/delete-account",
    methods=["DELETE"]
)
def delete_account():

    if "user_id" not in session:

        return jsonify({
            "error": "Unauthorized"
        }), 401

    uid = session["user_id"]

    conn = None

    try:

        conn = db()

        conn.execute(
            """
            DELETE FROM history
            WHERE user_id = ?
            """,
            (uid,),
        )

        conn.execute(
            """
            DELETE FROM favorites
            WHERE user_id = ?
            """,
            (uid,),
        )

        conn.execute(
            """
            DELETE FROM saved
            WHERE user_id = ?
            """,
            (uid,),
        )

        conn.execute(
            """
            DELETE FROM users
            WHERE id = ?
            """,
            (uid,),
        )

        conn.commit()

        session.clear()

        return jsonify({
            "message":
                "Account and associated data deleted."
        })

    except sqlite3.Error:

        return jsonify({
            "error":
                "Failed to delete account."
        }), 500

    finally:

        if conn:
            conn.close()


# ============================================================
# INITIALIZE DATABASE
# ============================================================

init_db()


# ============================================================
# START APPLICATION
# ============================================================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
    )
