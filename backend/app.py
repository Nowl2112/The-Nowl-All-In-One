import json
import os
import random
import re

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from word_list import WORDS

import hashlib
import secrets
from html import escape

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
    for origin in os.getenv(
        "FRONTEND_ORIGINS",
        "http://localhost:5173",
    ).split(",")
    if origin.strip()
]

CORS(
    app,
    resources={r"/api/*": {"origins": frontend_origins}},
    supports_credentials=False,
)

try:
    SINGAPORE_TZ = ZoneInfo("Asia/Singapore")
except Exception:
    # Prevent the server from failing on systems without tzdata installed.
    from datetime import timezone

    SINGAPORE_TZ = timezone(timedelta(hours=8))

WORD_LENGTH = 5
MAX_ATTEMPTS = 6

DICTIONARY_API_BASE_URL = (
    "https://api.dictionaryapi.dev/api/v2/entries/en"
)
DICTIONARY_API_TIMEOUT_SECONDS = 8
LOCAL_WORD_SELECTION_ATTEMPTS = 100

ADMIN_REGISTRATION_KEY = os.getenv(
    "ADMIN_REGISTRATION_KEY",
    "",
).strip()

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
        "word_list.py did not provide any valid five-letter words in WORDS"
    )

FIREBASE_CONFIGURED = False
FIRESTORE_DB = None

# ---------------------------------------------------------------------------
# Telegram configuration
# ---------------------------------------------------------------------------

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    "",
).strip()

TELEGRAM_BOT_USERNAME = os.getenv(
    "TELEGRAM_BOT_USERNAME",
    "",
).strip().removeprefix("@")

TELEGRAM_WEBHOOK_SECRET = os.getenv(
    "TELEGRAM_WEBHOOK_SECRET",
    "",
).strip()

TELEGRAM_CRON_SECRET = os.getenv(
    "TELEGRAM_CRON_SECRET",
    "",
).strip()

APP_PUBLIC_URL = os.getenv(
    "APP_PUBLIC_URL",
    "",
).strip().rstrip("/")

TELEGRAM_API_BASE_URL = (
    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
    if TELEGRAM_BOT_TOKEN
    else ""
)

TELEGRAM_LINK_TOKEN_LIFETIME_MINUTES = 15
TELEGRAM_REMINDER_DAYS_AHEAD = 7
TELEGRAM_REQUEST_TIMEOUT_SECONDS = 15


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
                "FIREBASE_CREDENTIALS_JSON",
                "",
            ).strip()

            if credentials_path and credentials_path.exists():
                credential = credentials.Certificate(
                    str(credentials_path)
                )
            elif credentials_json:
                credential = credentials.Certificate(
                    json.loads(credentials_json)
                )
            else:
                app.logger.error(
                    "Firebase credentials were not found. Checked path: %s",
                    credentials_path,
                )
                return

            firebase_admin.initialize_app(credential)

        FIRESTORE_DB = firestore.client()
        FIREBASE_CONFIGURED = True
        app.logger.info(
            "Firebase Admin and Firestore initialized successfully."
        )

    except Exception as error:
        FIREBASE_CONFIGURED = False
        FIRESTORE_DB = None
        app.logger.exception(
            "Firebase initialization failed: %s",
            error,
        )


_initialize_firebase()


# ---------------------------------------------------------------------------
# Frontend serving
# ---------------------------------------------------------------------------

@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve_frontend(path: str):
    if path.startswith("api/"):
        return jsonify({"error": "Not Found"}), 404

    requested_file = FRONTEND_DIST_DIR / path

    if (
        path
        and requested_file.exists()
        and requested_file.is_file()
    ):
        return send_from_directory(FRONTEND_DIST_DIR, path)

    index_file = FRONTEND_DIST_DIR / "index.html"
    if not index_file.exists():
        return jsonify(
            {
                "error": (
                    "Frontend build not found. Run the frontend development "
                    "server or build the frontend first."
                )
            }
        ), 404

    return send_from_directory(
        FRONTEND_DIST_DIR,
        "index.html",
    )


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _normalize_email(email: str | None) -> str:
    return (email or "").strip().lower()


def _profile_picture_link(user: dict[str, Any] | None) -> str:
    if not isinstance(user, dict):
        return ""

    # Firestore uses profile_pic_link. The camelCase fallback keeps the
    # backend compatible if an older document used the frontend field name.
    return str(
        user.get("profile_pic_link")
        or user.get("profilePicLink")
        or ""
    ).strip()


def _today_key() -> str:
    return datetime.now(SINGAPORE_TZ).date().isoformat()


def _yesterday_key() -> str:
    return (
        datetime.now(SINGAPORE_TZ).date() - timedelta(days=1)
    ).isoformat()


def _now_iso() -> str:
    return datetime.now(SINGAPORE_TZ).isoformat()


def _document(collection_name: str, document_id: str):
    if FIRESTORE_DB is None:
        raise RuntimeError("Firestore is not configured")

    return FIRESTORE_DB.collection(
        collection_name
    ).document(document_id)


def _read_document(
    collection_name: str,
    document_id: str,
) -> dict[str, Any] | None:
    snapshot = _document(
        collection_name,
        document_id,
    ).get()

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
    _document(
        collection_name,
        document_id,
    ).set(value, merge=merge)


def _require_firestore():
    if not FIREBASE_CONFIGURED or FIRESTORE_DB is None:
        return jsonify(
            {"error": "Firestore is not configured"}
        ), 503

    return None


# ---------------------------------------------------------------------------
# Firebase authentication
# ---------------------------------------------------------------------------

def _get_bearer_token() -> str | None:
    authorization = request.headers.get(
        "Authorization",
        "",
    )

    if not authorization.startswith("Bearer "):
        return None

    return (
        authorization.removeprefix("Bearer ").strip()
        or None
    )


def _verify_firebase_user():
    if not FIREBASE_CONFIGURED or firebase_auth is None:
        return None, (
            jsonify(
                {
                    "error": (
                        "Firebase authentication is unavailable"
                    )
                }
            ),
            503,
        )

    token = _get_bearer_token()
    if not token:
        return None, (
            jsonify(
                {"error": "Firebase ID token is required"}
            ),
            401,
        )

    try:
        decoded = firebase_auth.verify_id_token(token)
        uid = str(decoded.get("uid") or "").strip()

        if not uid:
            raise ValueError(
                "Verified token did not contain a UID"
            )

        return decoded, None

    except Exception as error:
        app.logger.warning(
            "Firebase token verification failed: %s",
            error,
        )
        return None, (
            jsonify(
                {
                    "error": (
                        "Invalid or expired Firebase ID token"
                    )
                }
            ),
            401,
        )


def _authenticated_identity():
    decoded, error = _verify_firebase_user()
    if error:
        return None, error

    return {
        "uid": str(decoded["uid"]),
        "email": _normalize_email(decoded.get("email")),
        "name": str(decoded.get("name") or "").strip(),
        "claims": decoded,
    }, None


# ---------------------------------------------------------------------------
# Calendar helpers
# ---------------------------------------------------------------------------
CALENDAR_VISIBILITIES = frozenset({"personal", "family", "all"})
CALENDAR_ITEM_TYPES = frozenset({"event", "task", "reminder"})
CALENDAR_TASK_STATUSES = frozenset({"pending", "in_progress", "completed"})


def _normalize_family_name(value: Any) -> str:
    return str(value or "").strip()


def _normalize_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []

    values: list[str] = []
    seen: set[str] = set()

    for item in value:
        normalized = str(item or "").strip()
        if normalized and normalized not in seen:
            values.append(normalized)
            seen.add(normalized)

    return values


def _parse_calendar_datetime(
    value: Any,
    field_name: str,
    *,
    required: bool = True,
) -> tuple[datetime | None, str | None]:
    raw_value = str(value or "").strip()
    if not raw_value:
        if required:
            return None, f"{field_name} is required"
        return None, None

    try:
        parsed = datetime.fromisoformat(
            raw_value.replace("Z", "+00:00")
        )
    except ValueError:
        return None, f"{field_name} must be a valid ISO 8601 datetime"

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SINGAPORE_TZ)

    return parsed.astimezone(SINGAPORE_TZ), None


