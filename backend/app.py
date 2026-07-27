import hashlib
import json
import os
import random
import re

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo
from word_list import WORDS
import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

try:
    import firebase_admin
    from firebase_admin import auth as firebase_auth
    from firebase_admin import credentials, firestore
except ImportError:  # pragma: no cover
    firebase_admin = None
    firebase_auth = None
    credentials = None
    firestore = None


# ---------------------------------------------------------------------------
# Environment and application setup
# ---------------------------------------------------------------------------

BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BACKEND_DIR.parent
load_dotenv(PROJECT_ROOT / ".env")

app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False

FRONTEND_DIST_DIR = (BACKEND_DIR / "frontend_dist").resolve()
if not FRONTEND_DIST_DIR.exists():
    FRONTEND_DIST_DIR = (PROJECT_ROOT / "frontend" / "dist").resolve()

frontend_origins = [
    origin.strip()
    for origin in os.getenv("FRONTEND_ORIGINS", "http://localhost:5173").split(",")
    if origin.strip()
]
CORS(
    app,
    resources={r"/api/*": {"origins": frontend_origins}},
    supports_credentials=True,
)

SINGAPORE_TZ = ZoneInfo("Asia/Singapore")
WORD_LENGTH = 5
MAX_ATTEMPTS = 6

DICTIONARY_API_BASE_URL = "https://api.dictionaryapi.dev/api/v2/entries/en"
DICTIONARY_API_TIMEOUT_SECONDS = 8
LOCAL_WORD_SELECTION_ATTEMPTS = 100

ADMIN_REGISTRATION_KEY = os.getenv("ADMIN_REGISTRATION_KEY", "").strip()

# Normalize the local list once when Flask starts.
# word_list.py must export a variable named `WORDS`.
ANSWER_WORDS = tuple(
    sorted(
        {
            str(word).strip().lower()
            for word in WORDS
            if isinstance(word, str)
            and re.fullmatch(r"[a-zA-Z]{5}", word.strip())
        }
    )
)
ANSWER_WORD_SET = frozenset(ANSWER_WORDS)

if not ANSWER_WORDS:
    raise RuntimeError(
        "word_list.py did not provide any valid five-letter words in `words`"
    )

FIREBASE_CONFIGURED = False
FIRESTORE_DB = None


# ---------------------------------------------------------------------------
# Firebase Admin and Firestore
# ---------------------------------------------------------------------------

def _resolve_credentials_path(raw_path: str) -> Path | None:
    value = raw_path.strip()
    if not value:
        return None

    path = Path(value).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path

    return path.resolve()


def _initialize_firebase() -> None:
    global FIREBASE_CONFIGURED, FIRESTORE_DB

    if (
        firebase_admin is None
        or firebase_auth is None
        or credentials is None
        or firestore is None
    ):
        app.logger.error(
            "Firebase Admin dependencies are unavailable. "
            "Install firebase-admin and google-cloud-firestore."
        )
        return

    try:
        if not firebase_admin._apps:
            credentials_path = _resolve_credentials_path(
                os.getenv("FIREBASE_CREDENTIALS_PATH", "")
            )
            credentials_json = os.getenv(
                "FIREBASE_CREDENTIALS_JSON", "").strip()

            if credentials_path and credentials_path.exists():
                credential = credentials.Certificate(str(credentials_path))
            elif credentials_json:
                credential = credentials.Certificate(
                    json.loads(credentials_json))
            else:
                app.logger.error(
                    "Firebase credentials were not found. Checked path: %s",
                    credentials_path,
                )
                return

            # Firestore does not require a Realtime Database URL.
            firebase_admin.initialize_app(credential)

        FIRESTORE_DB = firestore.client()
        FIREBASE_CONFIGURED = True
        app.logger.info(
            "Firebase Admin and Firestore initialized successfully.")

    except Exception as error:
        FIREBASE_CONFIGURED = False
        FIRESTORE_DB = None
        app.logger.exception("Firebase initialization failed: %s", error)


_initialize_firebase()


def _require_firestore():
    if not FIREBASE_CONFIGURED or FIRESTORE_DB is None:
        return jsonify({"error": "Firestore is not configured"}), 503
    return None


@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve_frontend(path: str):
    if path.startswith("api/"):
        return jsonify({"error": "Not Found"}), 404

    if path and (FRONTEND_DIST_DIR / path).exists() and (FRONTEND_DIST_DIR / path).is_file():
        return send_from_directory(FRONTEND_DIST_DIR, path)

    return send_from_directory(FRONTEND_DIST_DIR, "index.html")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _normalize_email(email: str | None) -> str:
    return (email or "").strip().lower()


def _user_key(email: str) -> str:
    return hashlib.sha256(_normalize_email(email).encode("utf-8")).hexdigest()


def _today_key() -> str:
    return datetime.now(SINGAPORE_TZ).date().isoformat()


def _yesterday_key() -> str:
    return (datetime.now(SINGAPORE_TZ).date() - timedelta(days=1)).isoformat()


def _now_iso() -> str:
    return datetime.now(SINGAPORE_TZ).isoformat()


def _document(collection_name: str, document_id: str):
    if FIRESTORE_DB is None:
        raise RuntimeError("Firestore is not configured")
    return FIRESTORE_DB.collection(collection_name).document(document_id)


def _read_document(collection_name: str, document_id: str) -> dict[str, Any] | None:
    snapshot = _document(collection_name, document_id).get()
    if not snapshot.exists:
        return None
    value = snapshot.to_dict()
    return value if isinstance(value, dict) else None


def _write_document(
    collection_name: str,
    document_id: str,
    value: dict[str, Any],
    *,
    merge: bool = False,
) -> None:
    _document(collection_name, document_id).set(value, merge=merge)


# ---------------------------------------------------------------------------
# Local Wordle answers and DictionaryAPI.dev guess validation
# ---------------------------------------------------------------------------

def _dictionary_api_is_real_word(word: str) -> bool:
    """
    Validate a player's guess using DictionaryAPI.dev.

    Results are cached in Firestore so the same word does not require another
    external API call on later guesses.
    """
    normalized = word.strip().lower()
    if not re.fullmatch(r"[a-z]{5}", normalized):
        return False

    cached = _read_document("wordleDictionaryCache", normalized)
    if isinstance(cached, dict) and isinstance(cached.get("valid"), bool):
        return cached["valid"]

    response = requests.get(
        f"{DICTIONARY_API_BASE_URL}/{normalized}",
        timeout=DICTIONARY_API_TIMEOUT_SECONDS,
    )

    if response.status_code == 404:
        valid = False
    else:
        response.raise_for_status()
        payload = response.json()
        valid = (
            isinstance(payload, list)
            and len(payload) > 0
            and isinstance(payload[0], dict)
            and bool(payload[0].get("word"))
        )

    _write_document(
        "wordleDictionaryCache",
        normalized,
        {
            "valid": valid,
            "checkedAt": _now_iso(),
            "source": "dictionaryapi.dev",
        },
    )
    return valid


def _claim_daily_answer(date_key: str, candidate: str) -> str | None:
    """
    Atomically create today's Wordle and permanently mark its answer as used.

    Returns:
        - the stored answer if today's game already exists;
        - the candidate if it was successfully claimed;
        - None if another day already used the candidate.
    """
    if FIRESTORE_DB is None or firestore is None:
        raise RuntimeError("Firestore is not configured")

    normalized_candidate = candidate.strip().lower()
    if normalized_candidate not in ANSWER_WORD_SET:
        raise ValueError("Candidate is not in the local answer list")

    answer = normalized_candidate.upper()
    daily_ref = _document("wordleDaily", date_key)
    used_ref = _document("wordleUsedAnswers", normalized_candidate)
    transaction = FIRESTORE_DB.transaction()

    @firestore.transactional
    def run_transaction(transaction):
        daily_snapshot = daily_ref.get(transaction=transaction)

        # Multiple users may request the game simultaneously. Everyone must
        # receive the same answer after the first request creates it.
        if daily_snapshot.exists:
            current = daily_snapshot.to_dict() or {}
            stored_answer = str(current.get("answer") or "").upper()
            return stored_answer or None

        used_snapshot = used_ref.get(transaction=transaction)
        if used_snapshot.exists:
            return None

        now = _now_iso()
        transaction.set(
            daily_ref,
            {
                "answer": answer,
                "date": date_key,
                "createdAt": now,
                "source": "local_word_list",
            },
        )
        transaction.set(
            used_ref,
            {
                "answer": answer,
                "firstUsedDate": date_key,
                "createdAt": now,
            },
        )
        return answer

    return run_transaction(transaction)