def _calendar_item_response(
    item_id: str,
    item: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": item_id,
        "itemType": item.get("itemType"),
        "title": str(item.get("title") or ""),
        "description": str(item.get("description") or ""),
        "startAt": item.get("startAt"),
        "endAt": item.get("endAt"),
        "dueAt": item.get("dueAt"),
        "allDay": bool(item.get("allDay", False)),
        "status": item.get("status"),
        "completedAt": item.get("completedAt"),
        "visibility": item.get("visibility"),
        "ownerId": item.get("ownerId"),
        "ownerDisplayName": item.get("ownerDisplayName"),
        "familyName": item.get("familyName"),
        "taggedUserIds": _normalize_string_list(item.get("taggedUserIds")),
        "taggedUsers": item.get("taggedUsers") or [],
        "createdAt": item.get("createdAt"),
        "updatedAt": item.get("updatedAt"),
    }


def _calendar_item_datetime(item: dict[str, Any]) -> str:
    item_type = str(item.get("itemType") or "").strip().lower()
    if item_type == "event":
        return str(item.get("startAt") or "")
    return str(item.get("dueAt") or "")


def _calendar_item_is_visible_to_user(
    item: dict[str, Any],
    uid: str,
    family_name: str,
) -> bool:
    visibility = str(item.get("visibility") or "").strip().lower()
    owner_id = str(item.get("ownerId") or "").strip()
    tagged_user_ids = _normalize_string_list(item.get("taggedUserIds"))

    if owner_id == uid or uid in tagged_user_ids:
        return True

    if visibility == "all":
        return True

    item_family = _normalize_family_name(item.get("familyName"))
    return bool(
        visibility == "family"
        and family_name
        and item_family == family_name
    )


def _load_calendar_items(
    *,
    uid: str,
    family_name: str,
    visibility_scope: str,
    item_type: str | None = None,
) -> list[dict[str, Any]]:
    snapshots = FIRESTORE_DB.collection("calendarItems").stream()
    items: list[dict[str, Any]] = []

    for snapshot in snapshots:
        item = snapshot.to_dict() or {}
        stored_type = str(item.get("itemType") or "").strip().lower()
        if item_type and stored_type != item_type:
            continue

        owner_id = str(item.get("ownerId") or "").strip()
        visibility = str(item.get("visibility") or "").strip().lower()
        item_family = _normalize_family_name(item.get("familyName"))
        tagged_user_ids = _normalize_string_list(item.get("taggedUserIds"))

        include = False
        if visibility_scope == "own":
            include = owner_id == uid
        elif visibility_scope == "tagged":
            include = uid in tagged_user_ids
        elif visibility_scope == "family":
            include = bool(
                family_name
                and visibility == "family"
                and item_family == family_name
            )
        elif visibility_scope == "all-users":
            include = visibility == "all"
        elif visibility_scope == "visible":
            include = _calendar_item_is_visible_to_user(
                item,
                uid,
                family_name,
            )

        if include:
            items.append(_calendar_item_response(snapshot.id, item))

    items.sort(
        key=lambda calendar_item: (
            _calendar_item_datetime(calendar_item),
            str(calendar_item.get("createdAt") or ""),
        )
    )
    return items


def _resolve_tagged_users(
    tagged_user_ids: list[str],
    owner_id: str,
) -> tuple[list[str], list[dict[str, Any]], list[str]]:
    valid_ids: list[str] = []
    users: list[dict[str, Any]] = []
    missing_ids: list[str] = []

    for tagged_uid in tagged_user_ids:
        if tagged_uid == owner_id:
            continue

        tagged_user = _read_document("users", tagged_uid)
        if not tagged_user:
            missing_ids.append(tagged_uid)
            continue

        valid_ids.append(tagged_uid)
        users.append(
            {
                "uid": tagged_uid,
                "displayName": str(
                    tagged_user.get("displayName") or "User"
                ).strip(),
                "email": _normalize_email(tagged_user.get("email")),
                "profilePicLink": _profile_picture_link(tagged_user),
            }
        )

    return valid_ids, users, missing_ids


# ---------------------------------------------------------------------------
# Telegram helpers
# ---------------------------------------------------------------------------

def _require_telegram_configuration():
    missing: list[str] = []

    if not TELEGRAM_BOT_TOKEN:
        missing.append("TELEGRAM_BOT_TOKEN")

    if not TELEGRAM_BOT_USERNAME:
        missing.append("TELEGRAM_BOT_USERNAME")

    if missing:
        return jsonify(
            {
                "error": "Telegram is not configured",
                "missing": missing,
            }
        ), 503

    return None