def _create_daily_answer(date_key: str) -> str:
    """
    Randomly select an unused answer from word_list.py.

    Fast random attempts are tried first. If many words have already been used,
    the function falls back to loading the used-answer IDs and choosing from
    the exact remaining pool.
    """
    # Fast path: avoids reading the entire used-answer collection while most
    # of the 3,000+ local words are still unused.
    for _ in range(LOCAL_WORD_SELECTION_ATTEMPTS):
        candidate = random.choice(ANSWER_WORDS)
        answer = _claim_daily_answer(date_key, candidate)
        if answer:
            return answer

    # Fallback for when the pool is becoming crowded.
    used_snapshots = FIRESTORE_DB.collection("wordleUsedAnswers").stream()
    used_words = {
        snapshot.id.strip().lower()
        for snapshot in used_snapshots
    }
    remaining_words = list(ANSWER_WORD_SET - used_words)

    if not remaining_words:
        raise RuntimeError(
            "Every word in word_list.py has already been used as an answer"
        )

    # Shuffle so transaction collisions do not repeatedly test the same order.
    random.shuffle(remaining_words)

    for candidate in remaining_words:
        answer = _claim_daily_answer(date_key, candidate)
        if answer:
            return answer

    # A concurrent request may have created today's answer while this request
    # was checking candidates.
    stored = _read_document("wordleDaily", date_key)
    if stored and stored.get("answer"):
        return str(stored["answer"]).upper()

    raise RuntimeError("Could not claim a unique daily Wordle answer")


def _daily_answer(date_key: str) -> str:
    stored = _read_document("wordleDaily", date_key)
    if stored and stored.get("answer"):
        return str(stored["answer"]).upper()

    return _create_daily_answer(date_key)


# ---------------------------------------------------------------------------
# User and Wordle persistence
# ---------------------------------------------------------------------------

def _get_user(email: str) -> dict[str, Any] | None:
    normalized = _normalize_email(email)
    if not normalized:
        return None
    return _read_document("users", _user_key(normalized))