def _telegram_api_request(
    method_name: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not TELEGRAM_API_BASE_URL:
        raise RuntimeError("Telegram bot token is not configured")

    response = requests.post(
        f"{TELEGRAM_API_BASE_URL}/{method_name}",
        json=payload or {},
        timeout=TELEGRAM_REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()

    result = response.json()
    if not result.get("ok"):
        raise RuntimeError(
            str(result.get("description") or "Telegram API request failed")
        )

    return result


def _send_telegram_message(
    chat_id: str | int,
    text: str,
) -> dict[str, Any]:
    return _telegram_api_request(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
    )


def _telegram_link_token_hash(raw_token: str) -> str:
    return hashlib.sha256(
        raw_token.encode("utf-8")
    ).hexdigest()


def _parse_stored_datetime(value: Any) -> datetime | None:
    raw_value = str(value or "").strip()
    if not raw_value:
        return None

    try:
        parsed = datetime.fromisoformat(
            raw_value.replace("Z", "+00:00")
        )
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SINGAPORE_TZ)

    return parsed.astimezone(SINGAPORE_TZ)


def _calendar_item_occurs_at(
    item: dict[str, Any],
) -> datetime | None:
    item_type = str(
        item.get("itemType") or ""
    ).strip().lower()

    raw_datetime = (
        item.get("startAt")
        if item_type == "event"
        else item.get("dueAt")
    )

    return _parse_stored_datetime(raw_datetime)


def _get_user_week_ahead_items(
    uid: str,
) -> list[dict[str, Any]]:
    user = _read_document("users", uid) or {}
    family_name = _normalize_family_name(
        user.get("familyName")
    )

    visible_items = _load_calendar_items(
        uid=uid,
        family_name=family_name,
        visibility_scope="visible",
    )

    now = datetime.now(SINGAPORE_TZ)
    end_time = now + timedelta(
        days=TELEGRAM_REMINDER_DAYS_AHEAD
    )

    upcoming_items: list[
        tuple[datetime, dict[str, Any]]
    ] = []

    for item in visible_items:
        occurs_at = _calendar_item_occurs_at(item)

        if occurs_at is None:
            continue

        if not (now <= occurs_at < end_time):
            continue

        if (
            item.get("itemType") == "task"
            and item.get("status") == "completed"
        ):
            continue

        upcoming_items.append((occurs_at, item))

    upcoming_items.sort(
        key=lambda entry: (
            entry[0],
            str(entry[1].get("title") or "").lower(),
        )
    )

    return [item for _, item in upcoming_items]


def _format_calendar_item_for_telegram(
    item: dict[str, Any],
) -> str:
    occurs_at = _calendar_item_occurs_at(item)
    if occurs_at is None:
        return ""

    item_type = str(
        item.get("itemType") or "item"
    ).strip().lower()

    labels = {
        "event": "Event",
        "task": "Task",
        "reminder": "Reminder",
    }

    icons = {
        "event": "📅",
        "task": "✅",
        "reminder": "🔔",
    }

    title = escape(
        str(item.get("title") or "Untitled")
    )
    owner_name = escape(
        str(item.get("ownerDisplayName") or "Unknown user")
    )

    if bool(item.get("allDay", False)):
        date_text = occurs_at.strftime("%a, %d %b")
    else:
        date_text = occurs_at.strftime(
            "%a, %d %b at %I:%M %p"
        ).lstrip("0")

    lines = [
        f"{icons.get(item_type, '•')} <b>{title}</b>",
        f"{labels.get(item_type, 'Item')} · {date_text}",
    ]

    if str(item.get("ownerId") or ""):
        lines.append(f"Created by {owner_name}")

    return "\n".join(lines)


def _build_week_ahead_message(
    display_name: str,
    items: list[dict[str, Any]],
) -> str:
    safe_name = escape(display_name or "there")
    today_text = datetime.now(
        SINGAPORE_TZ
    ).strftime("%A, %d %B %Y")

    if not items:
        return (
            f"Good morning, <b>{safe_name}</b>! \n\n"
            f"Today is {today_text}.\n\n"
            "You have no upcoming events, tasks, or reminders "
            "within the next seven days."
        )

    lines = [
        f"Good morning, <b>{safe_name}</b>! ",
        "",
        (
            f"Here is your schedule for the next "
            f"{TELEGRAM_REMINDER_DAYS_AHEAD} days:"
        ),
        "",
    ]

    current_date = None

    for item in items:
        occurs_at = _calendar_item_occurs_at(item)
        if occurs_at is None:
            continue

        item_date = occurs_at.date()

        if item_date != current_date:
            if current_date is not None:
                lines.append("")

            lines.append(
                f"<b>{occurs_at.strftime('%A, %d %B')}</b>"
            )
            current_date = item_date

        formatted_item = _format_calendar_item_for_telegram(
            item
        )

        if formatted_item:
            lines.append(formatted_item)
            lines.append("")

    lines.extend(
        [
            "Open The Nowl In One to view or update your calendar.",
            "",
            "Use /unsubscribe to stop these messages.",
        ]
    )

    message = "\n".join(lines).strip()

    if len(message) > 4000:
        message = (
            message[:3900].rstrip()
            + "\n\nMore items are available in The Nowl In One."
        )

    return message


def _set_telegram_subscription_active(
    uid: str,
    *,
    chat_id: str,
    telegram_user: dict[str, Any],
) -> None:
    _write_document(
        "telegramSubscriptions",
        uid,
        {
            "uid": uid,
            "chatId": str(chat_id),
            "telegramUserId": str(
                telegram_user.get("id") or ""
            ),
            "telegramUsername": str(
                telegram_user.get("username") or ""
            ).strip(),
            "telegramFirstName": str(
                telegram_user.get("first_name") or ""
            ).strip(),
            "telegramLastName": str(
                telegram_user.get("last_name") or ""
            ).strip(),
            "active": True,
            "reminderHour": 8,
            "timezone": "Asia/Singapore",
            "linkedAt": _now_iso(),
            "updatedAt": _now_iso(),
            "lastSentAt": None,
            "lastDeliveryStatus": None,
        },
        merge=True,
    )


def _deactivate_telegram_subscription(
    uid: str,
    *,
    reason: str,
) -> None:
    _write_document(
        "telegramSubscriptions",
        uid,
        {
            "active": False,
            "deactivatedAt": _now_iso(),
            "deactivationReason": reason,
            "updatedAt": _now_iso(),
        },
        merge=True,
    )


def _find_subscription_by_chat_id(
    chat_id: str,
) -> tuple[str | None, dict[str, Any] | None]:
    snapshots = (
        FIRESTORE_DB
        .collection("telegramSubscriptions")
        .where("chatId", "==", str(chat_id))
        .limit(1)
        .stream()
    )

    for snapshot in snapshots:
        return snapshot.id, snapshot.to_dict() or {}

    return None, None


def _verify_cron_secret() -> bool:
    provided_secret = request.headers.get(
        "X-Cron-Secret",
        "",
    ).strip()

    return bool(
        TELEGRAM_CRON_SECRET
        and secrets.compare_digest(
            provided_secret,
            TELEGRAM_CRON_SECRET,
        )
    )


# ---------------------------------------------------------------------------
# User and Wordle persistence
# ---------------------------------------------------------------------------


def _default_user(
    uid: str,
    email: str,
    display_name: str | None = None,
) -> dict[str, Any]:
    return {
        "uid": uid,
        "email": _normalize_email(email),
        "displayName": (
            str(display_name or "").strip()
            or (
                _normalize_email(email).split("@")[0]
                if email
                else "Player"
            )
        ),
        "mustResetPassword": False,
        "profile_pic_link": "",
        "createdAt": _now_iso(),
    }


def _get_or_create_user(
    uid: str,
    email: str,
    display_name: str | None = None,
) -> dict[str, Any]:
    user = _read_document("users", uid)

    if not isinstance(user, dict):
        user = _default_user(
            uid,
            email,
            display_name,
        )
        _write_document("users", uid, user)
        return user

    updates: dict[str, Any] = {}

    normalized_email = _normalize_email(email)
    if normalized_email and user.get("email") != normalized_email:
        updates["email"] = normalized_email

    if (
        display_name
        and not str(user.get("displayName") or "").strip()
    ):
        updates["displayName"] = display_name

    if updates:
        _write_document(
            "users",
            uid,
            updates,
            merge=True,
        )
        user.update(updates)

    return user


def _default_wordle_stats(
    uid: str,
    email: str,
) -> dict[str, Any]:
    return {
        "userId": uid,
        "email": _normalize_email(email),
        "rankScore": 0,
        "combo": 0,
        "wins": 0,
        "gamesPlayed": 0,
        "bestCombo": 0,
        "lastPlayedDate": None,
        "lastWinDate": None,
        "daily": {},
        "createdAt": _now_iso(),
        "updatedAt": _now_iso(),
    }


def _get_wordle_stats(
    uid: str,
    email: str,
) -> dict[str, Any]:
    stats = _read_document("wordleUsers", uid)

    if not isinstance(stats, dict):
        stats = _default_wordle_stats(uid, email)
        _write_document("wordleUsers", uid, stats)
        return stats

    changed = False

    if stats.get("userId") != uid:
        stats["userId"] = uid
        changed = True

    normalized_email = _normalize_email(email)
    if normalized_email and stats.get("email") != normalized_email:
        stats["email"] = normalized_email
        changed = True

    last_win = stats.get("lastWinDate")
    if (
        last_win
        and last_win not in {
            _today_key(),
            _yesterday_key(),
        }
        and int(stats.get("combo", 0)) != 0
    ):
        stats["combo"] = 0
        changed = True

    if changed:
        stats["updatedAt"] = _now_iso()
        _write_document("wordleUsers", uid, stats)

    return stats


def _save_wordle_stats(
    uid: str,
    stats: dict[str, Any],
) -> None:
    stats["userId"] = uid
    stats["updatedAt"] = _now_iso()
    _write_document("wordleUsers", uid, stats)


def _public_stats(
    stats: dict[str, Any],
) -> dict[str, Any]:
    return {
        "userId": str(
            stats.get("userId") or ""
        ),
        "rankScore": int(
            stats.get("rankScore", 0)
        ),
        "combo": int(stats.get("combo", 0)),
        "wins": int(stats.get("wins", 0)),
        "gamesPlayed": int(
            stats.get("gamesPlayed", 0)
        ),
        "bestCombo": int(
            stats.get("bestCombo", 0)
        ),
        "lastPlayedDate": stats.get(
            "lastPlayedDate"
        ),
        "lastWinDate": stats.get(
            "lastWinDate"
        ),
    }


# ---------------------------------------------------------------------------
# Word validation and daily answers
# ---------------------------------------------------------------------------

def _dictionary_api_is_real_word(word: str) -> bool:
    normalized = word.strip().lower()

    if not re.fullmatch(r"[a-z]{5}", normalized):
        return False

    cached = _read_document(
        "wordleDictionaryCache",
        normalized,
    )

    if (
        isinstance(cached, dict)
        and isinstance(cached.get("valid"), bool)
    ):
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


def _claim_daily_answer(
    date_key: str,
    candidate: str,
) -> str | None:
    if FIRESTORE_DB is None or firestore is None:
        raise RuntimeError("Firestore is not configured")

    normalized_candidate = candidate.strip().lower()

    if normalized_candidate not in ANSWER_WORD_SET:
        raise ValueError(
            "Candidate is not in the local answer list"
        )

    answer = normalized_candidate.upper()
    daily_ref = _document("wordleDaily", date_key)
    used_ref = _document(
        "wordleUsedAnswers",
        normalized_candidate,
    )
    transaction = FIRESTORE_DB.transaction()

    @firestore.transactional
    def run_transaction(transaction):
        daily_snapshot = daily_ref.get(
            transaction=transaction
        )

        if daily_snapshot.exists:
            current = daily_snapshot.to_dict() or {}
            stored_answer = str(
                current.get("answer") or ""
            ).upper()
            return stored_answer or None

        used_snapshot = used_ref.get(
            transaction=transaction
        )
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
    for _ in range(
        LOCAL_WORD_SELECTION_ATTEMPTS
    ):
        candidate = random.choice(ANSWER_WORDS)
        answer = _claim_daily_answer(
            date_key,
            candidate,
        )

        if answer:
            return answer

    used_snapshots = (
        FIRESTORE_DB
        .collection("wordleUsedAnswers")
        .stream()
    )

    used_words = {
        snapshot.id.strip().lower()
        for snapshot in used_snapshots
    }

    remaining_words = list(
        ANSWER_WORD_SET - used_words
    )

    if not remaining_words:
        raise RuntimeError(
            "Every word in word_list.py has already "
            "been used as an answer"
        )

    random.shuffle(remaining_words)

    for candidate in remaining_words:
        answer = _claim_daily_answer(
            date_key,
            candidate,
        )
        if answer:
            return answer

    stored = _read_document(
        "wordleDaily",
        date_key,
    )

    if stored and stored.get("answer"):
        return str(stored["answer"]).upper()

    raise RuntimeError(
        "Could not claim a unique daily Wordle answer"
    )


def _daily_answer(date_key: str) -> str:
    stored = _read_document(
        "wordleDaily",
        date_key,
    )

    if stored and stored.get("answer"):
        return str(stored["answer"]).upper()

    return _create_daily_answer(date_key)


def _letter_statuses(
    guess: str,
    answer: str,
) -> list[str]:
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
            remaining[
                remaining.index(guess[index])
            ] = ""

    return statuses


# ---------------------------------------------------------------------------
# Leaderboard helpers
# ---------------------------------------------------------------------------

def _build_leaderboard() -> list[dict[str, Any]]:
    snapshots = (
        FIRESTORE_DB
        .collection("wordleUsers")
        .stream()
    )

    players: list[dict[str, Any]] = []

    for snapshot in snapshots:
        stats = snapshot.to_dict() or {}
        uid = str(
            stats.get("userId")
            or snapshot.id
        )

        user = _read_document("users", uid) or {}

        email = _normalize_email(
            user.get("email")
            or stats.get("email")
        )

        display_name = str(
            user.get("displayName") or ""
        ).strip()

        if not display_name:
            display_name = (
                email.split("@")[0]
                if email
                else "Unknown player"
            )

        games_played = int(
            stats.get("gamesPlayed", 0)
        )
        wins = int(stats.get("wins", 0))

        win_rate = (
            round((wins / games_played) * 100)
            if games_played > 0
            else 0
        )

        players.append(
            {
                "id": uid,
                "userId": uid,
                "displayName": display_name,
                "profilePicLink": _profile_picture_link(user),
                "rankScore": int(
                    stats.get("rankScore", 0)
                ),
                "combo": int(
                    stats.get("combo", 0)
                ),
                "bestCombo": int(
                    stats.get("bestCombo", 0)
                ),
                "wins": wins,
                "gamesPlayed": games_played,
                "winRate": win_rate,
            }
        )

    players.sort(
        key=lambda player: (
            -player["rankScore"],
            -player["wins"],
            -player["bestCombo"],
            player["displayName"].lower(),
        )
    )

    previous_score = None
    previous_rank = 0

    for index, player in enumerate(players):
        score = player["rankScore"]

        if score != previous_score:
            previous_rank = index + 1
            previous_score = score

        player["rank"] = previous_rank

    return players


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
def health_check():
    return jsonify(
        {
            "status": "ok",
            "firebase_configured": FIREBASE_CONFIGURED,
            "storage": (
                "firestore"
                if FIREBASE_CONFIGURED
                else "unavailable"
            ),
            "dictionary_api": "dictionaryapi.dev",
            "local_answer_word_count": len(
                ANSWER_WORDS
            ),
            "admin_registration_configured": bool(
                ADMIN_REGISTRATION_KEY
            ),
            "telegram_configured": bool(
                TELEGRAM_BOT_TOKEN
                and TELEGRAM_BOT_USERNAME
                and TELEGRAM_WEBHOOK_SECRET
                and TELEGRAM_CRON_SECRET
            ),
        }
    )


@app.post("/api/admin/users")
def admin_create_user():
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error

    if not ADMIN_REGISTRATION_KEY:
        return jsonify(
            {
                "error": (
                    "ADMIN_REGISTRATION_KEY is not configured"
                )
            }
        ), 503

    provided_key = request.headers.get(
        "X-Admin-Key",
        "",
    ).strip()

    if provided_key != ADMIN_REGISTRATION_KEY:
        return jsonify(
            {"error": "Invalid admin key"}
        ), 403

    if firebase_auth is None:
        return jsonify(
            {
                "error": (
                    "Firebase Admin authentication is unavailable"
                )
            }
        ), 503

    payload = request.get_json(silent=True) or {}
    email = _normalize_email(payload.get("email"))
    temporary_password = str(
        payload.get("temporaryPassword") or ""
    )
    display_name = (
        str(payload.get("displayName") or "").strip()
        or None
    )

    if not re.fullmatch(
        r"[^@\s]+@[^@\s]+\.[^@\s]+",
        email,
    ):
        return jsonify(
            {"error": "Enter a valid email address"}
        ), 400

    if len(temporary_password) < 8:
        return jsonify(
            {
                "error": (
                    "Temporary password must be at least "
                    "8 characters"
                )
            }
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

        user_document = _default_user(
            created_user.uid,
            email,
            display_name,
        )
        user_document["mustResetPassword"] = True

        _write_document(
            "users",
            created_user.uid,
            user_document,
        )

        _write_document(
            "wordleUsers",
            created_user.uid,
            _default_wordle_stats(
                created_user.uid,
                email,
            ),
        )

    except firebase_auth.EmailAlreadyExistsError:
        return jsonify(
            {
                "error": (
                    "A Firebase user already exists "
                    "with this email"
                )
            }
        ), 409

    except Exception as error:
        app.logger.exception(
            "Manual user creation failed: %s",
            error,
        )

        if created_user is not None:
            try:
                firebase_auth.delete_user(
                    created_user.uid
                )
            except Exception:
                app.logger.exception(
                    "Could not roll back Firebase "
                    "Authentication user creation"
                )

        return jsonify(
            {"error": "Could not create user"}
        ), 500

    return jsonify(
        {
            "message": (
                "User created with a temporary password"
            ),
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
    identity, error = _authenticated_identity()
    if error:
        return error

    user = _get_or_create_user(
        identity["uid"],
        identity["email"],
        identity["name"],
    )

    return jsonify(
        {
            "uid": identity["uid"],
            "email": identity["email"],
            "displayName": user.get("displayName"),
            "profilePicLink": _profile_picture_link(user),
            "mustResetPassword": bool(
                identity["claims"].get(
                    "mustResetPassword",
                    False,
                )
            ),
        }
    )


@app.post("/api/auth/complete-password-reset")
def complete_password_reset():
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error

    identity, error = _authenticated_identity()
    if error:
        return error

    uid = identity["uid"]

    try:
        firebase_auth.set_custom_user_claims(
            uid,
            {"mustResetPassword": False},
        )

        _get_or_create_user(
            uid,
            identity["email"],
            identity["name"],
        )

        _write_document(
            "users",
            uid,
            {
                "mustResetPassword": False,
                "passwordResetAt": _now_iso(),
            },
            merge=True,
        )

    except Exception as error:
        app.logger.exception(
            "Could not complete password reset: %s",
            error,
        )
        return jsonify(
            {
                "error": (
                    "Could not complete password reset"
                )
            }
        ), 500

    return jsonify(
        {
            "message": (
                "Password reset requirement cleared"
            )
        }
    )


# ---------------------------------------------------------------------------
# User lookup for calendar tagging
# ---------------------------------------------------------------------------

@app.get("/api/users/search")
def search_users_for_tagging():
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error

    identity, error = _authenticated_identity()
    if error:
        return error

    query = str(request.args.get("q") or "").strip().lower()
    limit = request.args.get("limit", default=20, type=int)
    limit = max(1, min(limit, 50))

    if len(query) < 2:
        return jsonify(
            {"error": "q must contain at least 2 characters"}
        ), 400

    matches: list[dict[str, Any]] = []
    for snapshot in FIRESTORE_DB.collection("users").stream():
        if snapshot.id == identity["uid"]:
            continue

        user = snapshot.to_dict() or {}
        display_name = str(user.get("displayName") or "").strip()
        email = _normalize_email(user.get("email"))
        searchable = f"{display_name} {email}".lower()

        if query in searchable:
            matches.append(
                {
                    "uid": snapshot.id,
                    "displayName": display_name or "User",
                    "email": email,
                    "profilePicLink": _profile_picture_link(user),
                }
            )

        if len(matches) >= limit:
            break

    return jsonify({"users": matches, "count": len(matches)})


# ---------------------------------------------------------------------------
# Calendar routes
# ---------------------------------------------------------------------------

@app.post("/api/calendar/items")
def create_calendar_item():
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error

    identity, error = _authenticated_identity()
    if error:
        return error

    user = _get_or_create_user(
        identity["uid"],
        identity["email"],
        identity["name"],
    )
    payload = request.get_json(silent=True) or {}

    item_type = str(payload.get("itemType") or "").strip().lower()
    title = str(payload.get("title") or "").strip()
    description = str(payload.get("description") or "").strip()
    visibility = str(payload.get("visibility") or "personal").strip().lower()
    all_day = bool(payload.get("allDay", False))

    if item_type not in CALENDAR_ITEM_TYPES:
        return jsonify(
            {"error": "itemType must be event, task, or reminder"}
        ), 400

    if not title:
        return jsonify({"error": "Title is required"}), 400

    if len(title) > 200:
        return jsonify(
            {"error": "Title cannot exceed 200 characters"}
        ), 400

    if len(description) > 2000:
        return jsonify(
            {"error": "Description cannot exceed 2000 characters"}
        ), 400

    if visibility not in CALENDAR_VISIBILITIES:
        return jsonify(
            {"error": "visibility must be personal, family, or all"}
        ), 400

    family_name = _normalize_family_name(user.get("familyName"))
    if visibility == "family" and not family_name:
        return jsonify(
            {"error": "Your user document does not have a familyName"}
        ), 400

    requested_tagged_ids = _normalize_string_list(
        payload.get("taggedUserIds")
    )
    if len(requested_tagged_ids) > 50:
        return jsonify(
            {"error": "You can tag at most 50 users on one item"}
        ), 400

    tagged_user_ids, tagged_users, missing_ids = _resolve_tagged_users(
        requested_tagged_ids,
        identity["uid"],
    )
    if missing_ids:
        return jsonify(
            {
                "error": "One or more tagged users do not exist",
                "missingUserIds": missing_ids,
            }
        ), 400

    start_at = None
    end_at = None
    due_at = None
    now_datetime = datetime.now(SINGAPORE_TZ)
    today = now_datetime.date()

    if item_type == "event":
        start_at, datetime_error = _parse_calendar_datetime(
            payload.get("startAt"),
            "startAt",
        )
        if datetime_error:
            return jsonify({"error": datetime_error}), 400

        end_at, datetime_error = _parse_calendar_datetime(
            payload.get("endAt"),
            "endAt",
        )
        if datetime_error:
            return jsonify({"error": datetime_error}), 400

        if end_at < start_at:
            return jsonify(
                {"error": "endAt must be the same as or later than startAt"}
            ), 400

        if all_day:
            if start_at.date() < today:
                return jsonify(
                    {"error": "Calendar items cannot be created in the past"}
                ), 400
        elif start_at < now_datetime:
            return jsonify(
                {"error": "Calendar items cannot be created in the past"}
            ), 400
    else:
        due_at, datetime_error = _parse_calendar_datetime(
            payload.get("dueAt"),
            "dueAt",
        )
        if datetime_error:
            return jsonify({"error": datetime_error}), 400

        if all_day:
            if due_at.date() < today:
                return jsonify(
                    {"error": "Calendar items cannot be created in the past"}
                ), 400
        elif due_at < now_datetime:
            return jsonify(
                {"error": "Calendar items cannot be created in the past"}
            ), 400

    status = "pending" if item_type == "task" else None
    now = _now_iso()
    item_ref = FIRESTORE_DB.collection("calendarItems").document()

    item = {
        "itemType": item_type,
        "title": title,
        "description": description,
        "startAt": start_at.isoformat() if start_at else None,
        "endAt": end_at.isoformat() if end_at else None,
        "dueAt": due_at.isoformat() if due_at else None,
        "allDay": all_day,
        "status": status,
        "completedAt": None,
        "visibility": visibility,
        "ownerId": identity["uid"],
        "ownerEmail": identity["email"],
        "ownerDisplayName": (
            str(user.get("displayName") or "").strip()
            or identity["name"]
            or (
                identity["email"].split("@")[0]
                if identity["email"]
                else "User"
            )
        ),
        "familyName": family_name if visibility == "family" else None,
        "taggedUserIds": tagged_user_ids,
        "taggedUsers": tagged_users,
        "createdAt": now,
        "updatedAt": now,
    }

    try:
        item_ref.set(item)
    except Exception as database_error:
        app.logger.exception(
            "Could not create calendar item: %s",
            database_error,
        )
        return jsonify({"error": "Could not create calendar item"}), 500

    return jsonify(
        {
            "message": f"Calendar {item_type} created",
            "item": _calendar_item_response(item_ref.id, item),
        }
    ), 201


@app.delete("/api/calendar/items/<item_id>")
def delete_calendar_item(item_id: str):
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error

    identity, error = _authenticated_identity()
    if error:
        return error

    normalized_item_id = str(item_id or "").strip()
    if not normalized_item_id:
        return jsonify(
            {"error": "Calendar item ID is required"}
        ), 400

    item_ref = _document(
        "calendarItems",
        normalized_item_id,
    )

    try:
        snapshot = item_ref.get()
    except Exception as database_error:
        app.logger.exception(
            "Could not retrieve calendar item for deletion: %s",
            database_error,
        )
        return jsonify(
            {"error": "Could not retrieve calendar item"}
        ), 500

    if not snapshot.exists:
        return jsonify(
            {"error": "Calendar item not found"}
        ), 404

    item = snapshot.to_dict() or {}
    owner_id = str(item.get("ownerId") or "").strip()

    if owner_id != identity["uid"]:
        return jsonify(
            {
                "error": (
                    "Only the user who created this item can delete it"
                )
            }
        ), 403

    try:
        item_ref.delete()
    except Exception as database_error:
        app.logger.exception(
            "Could not delete calendar item: %s",
            database_error,
        )
        return jsonify(
            {"error": "Could not delete calendar item"}
        ), 500

    return jsonify(
        {
            "message": "Calendar item deleted successfully",
            "deletedItem": {
                "id": normalized_item_id,
                "itemType": item.get("itemType"),
                "title": str(item.get("title") or ""),
            },
        }
    ), 200


@app.get("/api/calendar/items")
def get_visible_calendar_items():
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error

    identity, error = _authenticated_identity()
    if error:
        return error

    user = _get_or_create_user(
        identity["uid"],
        identity["email"],
        identity["name"],
    )
    family_name = _normalize_family_name(user.get("familyName"))
    item_type = str(request.args.get("type") or "").strip().lower() or None

    if item_type and item_type not in CALENDAR_ITEM_TYPES:
        return jsonify(
            {"error": "type must be event, task, or reminder"}
        ), 400

    try:
        items = _load_calendar_items(
            uid=identity["uid"],
            family_name=family_name,
            visibility_scope="visible",
            item_type=item_type,
        )
    except Exception as database_error:
        app.logger.exception(
            "Could not load calendar items: %s",
            database_error,
        )
        return jsonify({"error": "Could not load calendar items"}), 500

    return jsonify(
        {
            "items": items,
            "count": len(items),
            "familyName": family_name or None,
        }
    )


@app.get("/api/calendar/items/own")
def get_own_calendar_items():
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error

    identity, error = _authenticated_identity()
    if error:
        return error

    item_type = str(request.args.get("type") or "").strip().lower() or None
    if item_type and item_type not in CALENDAR_ITEM_TYPES:
        return jsonify({"error": "Invalid calendar item type"}), 400

    items = _load_calendar_items(
        uid=identity["uid"],
        family_name="",
        visibility_scope="own",
        item_type=item_type,
    )
    return jsonify({"items": items, "count": len(items)})


@app.get("/api/calendar/items/tagged")
def get_tagged_calendar_items():
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error

    identity, error = _authenticated_identity()
    if error:
        return error

    items = _load_calendar_items(
        uid=identity["uid"],
        family_name="",
        visibility_scope="tagged",
    )
    return jsonify({"items": items, "count": len(items)})


@app.get("/api/calendar/items/upcoming")
def get_upcoming_calendar_items():
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error

    identity, error = _authenticated_identity()
    if error:
        return error

    user = _get_or_create_user(
        identity["uid"],
        identity["email"],
        identity["name"],
    )
    family_name = _normalize_family_name(user.get("familyName"))
    limit = request.args.get("limit", default=5, type=int)
    limit = max(1, min(limit, 50))
    item_type = str(request.args.get("type") or "").strip().lower() or None

    if item_type and item_type not in CALENDAR_ITEM_TYPES:
        return jsonify(
            {"error": "type must be event, task, or reminder"}
        ), 400

    now = datetime.now(SINGAPORE_TZ)
    visible_items = _load_calendar_items(
        uid=identity["uid"],
        family_name=family_name,
        visibility_scope="visible",
        item_type=item_type,
    )

    upcoming: list[tuple[datetime, dict[str, Any]]] = []
    for item in visible_items:
        raw_datetime = _calendar_item_datetime(item)
        if not raw_datetime:
            continue

        parsed_datetime, parse_error = _parse_calendar_datetime(
            raw_datetime,
            "calendar datetime",
        )
        if parse_error or parsed_datetime is None:
            continue

        if parsed_datetime >= now:
            upcoming.append((parsed_datetime, item))

    upcoming.sort(key=lambda entry: entry[0])
    selected_items = [entry[1] for entry in upcoming[:limit]]

    return jsonify(
        {
            "items": selected_items,
            "count": len(selected_items),
            "limit": limit,
        }
    )


@app.patch("/api/calendar/tasks/<item_id>/status")
def update_calendar_task_status(item_id: str):
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error

    identity, error = _authenticated_identity()
    if error:
        return error

    item_ref = _document("calendarItems", item_id)
    snapshot = item_ref.get()
    if not snapshot.exists:
        return jsonify({"error": "Task not found"}), 404

    item = snapshot.to_dict() or {}
    if item.get("itemType") != "task":
        return jsonify({"error": "Calendar item is not a task"}), 400

    if str(item.get("ownerId") or "") != identity["uid"]:
        return jsonify({"error": "Only the task owner can change its status"}), 403

    payload = request.get_json(silent=True) or {}
    status = str(payload.get("status") or "").strip().lower()
    if status not in CALENDAR_TASK_STATUSES:
        return jsonify(
            {"error": "status must be pending, in_progress, or completed"}
        ), 400

    updates = {
        "status": status,
        "completedAt": _now_iso() if status == "completed" else None,
        "updatedAt": _now_iso(),
    }
    item_ref.set(updates, merge=True)
    item.update(updates)

    return jsonify(
        {
            "message": "Task status updated",
            "item": _calendar_item_response(item_id, item),
        }
    )


# ---------------------------------------------------------------------------
# Telegram routes
# ---------------------------------------------------------------------------

@app.post("/api/telegram/link")
def create_telegram_link():
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error

    telegram_error = _require_telegram_configuration()
    if telegram_error:
        return telegram_error

    identity, error = _authenticated_identity()
    if error:
        return error

    user = _get_or_create_user(
        identity["uid"],
        identity["email"],
        identity["name"],
    )

    raw_token = secrets.token_urlsafe(32)
    token_hash = _telegram_link_token_hash(raw_token)

    now = datetime.now(SINGAPORE_TZ)
    expires_at = now + timedelta(
        minutes=TELEGRAM_LINK_TOKEN_LIFETIME_MINUTES
    )

    _write_document(
        "telegramLinkTokens",
        token_hash,
        {
            "uid": identity["uid"],
            "email": identity["email"],
            "displayName": str(
                user.get("displayName") or ""
            ),
            "createdAt": now.isoformat(),
            "expiresAt": expires_at.isoformat(),
            "usedAt": None,
        },
    )

    telegram_url = (
        f"https://t.me/{TELEGRAM_BOT_USERNAME}"
        f"?start={raw_token}"
    )

    return jsonify(
        {
            "telegramUrl": telegram_url,
            "expiresAt": expires_at.isoformat(),
            "expiresInMinutes": (
                TELEGRAM_LINK_TOKEN_LIFETIME_MINUTES
            ),
        }
    )


@app.post("/api/telegram/webhook")
def telegram_webhook():
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error

    if not TELEGRAM_WEBHOOK_SECRET:
        return jsonify(
            {
                "error": (
                    "TELEGRAM_WEBHOOK_SECRET is not configured"
                )
            }
        ), 503

    received_secret = request.headers.get(
        "X-Telegram-Bot-Api-Secret-Token",
        "",
    ).strip()

    if not secrets.compare_digest(
        received_secret,
        TELEGRAM_WEBHOOK_SECRET,
    ):
        return jsonify(
            {"error": "Invalid webhook secret"}
        ), 403

    update = request.get_json(silent=True) or {}
    message = update.get("message") or {}
    chat = message.get("chat") or {}
    telegram_user = message.get("from") or {}
    text_value = str(message.get("text") or "").strip()

    chat_id = chat.get("id")
    chat_type = str(chat.get("type") or "")

    if not chat_id or not text_value:
        return jsonify({"ok": True})

    if chat_type != "private":
        return jsonify({"ok": True})

    command_parts = text_value.split(maxsplit=1)
    command = command_parts[0].split("@", 1)[0].lower()
    command_argument = (
        command_parts[1].strip()
        if len(command_parts) > 1
        else ""
    )

    try:
        if command == "/start":
            first_name = escape(
                str(telegram_user.get("first_name") or "there")
            )

            # Always send an immediate greeting so the user can confirm
            # that the Telegram bot and webhook are working.
            _send_telegram_message(
                chat_id,
                (
                    f"Hello, <b>{first_name}</b>! \n\n"
                    "Kotaro here, I'll be here to remind you of upcoming events from now on."
                ),
            )

            if not command_argument:
                _send_telegram_message(
                    chat_id,
                    (
                        "To connect your account, open The Nowl In One "
                        "website and press <b>Connect Telegram</b>.\n\n"
                        "Once connected, I can send your upcoming "
                        "events, tasks, and reminders."
                    ),
                )
                return jsonify({"ok": True})

            token_hash = _telegram_link_token_hash(
                command_argument
            )
            token_ref = _document(
                "telegramLinkTokens",
                token_hash,
            )
            token_snapshot = token_ref.get()

            if not token_snapshot.exists:
                _send_telegram_message(
                    chat_id,
                    (
                        "This connection link is invalid or "
                        "has already been used. Please generate "
                        "a new link from The Nowl."
                    ),
                )
                return jsonify({"ok": True})

            token_data = token_snapshot.to_dict() or {}
            expires_at = _parse_stored_datetime(
                token_data.get("expiresAt")
            )

            if (
                token_data.get("usedAt")
                or expires_at is None
                or expires_at < datetime.now(SINGAPORE_TZ)
            ):
                token_ref.delete()
                _send_telegram_message(
                    chat_id,
                    (
                        "This connection link has expired. "
                        "Please generate a new one from The Nowl."
                    ),
                )
                return jsonify({"ok": True})

            uid = str(token_data.get("uid") or "").strip()
            if not uid:
                token_ref.delete()
                _send_telegram_message(
                    chat_id,
                    (
                        "This connection link could not be processed. "
                        "Please generate a new one from The Nowl."
                    ),
                )
                return jsonify({"ok": True})

            existing_uid, existing_subscription = (
                _find_subscription_by_chat_id(
                    str(chat_id)
                )
            )

            if (
                existing_uid
                and existing_uid != uid
                and existing_subscription
            ):
                _deactivate_telegram_subscription(
                    existing_uid,
                    reason="telegram_chat_relinked",
                )

            _set_telegram_subscription_active(
                uid,
                chat_id=str(chat_id),
                telegram_user=telegram_user,
            )
            token_ref.delete()

            display_name = escape(
                str(
                    token_data.get("displayName")
                    or telegram_user.get("first_name")
                    or "there"
                )
            )

            _send_telegram_message(
                chat_id,
                (
                    f"Connected successfully, "
                    f"<b>{display_name}</b>! ✅\n\n"
                    "You will receive a daily summary of your "
                    "events, tasks, and reminders for the next "
                    "seven days.\n\n"
                    "Commands:\n"
                    "/upcoming — View your week ahead\n"
                    "/status — Check your subscription\n"
                    "/unsubscribe — Stop daily reminders"
                ),
            )

        elif command == "/upcoming":
            uid, subscription = (
                _find_subscription_by_chat_id(
                    str(chat_id)
                )
            )

            if (
                not uid
                or not subscription
                or not subscription.get("active")
            ):
                _send_telegram_message(
                    chat_id,
                    (
                        "Your Telegram account is not currently "
                        "connected to The Nowl."
                    ),
                )
                return jsonify({"ok": True})

            user = _read_document("users", uid) or {}
            items = _get_user_week_ahead_items(uid)

            _send_telegram_message(
                chat_id,
                _build_week_ahead_message(
                    str(
                        user.get("displayName")
                        or telegram_user.get("first_name")
                        or "there"
                    ),
                    items,
                ),
            )

        elif command == "/status":
            uid, subscription = (
                _find_subscription_by_chat_id(
                    str(chat_id)
                )
            )

            if (
                uid
                and subscription
                and subscription.get("active")
            ):
                _send_telegram_message(
                    chat_id,
                    (
                        "Your daily Nowl reminders are "
                        "<b>active</b>. ✅\n\n"
                        "They are currently scheduled for "
                        "8:00 AM Singapore time."
                    ),
                )
            else:
                _send_telegram_message(
                    chat_id,
                    (
                        "Your Nowl reminder subscription is "
                        "<b>not active</b>."
                    ),
                )

        elif command in {"/unsubscribe", "/stop"}:
            uid, subscription = (
                _find_subscription_by_chat_id(
                    str(chat_id)
                )
            )

            if uid and subscription:
                _deactivate_telegram_subscription(
                    uid,
                    reason="telegram_command",
                )

            _send_telegram_message(
                chat_id,
                (
                    "Daily reminders have been disabled. "
                    "You can reconnect from The Nowl In One at any time."
                ),
            )

        elif command == "/help":
            _send_telegram_message(
                chat_id,
                (
                    "<b>The Kotaro Reminders Bot</b>\n\n"
                    "/upcoming — View the next seven days\n"
                    "/status — Check reminder status\n"
                    "/unsubscribe — Stop reminders\n"
                    "/help — Show this message"
                ),
            )

    except requests.RequestException as telegram_error:
        app.logger.exception(
            "Telegram request failed: %s",
            telegram_error,
        )
    except Exception as webhook_error:
        app.logger.exception(
            "Telegram webhook processing failed: %s",
            webhook_error,
        )

    return jsonify({"ok": True})


@app.get("/api/telegram/subscription")
def get_telegram_subscription():
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error

    identity, error = _authenticated_identity()
    if error:
        return error

    subscription = _read_document(
        "telegramSubscriptions",
        identity["uid"],
    )

    if not subscription:
        return jsonify(
            {
                "connected": False,
                "active": False,
            }
        )

    return jsonify(
        {
            "connected": bool(subscription.get("chatId")),
            "active": bool(subscription.get("active")),
            "telegramUsername": (
                subscription.get("telegramUsername")
                or None
            ),
            "telegramFirstName": (
                subscription.get("telegramFirstName")
                or None
            ),
            "linkedAt": subscription.get("linkedAt"),
            "lastSentAt": subscription.get("lastSentAt"),
            "lastDeliveryStatus": subscription.get(
                "lastDeliveryStatus"
            ),
            "reminderHour": subscription.get(
                "reminderHour",
                8,
            ),
            "timezone": subscription.get(
                "timezone",
                "Asia/Singapore",
            ),
        }
    )


@app.delete("/api/telegram/subscription")
def delete_telegram_subscription():
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error

    identity, error = _authenticated_identity()
    if error:
        return error

    subscription = _read_document(
        "telegramSubscriptions",
        identity["uid"],
    )

    if not subscription:
        return jsonify(
            {
                "message": (
                    "Telegram subscription was already inactive"
                )
            }
        )

    _deactivate_telegram_subscription(
        identity["uid"],
        reason="nowl_frontend",
    )

    chat_id = subscription.get("chatId")
    if chat_id:
        try:
            _send_telegram_message(
                chat_id,
                (
                    "Your daily Nowl reminders have been "
                    "disabled from the website."
                ),
            )
        except Exception as telegram_error:
            app.logger.warning(
                "Could not send Telegram unsubscribe message: %s",
                telegram_error,
            )

    return jsonify(
        {
            "message": (
                "Telegram reminders disabled successfully"
            )
        }
    )


@app.post("/api/telegram/test")
def send_telegram_test_message():
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error

    telegram_error = _require_telegram_configuration()
    if telegram_error:
        return telegram_error

    identity, error = _authenticated_identity()
    if error:
        return error

    subscription = _read_document(
        "telegramSubscriptions",
        identity["uid"],
    )

    if (
        not subscription
        or not subscription.get("active")
        or not subscription.get("chatId")
    ):
        return jsonify(
            {
                "error": (
                    "Connect Telegram before sending a test message"
                )
            }
        ), 409

    try:
        _send_telegram_message(
            subscription["chatId"],
            (
                "<b>The Nowl In One test reminder</b> 🦉\n\n"
                "Your Telegram subscription is working correctly."
            ),
        )
    except requests.RequestException as telegram_error:
        app.logger.exception(
            "Could not send Telegram test message: %s",
            telegram_error,
        )
        return jsonify(
            {"error": "Could not send Telegram message"}
        ), 502

    return jsonify(
        {"message": "Test reminder sent"}
    )


@app.post("/api/internal/telegram/send-daily-reminders")
def send_daily_telegram_reminders():
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error

    telegram_error = _require_telegram_configuration()
    if telegram_error:
        return telegram_error

    if not _verify_cron_secret():
        return jsonify(
            {"error": "Invalid cron secret"}
        ), 403

    today_key = datetime.now(
        SINGAPORE_TZ
    ).date().isoformat()

    sent_count = 0
    failed_count = 0
    skipped_count = 0
    failures: list[dict[str, str]] = []

    snapshots = (
        FIRESTORE_DB
        .collection("telegramSubscriptions")
        .where("active", "==", True)
        .stream()
    )

    for snapshot in snapshots:
        uid = snapshot.id
        subscription = snapshot.to_dict() or {}
        chat_id = str(
            subscription.get("chatId") or ""
        ).strip()

        if not chat_id:
            skipped_count += 1
            continue

        if subscription.get("lastSentDate") == today_key:
            skipped_count += 1
            continue

        try:
            user = _read_document("users", uid) or {}
            items = _get_user_week_ahead_items(uid)

            _send_telegram_message(
                chat_id,
                _build_week_ahead_message(
                    str(
                        user.get("displayName")
                        or subscription.get(
                            "telegramFirstName"
                        )
                        or "there"
                    ),
                    items,
                ),
            )

            _write_document(
                "telegramSubscriptions",
                uid,
                {
                    "lastSentAt": _now_iso(),
                    "lastSentDate": today_key,
                    "lastDeliveryStatus": "sent",
                    "lastDeliveryItemCount": len(items),
                    "updatedAt": _now_iso(),
                },
                merge=True,
            )
            sent_count += 1

        except Exception as delivery_error:
            failed_count += 1
            error_message = str(delivery_error)[:300]
            failures.append(
                {
                    "uid": uid,
                    "error": error_message,
                }
            )

            _write_document(
                "telegramSubscriptions",
                uid,
                {
                    "lastDeliveryStatus": "failed",
                    "lastDeliveryError": error_message,
                    "lastDeliveryAttemptAt": _now_iso(),
                    "updatedAt": _now_iso(),
                },
                merge=True,
            )

            app.logger.exception(
                "Telegram reminder failed for user %s: %s",
                uid,
                delivery_error,
            )

    return jsonify(
        {
            "message": "Daily Telegram reminder run completed",
            "date": today_key,
            "sent": sent_count,
            "failed": failed_count,
            "skipped": skipped_count,
            "failures": failures,
        }
    )


@app.get("/api/games/wordle/me")
def get_current_wordle_player():
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error

    identity, error = _authenticated_identity()
    if error:
        return error

    uid = identity["uid"]
    email = identity["email"]

    user = _get_or_create_user(
        uid,
        email,
        identity["name"],
    )
    stats = _get_wordle_stats(uid, email)

    leaderboard = _build_leaderboard()
    leaderboard_entry = next(
        (
            player
            for player in leaderboard
            if player["userId"] == uid
        ),
        None,
    )

    player = {
        **_public_stats(stats),
        "id": uid,
        "userId": uid,
        "email": email,
        "displayName": user.get("displayName")
        or (
            email.split("@")[0]
            if email
            else "Player"
        ),
        "profilePicLink": _profile_picture_link(user),
        "rank": (
            leaderboard_entry.get("rank")
            if leaderboard_entry
            else None
        ),
        "winRate": (
            leaderboard_entry.get("winRate", 0)
            if leaderboard_entry
            else 0
        ),
    }

    return jsonify({"player": player})


@app.get("/api/games/wordle")
def get_wordle_game():
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error

    identity, error = _authenticated_identity()
    if error:
        return error

    uid = identity["uid"]
    email = identity["email"]

    _get_or_create_user(
        uid,
        email,
        identity["name"],
    )

    date_key = _today_key()

    try:
        answer = _daily_answer(date_key)
    except (
        requests.RequestException,
        ValueError,
        RuntimeError,
    ) as error:
        app.logger.exception(
            "Daily Wordle generation failed: %s",
            error,
        )
        return jsonify(
            {
                "error": (
                    "Could not prepare today's Wordle. "
                    "Please try again."
                ),
                "code": "daily_word_unavailable",
            }
        ), 503

    stats = _get_wordle_stats(uid, email)
    daily = stats.setdefault("daily", {})
    today_game = daily.get(date_key, {})

    guesses = today_game.get("guesses", [])
    status = today_game.get(
        "status",
        "playing",
    )

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

    identity, error = _authenticated_identity()
    if error:
        return error

    uid = identity["uid"]
    email = identity["email"]

    payload = request.get_json(silent=True) or {}
    guess = str(
        payload.get("guess") or ""
    ).strip().upper()

    if not re.fullmatch(r"[A-Z]{5}", guess):
        return jsonify(
            {
                "error": (
                    "Guess must contain exactly five letters"
                )
            }
        ), 400

    _get_or_create_user(
        uid,
        email,
        identity["name"],
    )

    try:
        is_real_word = _dictionary_api_is_real_word(
            guess
        )
    except (
        requests.RequestException,
        ValueError,
        RuntimeError,
    ) as error:
        app.logger.exception(
            "Word validation failed: %s",
            error,
        )
        return jsonify(
            {
                "error": (
                    "The dictionary service is unavailable. "
                    "Please try again."
                ),
                "code": "dictionary_unavailable",
            }
        ), 503

    if not is_real_word:
        return jsonify(
            {
                "error": (
                    "That is not a recognised five-letter "
                    "word. Enter another word."
                ),
                "code": "invalid_word",
            }
        ), 400

    date_key = _today_key()

    try:
        answer = _daily_answer(date_key)
    except (
        requests.RequestException,
        ValueError,
        RuntimeError,
    ) as error:
        app.logger.exception(
            "Daily Wordle retrieval failed: %s",
            error,
        )
        return jsonify(
            {
                "error": (
                    "Could not load today's Wordle. "
                    "Please try again."
                ),
                "code": "daily_word_unavailable",
            }
        ), 503

    stats = _get_wordle_stats(uid, email)
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
            {
                "error": (
                    "You have already completed today's "
                    "Wordle"
                )
            }
        ), 409

    guesses = today_game.setdefault(
        "guesses",
        [],
    )

    if len(guesses) >= MAX_ATTEMPTS:
        return jsonify(
            {"error": "No attempts remaining"}
        ), 409

    guesses.append(guess)

    attempts_used = len(guesses)
    did_win = guess == answer
    did_lose = (
        not did_win
        and attempts_used >= MAX_ATTEMPTS
    )
    points_gained = 0

    if did_win:
        previous_win = stats.get(
            "lastWinDate"
        )

        new_combo = (
            int(stats.get("combo", 0)) + 1
            if previous_win == _yesterday_key()
            else 1
        )

        points_gained = (
            (MAX_ATTEMPTS + 1) - attempts_used
        ) * new_combo

        stats["rankScore"] = max(
            0,
            int(stats.get("rankScore", 0))
            + points_gained,
        )
        stats["combo"] = new_combo
        stats["bestCombo"] = max(
            int(stats.get("bestCombo", 0)),
            new_combo,
        )
        stats["wins"] = (
            int(stats.get("wins", 0)) + 1
        )
        stats["gamesPlayed"] = (
            int(stats.get("gamesPlayed", 0)) + 1
        )
        stats["lastPlayedDate"] = date_key
        stats["lastWinDate"] = date_key

        today_game["status"] = "won"
        today_game["pointsGained"] = (
            points_gained
        )
        today_game["completedAt"] = _now_iso()

    elif did_lose:
        stats["combo"] = 0
        stats["gamesPlayed"] = (
            int(stats.get("gamesPlayed", 0)) + 1
        )
        stats["lastPlayedDate"] = date_key

        today_game["status"] = "lost"
        today_game["pointsGained"] = 0
        today_game["completedAt"] = _now_iso()

    _save_wordle_stats(uid, stats)

    response = {
        "guess": guess,
        "evaluation": _letter_statuses(
            guess,
            answer,
        ),
        "status": today_game["status"],
        "attemptsUsed": attempts_used,
        "pointsGained": points_gained,
        "stats": _public_stats(stats),
    }

    if today_game["status"] in {
        "won",
        "lost",
    }:
        response["answer"] = answer

    return jsonify(response)


@app.get("/api/games/wordle/leaderboard")
def get_wordle_leaderboard():
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error

    _, error = _authenticated_identity()
    if error:
        return error

    try:
        limit = request.args.get(
            "limit",
            default=100,
            type=int,
        )
        limit = max(1, min(limit, 500))

        players = _build_leaderboard()

        return jsonify(
            {
                "leaderboard": players[:limit],
                "totalPlayers": len(players),
            }
        )

    except Exception as error:
        app.logger.exception(
            "Could not load Wordle leaderboard: %s",
            error,
        )
        return jsonify(
            {
                "error": (
                    "Could not load the leaderboard"
                )
            }
        ), 500


if __name__ == "__main__":
    app.run(
        debug=(
            os.getenv(
                "FLASK_DEBUG",
                "false",
            ).lower()
            == "true"
        ),
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5000")),
    )