def _save_user(email: str, user_data: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_email(email)
    if not normalized:
        raise ValueError("Email is required")

    _write_document("users", _user_key(normalized), user_data)
    return user_data


def _default_wordle_stats(email: str) -> dict[str, Any]:
    return {
        "email": _normalize_email(email),
        "rankScore": 0,
        "combo": 0,
        "wins": 0,
        "gamesPlayed": 0,
        "bestCombo": 0,
        "lastPlayedDate": None,
        "lastWinDate": None,
        "daily": {},
    }


def _get_wordle_stats(email: str) -> dict[str, Any]:
    document_id = _user_key(email)
    value = _read_document("wordleUsers", document_id)

    if not isinstance(value, dict):
        value = _default_wordle_stats(email)
        _write_document("wordleUsers", document_id, value)

    last_win = value.get("lastWinDate")
    if last_win and last_win not in {_today_key(), _yesterday_key()}:
        value["combo"] = 0
        _write_document("wordleUsers", document_id, value)

    return value


def _save_wordle_stats(email: str, stats: dict[str, Any]) -> None:
    _write_document("wordleUsers", _user_key(email), stats)


def _require_registered_user(email: str):
    user = _get_user(email)
    if not user:
        return None, (jsonify({"error": "Registered user not found"}), 404)
    return user, None


def _letter_statuses(guess: str, answer: str) -> list[str]:
    statuses = ["absent"] * WORD_LENGTH
    remaining = list(answer)

    for index in range(WORD_LENGTH):
        if guess[index] == answer[index]:
            statuses[index] = "correct"
            remaining[index] = ""

    for index in range(WORD_LENGTH):
        if statuses[index] == "correct":
            continue

        if guess[index] in remaining:
            statuses[index] = "present"
            remaining[remaining.index(guess[index])] = ""

    return statuses


def _public_stats(stats: dict[str, Any]) -> dict[str, Any]:
    return {
        "rankScore": int(stats.get("rankScore", 0)),
        "combo": int(stats.get("combo", 0)),
        "wins": int(stats.get("wins", 0)),
        "gamesPlayed": int(stats.get("gamesPlayed", 0)),
        "bestCombo": int(stats.get("bestCombo", 0)),
        "lastPlayedDate": stats.get("lastPlayedDate"),
        "lastWinDate": stats.get("lastWinDate"),
    }


# ---------------------------------------------------------------------------
# Firebase token helpers
# ---------------------------------------------------------------------------

def _get_bearer_token() -> str | None:
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        return None
    return authorization.removeprefix("Bearer ").strip() or None


def _verify_firebase_user():
    if not FIREBASE_CONFIGURED or firebase_auth is None:
        return None, (
            jsonify({"error": "Firebase authentication is unavailable"}),
            503,
        )

    token = _get_bearer_token()
    if not token:
        return None, (jsonify({"error": "Firebase ID token is required"}), 401)

    try:
        return firebase_auth.verify_id_token(token), None
    except Exception as error:
        app.logger.warning("Firebase token verification failed: %s", error)
        return None, (
            jsonify({"error": "Invalid or expired Firebase ID token"}),
            401,
        )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
def health_check():
    return jsonify(
        {
            "status": "ok",
            "firebase_configured": FIREBASE_CONFIGURED,
            "storage": "firestore" if FIREBASE_CONFIGURED else "unavailable",
            "dictionary_api": "dictionaryapi.dev",
            "local_answer_word_count": len(ANSWER_WORDS),
            "admin_registration_configured": bool(ADMIN_REGISTRATION_KEY),
        }
    )


@app.post("/api/admin/users")
def admin_create_user():
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error

    if not ADMIN_REGISTRATION_KEY:
        return jsonify(
            {"error": "ADMIN_REGISTRATION_KEY is not configured"}
        ), 503

    provided_key = request.headers.get("X-Admin-Key", "").strip()
    if provided_key != ADMIN_REGISTRATION_KEY:
        return jsonify({"error": "Invalid admin key"}), 403

    if firebase_auth is None:
        return jsonify({"error": "Firebase Admin authentication is unavailable"}), 503

    payload = request.get_json(silent=True) or {}
    email = _normalize_email(payload.get("email"))
    temporary_password = str(payload.get("temporaryPassword") or "")
    display_name = str(payload.get("displayName") or "").strip() or None

    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return jsonify({"error": "Enter a valid email address"}), 400

    if len(temporary_password) < 8:
        return jsonify(
            {"error": "Temporary password must be at least 8 characters"}
        ), 400

    created_user = None

    try:
        created_user = firebase_auth.create_user(
            email=email,
            password=temporary_password,
            display_name=display_name,
            email_verified=False,
        )

        firebase_auth.set_custom_user_claims(
            created_user.uid,
            {"mustResetPassword": True},
        )

        now = _now_iso()
        _save_user(
            email,
            {
                "uid": created_user.uid,
                "email": email,
                "displayName": display_name,
                "mustResetPassword": True,
                "createdAt": now,
            },
        )
        _save_wordle_stats(email, _default_wordle_stats(email))

    except firebase_auth.EmailAlreadyExistsError:
        return jsonify(
            {"error": "A Firebase user already exists with this email"}
        ), 409
    except Exception as error:
        app.logger.exception("Manual user creation failed: %s", error)

        # Avoid leaving an orphaned Authentication account if Firestore failed.
        if created_user is not None:
            try:
                firebase_auth.delete_user(created_user.uid)
            except Exception:
                app.logger.exception(
                    "Could not roll back Firebase Authentication user creation"
                )

        return jsonify({"error": "Could not create user"}), 500

    return jsonify(
        {
            "message": "User created with a temporary password",
            "user": {
                "uid": created_user.uid,
                "email": email,
                "displayName": display_name,
                "mustResetPassword": True,
            },
        }
    ), 201


@app.get("/api/auth/session")
def auth_session():
    decoded, error = _verify_firebase_user()
    if error:
        return error

    email = _normalize_email(decoded.get("email"))
    return jsonify(
        {
            "uid": decoded.get("uid"),
            "email": email,
            "mustResetPassword": bool(
                decoded.get("mustResetPassword", False)
            ),
        }
    )


@app.post("/api/auth/complete-password-reset")
def complete_password_reset():
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error

    decoded, error = _verify_firebase_user()
    if error:
        return error

    uid = decoded["uid"]
    email = _normalize_email(decoded.get("email"))

    try:
        firebase_auth.set_custom_user_claims(
            uid,
            {"mustResetPassword": False},
        )

        user = _get_user(email) or {"uid": uid, "email": email}
        user["mustResetPassword"] = False
        user["passwordResetAt"] = _now_iso()
        _save_user(email, user)

    except Exception as error:
        app.logger.exception("Could not complete password reset: %s", error)
        return jsonify({"error": "Could not complete password reset"}), 500

    return jsonify({"message": "Password reset requirement cleared"})


@app.get("/api/games/wordle")
def get_wordle_game():
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error

    email = _normalize_email(request.args.get("email"))
    if not email:
        return jsonify({"error": "email is required"}), 400

    _, error = _require_registered_user(email)
    if error:
        return error

    date_key = _today_key()

    try:
        answer = _daily_answer(date_key)
    except (requests.RequestException, ValueError, RuntimeError) as error:
        app.logger.exception("Daily Wordle generation failed: %s", error)
        return jsonify(
            {
                "error": "Could not prepare today's Wordle. Please try again.",
                "code": "daily_word_unavailable",
            }
        ), 503

    stats = _get_wordle_stats(email)
    daily = stats.setdefault("daily", {})
    today_game = daily.get(date_key, {})

    guesses = today_game.get("guesses", [])
    status = today_game.get("status", "playing")

    response = {
        "date": date_key,
        "status": status,
        "guesses": guesses,
        "evaluations": [
            _letter_statuses(guess, answer)
            for guess in guesses
        ],
        "stats": _public_stats(stats),
    }

    if status in {"won", "lost"}:
        response["answer"] = answer

    return jsonify(response)


@app.post("/api/games/wordle/guess")
def submit_wordle_guess():
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error

    payload = request.get_json(silent=True) or {}
    email = _normalize_email(payload.get("email"))
    guess = str(payload.get("guess") or "").strip().upper()

    if not email:
        return jsonify({"error": "email is required"}), 400

    if not re.fullmatch(r"[A-Z]{5}", guess):
        return jsonify(
            {"error": "Guess must contain exactly five letters"}
        ), 400

    _, error = _require_registered_user(email)
    if error:
        return error

    try:
        is_real_word = _dictionary_api_is_real_word(guess)
    except (requests.RequestException, ValueError, RuntimeError) as error:
        app.logger.exception("Word validation failed: %s", error)
        return jsonify(
            {
                "error": "The dictionary service is unavailable. Please try again.",
                "code": "dictionary_unavailable",
            }
        ), 503

    if not is_real_word:
        return jsonify(
            {
                "error": (
                    "That is not a recognised five-letter word. "
                    "Enter another word."
                ),
                "code": "invalid_word",
            }
        ), 400

    date_key = _today_key()

    try:
        answer = _daily_answer(date_key)
    except (requests.RequestException, ValueError, RuntimeError) as error:
        app.logger.exception("Daily Wordle retrieval failed: %s", error)
        return jsonify(
            {
                "error": "Could not load today's Wordle. Please try again.",
                "code": "daily_word_unavailable",
            }
        ), 503

    stats = _get_wordle_stats(email)
    daily = stats.setdefault("daily", {})
    today_game = daily.setdefault(
        date_key,
        {
            "guesses": [],
            "status": "playing",
        },
    )

    if today_game.get("status") != "playing":
        return jsonify(
            {"error": "You have already completed today's Wordle"}
        ), 409

    guesses = today_game.setdefault("guesses", [])
    if len(guesses) >= MAX_ATTEMPTS:
        return jsonify({"error": "No attempts remaining"}), 409

    guesses.append(guess)
    attempts_used = len(guesses)
    did_win = guess == answer
    did_lose = not did_win and attempts_used >= MAX_ATTEMPTS
    points_gained = 0

    if did_win:
        previous_win = stats.get("lastWinDate")
        new_combo = (
            int(stats.get("combo", 0)) + 1
            if previous_win == _yesterday_key()
            else 1
        )

        points_gained = (MAX_ATTEMPTS - attempts_used) * new_combo

        stats["rankScore"] = max(
            0,
            int(stats.get("rankScore", 0)) + points_gained,
        )
        stats["combo"] = new_combo
        stats["bestCombo"] = max(
            int(stats.get("bestCombo", 0)),
            new_combo,
        )
        stats["wins"] = int(stats.get("wins", 0)) + 1
        stats["gamesPlayed"] = int(stats.get("gamesPlayed", 0)) + 1
        stats["lastPlayedDate"] = date_key
        stats["lastWinDate"] = date_key

        today_game["status"] = "won"
        today_game["pointsGained"] = points_gained
        today_game["completedAt"] = _now_iso()

    elif did_lose:
        stats["combo"] = 0
        stats["gamesPlayed"] = int(stats.get("gamesPlayed", 0)) + 1
        stats["lastPlayedDate"] = date_key

        today_game["status"] = "lost"
        today_game["pointsGained"] = 0
        today_game["completedAt"] = _now_iso()

    _save_wordle_stats(email, stats)

    response = {
        "guess": guess,
        "evaluation": _letter_statuses(guess, answer),
        "status": today_game["status"],
        "attemptsUsed": attempts_used,
        "pointsGained": points_gained,
        "stats": _public_stats(stats),
    }

    if today_game["status"] in {"won", "lost"}:
        response["answer"] = answer

    return jsonify(response)


if __name__ == "__main__":
    app.run(
        debug=os.getenv("FLASK_DEBUG", "false").lower() == "true",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5000")),
    )
