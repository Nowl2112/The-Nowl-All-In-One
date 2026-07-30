import calendar
import json
import os
import random
import re

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo
import feedparser
from email.utils import parsedate_to_datetime
from functools import lru_cache
from urllib.parse import urlparse
import requests
from dotenv import load_dotenv
from flask import current_app, jsonify, request, send_from_directory
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
CRON_SECRET = os.environ.get("CRON_SECRET")

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

TELEGRAM_REQUEST_TIMEOUT_SECONDS = int(
    os.getenv("TELEGRAM_REQUEST_TIMEOUT_SECONDS", "15")
)

TELEGRAM_LINK_TOKEN_LIFETIME_MINUTES = 15
TELEGRAM_REMINDER_DAYS_AHEAD = 7
TELEGRAM_NEWS_MAX_EVENTS = int(
    os.getenv("TELEGRAM_NEWS_MAX_EVENTS", "5")
)

TELEGRAM_NEWS_SUMMARY_MAX_ARTICLES = int(
    os.getenv("TELEGRAM_NEWS_SUMMARY_MAX_ARTICLES", "10")
)

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
        current_app.logger.error(
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
                current_app.logger.error(
                    "Firebase credentials were not found. Checked path: %s",
                    credentials_path,
                )
                return

            firebase_admin.initialize_app(credential)

        FIRESTORE_DB = firestore.client()
        FIREBASE_CONFIGURED = True
        current_app.logger.info(
            "Firebase Admin and Firestore initialized successfully."
        )

    except Exception as error:
        FIREBASE_CONFIGURED = False
        FIRESTORE_DB = None
        current_app.logger.exception(
            "Firebase initialization failed: %s",
            error,
        )




# ---------------------------------------------------------------------------
# Frontend serving
# ---------------------------------------------------------------------------



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
        current_app.logger.warning(
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
CALENDAR_RECURRENCE_FREQUENCIES = frozenset(
    {"none", "daily", "weekly", "monthly", "yearly"})
CALENDAR_MAX_OCCURRENCES_PER_QUERY = 1000
CALENDAR_DEFAULT_RANGE_PAST_DAYS = 31
CALENDAR_DEFAULT_RANGE_FUTURE_DAYS = 366
OCCURRENCE_ID_SEPARATOR = "__occurrence__"


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


def _normalize_recurrence(
    value: Any,
    *,
    anchor: datetime | None,
) -> tuple[dict[str, Any], str | None]:
    if value in (None, "", False):
        return {"frequency": "none", "interval": 1, "endAt": None, "count": None}, None

    if isinstance(value, str):
        value = {"frequency": value}

    if not isinstance(value, dict):
        return {}, "recurrence must be an object"

    frequency = str(value.get("frequency") or "none").strip().lower()
    if frequency == "annually":
        frequency = "yearly"
    if frequency not in CALENDAR_RECURRENCE_FREQUENCIES:
        return {}, "recurrence.frequency must be none, daily, weekly, monthly, or yearly"

    try:
        interval = int(value.get("interval", 1))
    except (TypeError, ValueError):
        return {}, "recurrence.interval must be a whole number"
    if interval < 1 or interval > 365:
        return {}, "recurrence.interval must be between 1 and 365"

    count_value = value.get("count")
    count = None
    if count_value not in (None, ""):
        try:
            count = int(count_value)
        except (TypeError, ValueError):
            return {}, "recurrence.count must be a whole number"
        if count < 1 or count > 1000:
            return {}, "recurrence.count must be between 1 and 1000"

    end_at = None
    if value.get("endAt") not in (None, ""):
        end_at, error = _parse_calendar_datetime(
            value.get("endAt"), "recurrence.endAt")
        if error:
            return {}, error
        if anchor and end_at < anchor:
            return {}, "recurrence.endAt cannot be earlier than the first occurrence"

    if frequency == "none":
        count = None
        end_at = None

    return {
        "frequency": frequency,
        "interval": interval,
        "endAt": end_at.isoformat() if end_at else None,
        "count": count,
    }, None


def _add_months(value: datetime, months: int) -> datetime:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _next_recurrence_datetime(value: datetime, frequency: str, interval: int) -> datetime:
    if frequency == "daily":
        return value + timedelta(days=interval)
    if frequency == "weekly":
        return value + timedelta(weeks=interval)
    if frequency == "monthly":
        return _add_months(value, interval)
    if frequency == "yearly":
        return _add_months(value, 12 * interval)
    return value


def _occurrence_key(value: datetime) -> str:
    return value.astimezone(SINGAPORE_TZ).isoformat()


def _split_occurrence_id(item_id: str) -> tuple[str, str | None]:
    if OCCURRENCE_ID_SEPARATOR not in item_id:
        return item_id, None
    return tuple(item_id.split(OCCURRENCE_ID_SEPARATOR, 1))


def _expand_calendar_item(
    item_id: str,
    item: dict[str, Any],
    range_start: datetime,
    range_end: datetime,
) -> list[dict[str, Any]]:
    recurrence = item.get("recurrence") if isinstance(
        item.get("recurrence"), dict) else {}
    frequency = str(recurrence.get("frequency") or "none").lower()
    anchor = _calendar_item_occurs_at(item)
    if anchor is None:
        return []

    if frequency == "none":
        response = _calendar_item_response(item_id, item)
        return [response] if range_start <= anchor < range_end else []

    interval = max(1, int(recurrence.get("interval") or 1))
    recurrence_end = _parse_stored_datetime(recurrence.get("endAt"))
    count = recurrence.get("count")
    count = int(count) if count not in (None, "") else None
    completed_keys = set(_normalize_string_list(
        item.get("completedOccurrenceKeys")))
    skipped_keys = set(_normalize_string_list(
        item.get("skippedOccurrenceKeys")))

    start_at = _parse_stored_datetime(item.get("startAt"))
    end_at = _parse_stored_datetime(item.get("endAt"))
    due_at = _parse_stored_datetime(item.get("dueAt"))
    start_offset = (start_at - anchor) if start_at else None
    end_offset = (end_at - anchor) if end_at else None
    due_offset = (due_at - anchor) if due_at else None

    results = []
    current = anchor
    index = 0
    while len(results) < CALENDAR_MAX_OCCURRENCES_PER_QUERY:
        if count is not None and index >= count:
            break
        if recurrence_end is not None and current > recurrence_end:
            break
        if current >= range_end:
            break

        key = _occurrence_key(current)
        if current >= range_start and key not in skipped_keys:
            occurrence = dict(item)
            occurrence["startAt"] = (
                current + start_offset).isoformat() if start_offset is not None else None
            occurrence["endAt"] = (
                current + end_offset).isoformat() if end_offset is not None else None
            occurrence["dueAt"] = (
                current + due_offset).isoformat() if due_offset is not None else None
            occurrence["seriesId"] = item_id
            occurrence["occurrenceKey"] = key
            occurrence["isRecurringOccurrence"] = True
            if str(item.get("itemType") or "") == "task":
                completed = key in completed_keys
                occurrence["status"] = "completed" if completed else "pending"
                occurrence["completedAt"] = key if completed else None
            occurrence_id = f"{item_id}{OCCURRENCE_ID_SEPARATOR}{key}"
            results.append(_calendar_item_response(occurrence_id, occurrence))

        next_value = _next_recurrence_datetime(current, frequency, interval)
        if next_value <= current:
            break
        current = next_value
        index += 1

    return results


def _parse_calendar_range() -> tuple[datetime | None, datetime | None, str | None]:
    now = datetime.now(SINGAPORE_TZ)
    raw_start = request.args.get("start")
    raw_end = request.args.get("end")
    if raw_start:
        range_start, error = _parse_calendar_datetime(raw_start, "start")
        if error:
            return None, None, error
    else:
        range_start = now - timedelta(days=CALENDAR_DEFAULT_RANGE_PAST_DAYS)
    if raw_end:
        range_end, error = _parse_calendar_datetime(raw_end, "end")
        if error:
            return None, None, error
    else:
        range_end = now + timedelta(days=CALENDAR_DEFAULT_RANGE_FUTURE_DAYS)
    if range_end <= range_start:
        return None, None, "end must be later than start"
    return range_start, range_end, None


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
        "recurrence": item.get("recurrence") or {"frequency": "none", "interval": 1, "endAt": None, "count": None},
        "seriesId": item.get("seriesId"),
        "occurrenceKey": item.get("occurrenceKey"),
        "isRecurringOccurrence": bool(item.get("isRecurringOccurrence", False)),
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
    range_start: datetime | None = None,
    range_end: datetime | None = None,
) -> list[dict[str, Any]]:
    snapshots = FIRESTORE_DB.collection("calendarItems").stream()
    items: list[dict[str, Any]] = []
    now = datetime.now(SINGAPORE_TZ)
    range_start = range_start or (
        now - timedelta(days=CALENDAR_DEFAULT_RANGE_PAST_DAYS))
    range_end = range_end or (
        now + timedelta(days=CALENDAR_DEFAULT_RANGE_FUTURE_DAYS))

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
            items.extend(_expand_calendar_item(
                snapshot.id, item, range_start, range_end))

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

def _build_telegram_news_message(
    summary: dict[str, Any],
    scope: str,
) -> str:
    scope_name = (
        "Singapore"
        if scope == "singapore"
        else "Global"
    )

    scope_icon = (
        "🇸🇬"
        if scope == "singapore"
        else "🌍"
    )

    headline = escape(
        str(
            summary.get("headline")
            or f"{scope_name} News Summary"
        )
    )

    overview = escape(
        str(summary.get("overview") or "").strip()
    )

    events = summary.get("events")
    if not isinstance(events, list):
        events = []

    lines = [
        f"{scope_icon} <b>{scope_name} News Summary</b>",
        "",
        f"<b>{headline}</b>",
    ]

    if overview:
        lines.extend([
            "",
            overview,
        ])

    selected_events = events[:TELEGRAM_NEWS_MAX_EVENTS]

    if selected_events:
        lines.extend([
            "",
            "<b>Key stories</b>",
            "",
        ])

    for index, event in enumerate(selected_events, start=1):
        if not isinstance(event, dict):
            continue

        title = escape(
            str(
                event.get("title")
                or "Untitled story"
            ).strip()
        )

        event_summary = escape(
            str(event.get("summary") or "").strip()
        )

        importance = escape(
            str(event.get("importance") or "").strip()
        )

        lines.append(
            f"<b>{index}. {title}</b>"
        )

        if event_summary:
            lines.append(event_summary)

        if importance:
            lines.append(
                f"<i>Why it matters:</i> {importance}"
            )

        sources = event.get("sources")
        if isinstance(sources, list) and sources:
            first_source = sources[0]

            if isinstance(first_source, dict):
                source_name = escape(
                    str(
                        first_source.get("name")
                        or "Read article"
                    ).strip()
                )

                source_url = str(
                    first_source.get("url") or ""
                ).strip()

                if source_url.startswith(
                    ("https://", "http://")
                ):
                    safe_url = escape(
                        source_url,
                        quote=True,
                    )
                    lines.append(
                        f'<a href="{safe_url}">{source_name}</a>'
                    )

        lines.append("")

    lines.extend([
        "Generated from articles published during the past 24 hours.",
        "",
        "Open The Nowl In One to view all article TLDRs.",
        "",
        "Use /unsubscribe to stop Telegram updates.",
    ])

    message = "\n".join(lines).strip()

    # Telegram sendMessage allows roughly 4096 characters.
    if len(message) > 4000:
        message = (
            message[:3900].rstrip()
            + "\n\nOpen The Nowl In One for the remaining stories."
        )

    return message


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

    now = datetime.now(SINGAPORE_TZ)
    end_time = now + timedelta(days=TELEGRAM_REMINDER_DAYS_AHEAD)
    visible_items = _load_calendar_items(
        uid=uid,
        family_name=family_name,
        visibility_scope="visible",
        range_start=now,
        range_end=end_time,
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









# ---------------------------------------------------------------------------
# User lookup for calendar tagging
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# Calendar routes
# ---------------------------------------------------------------------------

















# ---------------------------------------------------------------------------
# News configuration
# ---------------------------------------------------------------------------

CNA_RSS_FEEDS = {
    "latest": (
        "https://www.channelnewsasia.com/api/v1/"
        "rss-outbound-feed?_format=xml"
    ),
    "singapore": (
        "https://www.channelnewsasia.com/api/v1/"
        "rss-outbound-feed?_format=xml&category=10416"
    ),
    "asia": (
        "https://www.channelnewsasia.com/api/v1/"
        "rss-outbound-feed?_format=xml&category=6511"
    ),
    "world": (
        "https://www.channelnewsasia.com/api/v1/"
        "rss-outbound-feed?_format=xml&category=6311"
    ),
}

NEWS_REQUEST_TIMEOUT_SECONDS = 12
NEWS_CACHE_SECONDS = 300
NEWS_MAX_LIMIT = 100

# ---------------------------------------------------------------------------
# AI news summary configuration
# ---------------------------------------------------------------------------

HF_TOKEN = os.getenv(
    "HF_TOKEN",
    "",
).strip()

HF_NEWS_MODEL = os.getenv(
    "HF_NEWS_MODEL",
    "deepseek-ai/DeepSeek-V4-Flash:featherless-ai",
).strip()

HF_NEWS_API_URL = (
    "https://router.huggingface.co/v1/chat/completions"
)

HF_NEWS_TIMEOUT_SECONDS = int(
    os.getenv("HF_NEWS_TIMEOUT_SECONDS", "90")
)

NEWS_SUMMARY_WINDOW_HOURS = 24
# The endpoint can summarise every article returned by the feed. The limit is
# only a safety cap against unexpectedly large or malicious client payloads.
NEWS_SUMMARY_MAX_ARTICLES = int(os.getenv("NEWS_SUMMARY_MAX_ARTICLES", "100"))
NEWS_SUMMARY_MIN_ARTICLES = 1
NEWS_SUMMARY_BATCH_SIZE = max(
    1,
    min(int(os.getenv("NEWS_SUMMARY_BATCH_SIZE", "6")), 10),
)
NEWS_SUMMARY_MAX_OUTPUT_TOKENS = int(
    os.getenv("NEWS_SUMMARY_MAX_OUTPUT_TOKENS", "3000")
)

# Words that usually indicate an article has broad public importance.
# The scores are deliberately transparent and easy to adjust.
NEWS_IMPORTANCE_KEYWORDS = {
    # Major emergencies and public safety
    "breaking": 10,
    "emergency": 10,
    "earthquake": 7,
    "tsunami": 7,
    "war": 7,
    "attack": 7,
    "terror": 7,
    "explosion": 6,
    "evacuation": 6,
    "disaster": 6,
    "fatal": 7,
    "death": 6,
    "killed": 7,
    "injured": 5,
    "outbreak": 8,
    "pandemic": 9,

    # Government and major policy
    "prime minister": 9,
    "president": 8,
    "parliament": 8,
    "government": 8,
    "ministry": 7,
    "election": 9,
    "budget": 7,
    "law": 6,
    "policy": 5,
    "ban": 6,

    # Economy and infrastructure
    "recession": 8,
    "inflation": 7,
    "interest rate": 7,
    "job cuts": 8,
    "retrenchment": 8,
    "market crash": 9,
    "outage": 8,
    "disruption": 9,
    "transport": 7,
    "mrt": 8,
    "airport": 5,

    # Singapore-specific high-impact issues
    "singapore": 3,
    "scam": 5,
    "hdb": 5,
    "cpf": 5,
    "coe": 5,
    "gst": 6,
    "mas": 6,
    "moh": 6,
    "mof": 6,
    "mom": 5,
    "lta": 5,
    "police": 8,
}


def _strip_html(value: Any) -> str:
    """Remove basic HTML tags from RSS summaries."""
    text = str(value or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _parse_news_datetime(entry: Any) -> datetime | None:
    """
    Parse the publication date supplied by an RSS entry.

    feedparser commonly provides either published_parsed,
    updated_parsed, published, or updated.
    """
    parsed_time = (
        entry.get("published_parsed")
        or entry.get("updated_parsed")
    )

    if parsed_time:
        try:
            return datetime(
                parsed_time.tm_year,
                parsed_time.tm_mon,
                parsed_time.tm_mday,
                parsed_time.tm_hour,
                parsed_time.tm_min,
                parsed_time.tm_sec,
                tzinfo=ZoneInfo("UTC"),
            ).astimezone(SINGAPORE_TZ)
        except (TypeError, ValueError):
            pass

    raw_date = str(
        entry.get("published")
        or entry.get("updated")
        or ""
    ).strip()

    if not raw_date:
        return None

    try:
        parsed = parsedate_to_datetime(raw_date)

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZoneInfo("UTC"))

        return parsed.astimezone(SINGAPORE_TZ)
    except (TypeError, ValueError, OverflowError):
        return None


def _extract_news_image(entry: Any) -> str:
    """
    Attempt to retrieve an image supplied inside the RSS item.
    """
    media_content = entry.get("media_content") or []

    if isinstance(media_content, list):
        for media in media_content:
            if not isinstance(media, dict):
                continue

            url = str(media.get("url") or "").strip()
            medium = str(media.get("medium") or "").lower()

            if url and medium in {"", "image"}:
                return url

    media_thumbnail = entry.get("media_thumbnail") or []

    if isinstance(media_thumbnail, list):
        for thumbnail in media_thumbnail:
            if not isinstance(thumbnail, dict):
                continue

            url = str(thumbnail.get("url") or "").strip()
            if url:
                return url

    enclosures = entry.get("enclosures") or []

    if isinstance(enclosures, list):
        for enclosure in enclosures:
            if not isinstance(enclosure, dict):
                continue

            enclosure_type = str(
                enclosure.get("type") or ""
            ).lower()

            url = str(enclosure.get("href") or "").strip()

            if url and enclosure_type.startswith("image/"):
                return url

    return ""


def _news_importance_score(
    title: str,
    summary: str,
    *,
    region: str,
    published_at: datetime | None,
    feed_position: int,
) -> tuple[float, list[str]]:
    """
    Produce a simple editorial importance score.

    This is not a factual assessment of importance. It is a ranking
    heuristic based on:
    - keywords
    - recency
    - location relevance
    - RSS feed position
    """
    searchable_text = f"{title} {summary}".lower()
    score = 0.0
    reasons: list[str] = []

    matched_keywords: list[str] = []

    for keyword, keyword_score in NEWS_IMPORTANCE_KEYWORDS.items():
        if keyword in searchable_text:
            score += keyword_score
            matched_keywords.append(keyword)

    if matched_keywords:
        reasons.append(
            "Keywords: " + ", ".join(matched_keywords[:5])
        )

    # Give a small advantage to earlier items in the publisher's RSS feed.
    feed_position_bonus = max(0, 10 - feed_position) * 0.4
    score += feed_position_bonus

    if feed_position_bonus > 0:
        reasons.append("High RSS feed position")

    # Singapore relevance matters more in the Singapore section.
    if region == "singapore":
        score += 3
        reasons.append("Singapore section")

    # Recency remains part of importance, but does not determine it alone.
    if published_at is not None:
        age_hours = max(
            0,
            (
                datetime.now(SINGAPORE_TZ) - published_at
            ).total_seconds() / 3600,
        )

        if age_hours <= 2:
            score += 6
            reasons.append("Published within 2 hours")
        elif age_hours <= 6:
            score += 4
            reasons.append("Published within 6 hours")
        elif age_hours <= 12:
            score += 2
            reasons.append("Published within 12 hours")
        elif age_hours <= 24:
            score += 1

    return round(score, 2), reasons


def _normalize_news_entry(
    entry: Any,
    *,
    region: str,
    feed_position: int,
) -> dict[str, Any]:
    title = _strip_html(entry.get("title"))
    summary = _strip_html(
        entry.get("summary")
        or entry.get("description")
    )

    link = str(entry.get("link") or "").strip()
    published_at = _parse_news_datetime(entry)

    importance_score, importance_reasons = (
        _news_importance_score(
            title,
            summary,
            region=region,
            published_at=published_at,
            feed_position=feed_position,
        )
    )

    article_id_source = (
        str(entry.get("id") or "").strip()
        or link
        or f"{region}:{title}"
    )

    article_id = hashlib.sha256(
        article_id_source.encode("utf-8")
    ).hexdigest()[:24]

    return {
        "id": article_id,
        "title": title,
        "summary": summary,
        "url": link,
        "imageUrl": _extract_news_image(entry),
        "source": "CNA",
        "sourceDomain": (
            urlparse(link).netloc
            if link
            else "channelnewsasia.com"
        ),
        "region": region,
        "publishedAt": (
            published_at.isoformat()
            if published_at
            else None
        ),
        "importanceScore": importance_score,
        "importanceReasons": importance_reasons,
        "feedPosition": feed_position,
    }


@lru_cache(maxsize=16)
def _fetch_cna_feed_cached(
    region: str,
    cache_window: int,
) -> tuple[dict[str, Any], ...]:
    """
    cache_window changes every NEWS_CACHE_SECONDS, causing the
    lru_cache result to refresh automatically.
    """
    feed_url = CNA_RSS_FEEDS.get(region)

    if not feed_url:
        raise ValueError("Unknown news region")

    response = requests.get(
        feed_url,
        timeout=NEWS_REQUEST_TIMEOUT_SECONDS,
        headers={
            "User-Agent": (
                "The-Nowl-All-In-One/1.0 "
                "(personal RSS reader)"
            ),
            "Accept": (
                "application/rss+xml, "
                "application/xml, text/xml"
            ),
        },
    )
    response.raise_for_status()

    parsed_feed = feedparser.parse(response.content)

    if parsed_feed.bozo and not parsed_feed.entries:
        raise RuntimeError(
            f"Could not parse CNA {region} RSS feed"
        )

    articles = tuple(
        _normalize_news_entry(
            entry,
            region=region,
            feed_position=index,
        )
        for index, entry in enumerate(parsed_feed.entries)
    )

    return articles


def _fetch_cna_feed(region: str) -> list[dict[str, Any]]:
    cache_window = int(
        datetime.now().timestamp()
        // NEWS_CACHE_SECONDS
    )

    return [
        dict(article)
        for article in _fetch_cna_feed_cached(
            region,
            cache_window,
        )
    ]


def _sort_news_articles(
    articles: list[dict[str, Any]],
    sort_method: str,
) -> list[dict[str, Any]]:
    if sort_method == "importance":
        return sorted(
            articles,
            key=lambda article: (
                -float(article.get("importanceScore", 0)),
                str(article.get("publishedAt") or ""),
            ),
            reverse=False,
        )

    return sorted(
        articles,
        key=lambda article: str(
            article.get("publishedAt") or ""
        ),
        reverse=True,
    )


def _deduplicate_news_articles(
    articles: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    unique_articles: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()

    for article in articles:
        url = str(article.get("url") or "").strip()
        normalized_title = re.sub(
            r"[^a-z0-9]+",
            " ",
            str(article.get("title") or "").lower(),
        ).strip()

        if url and url in seen_urls:
            continue

        if normalized_title and normalized_title in seen_titles:
            continue

        if url:
            seen_urls.add(url)

        if normalized_title:
            seen_titles.add(normalized_title)

        unique_articles.append(article)

    return unique_articles


def _require_huggingface_configuration():
    if not HF_TOKEN:
        return jsonify({
            "error": "AI news summaries are not configured",
            "missing": ["HF_TOKEN"],
        }), 503

    if not HF_NEWS_MODEL:
        return jsonify({
            "error": "AI news model is not configured",
            "missing": ["HF_NEWS_MODEL"],
        }), 503

    return None


def _articles_from_last_hours(
    articles: list[dict[str, Any]],
    hours: int,
) -> list[dict[str, Any]]:
    """
    Keep only articles published during the requested time window.
    All stored article dates have already been converted to Singapore time.
    """
    cutoff = datetime.now(SINGAPORE_TZ) - timedelta(hours=hours)
    recent_articles: list[dict[str, Any]] = []

    for article in articles:
        published_at = _parse_stored_datetime(
            article.get("publishedAt")
        )

        if published_at is None:
            continue

        if published_at >= cutoff:
            recent_articles.append(article)

    return recent_articles


def _normalize_client_news_articles(
    value: Any,
    *,
    default_region: str,
) -> list[dict[str, Any]]:
    """
    Validate and normalize articles supplied by the frontend.

    The frontend may send the articles already displayed to the user. This
    prevents the summary endpoint from silently summarising a different RSS
    snapshot and makes the request payload self-contained.
    """
    if not isinstance(value, list):
        return []

    normalized: list[dict[str, Any]] = []

    for index, raw_article in enumerate(value):
        if not isinstance(raw_article, dict):
            continue

        title = _strip_html(raw_article.get("title"))
        summary = _strip_html(
            raw_article.get("summary")
            or raw_article.get("publisherSummary")
            or raw_article.get("description")
        )
        url = str(raw_article.get("url")
                  or raw_article.get("link") or "").strip()

        if not title:
            continue

        published_at = _parse_stored_datetime(
            raw_article.get("publishedAt")
            or raw_article.get("published_at")
            or raw_article.get("published")
        )

        article_id = str(
            raw_article.get("id")
            or raw_article.get("articleId")
            or ""
        ).strip()

        if not article_id:
            article_id_source = url or f"{default_region}:{title}:{index}"
            article_id = hashlib.sha256(
                article_id_source.encode("utf-8")
            ).hexdigest()[:24]

        try:
            importance_score = float(
                raw_article.get("importanceScore") or 0
            )
        except (TypeError, ValueError):
            importance_score = 0.0

        normalized.append({
            "id": article_id,
            "title": title,
            "summary": summary,
            "url": url,
            "imageUrl": str(raw_article.get("imageUrl") or "").strip(),
            "source": str(raw_article.get("source") or "CNA").strip() or "CNA",
            "sourceDomain": str(
                raw_article.get("sourceDomain")
                or (urlparse(url).netloc if url else "")
            ).strip(),
            "region": _normalize_news_region(
                raw_article.get("region") or default_region
            ),
            "publishedAt": published_at.isoformat() if published_at else None,
            "importanceScore": importance_score,
            "importanceReasons": _normalize_string_list(
                raw_article.get("importanceReasons")
            ),
            "feedPosition": int(raw_article.get("feedPosition") or index),
        })

    return _deduplicate_news_articles(normalized)


def _prepare_articles_for_ai(
    articles: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Only send fields that are useful for summarisation.

    This keeps the prompt smaller and prevents unnecessary data from being
    sent to the model.
    """
    prepared: list[dict[str, Any]] = []

    for index, article in enumerate(articles, start=1):
        prepared.append({
            "articleNumber": index,
            "id": str(article.get("id") or ""),
            "title": str(article.get("title") or ""),
            "publisherSummary": str(
                article.get("summary") or ""
            ),
            "publishedAt": article.get("publishedAt"),
            "source": str(article.get("source") or "CNA"),
            "url": str(article.get("url") or ""),
            "importanceScore": float(
                article.get("importanceScore") or 0
            ),
        })

    return prepared


def _build_article_tldr_prompt(
    articles: list[dict[str, Any]],
    scope: str,
) -> str:
    """Build a compact prompt that asks for one TLDR per input article."""
    scope_description = (
        "Singapore"
        if scope == "singapore"
        else "global"
    )

    article_json = json.dumps(
        articles,
        ensure_ascii=False,
        separators=(",", ":"),
    )

    return f"""
Create one concise TLDR for every supplied {scope_description} news article.

Use only the supplied article information. Do not invent facts. Preserve
uncertainty, allegations, predictions and future plans as uncertainty rather
than presenting them as confirmed events.

Return exactly one valid JSON object and no Markdown or commentary:

{{
  "articles": [
    {{
      "id": "the exact supplied article id",
      "tldr": "one or two concise sentences explaining the article",
      "importance": "one short sentence explaining why it matters"
    }}
  ]
}}

Rules:
1. Return exactly one item for every supplied article.
2. Keep each TLDR below 70 words.
3. Keep each importance sentence below 30 words.
4. Copy each supplied id exactly.
5. Do not combine different articles.
6. Ensure all JSON braces and brackets are closed.

Articles:
{article_json}
""".strip()


def _validate_article_tldr_batch(
    value: dict[str, Any],
    source_articles: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Validate a batch response and restore missing articles safely."""
    if not isinstance(value, dict):
        raise RuntimeError("The AI article TLDR response was not an object")

    for wrapper_key in ("result", "output", "data"):
        wrapped = value.get(wrapper_key)
        if isinstance(wrapped, dict):
            value = wrapped
            break

    raw_items = (
        value.get("articles")
        or value.get("summaries")
        or value.get("items")
        or []
    )
    if not isinstance(raw_items, list):
        raw_items = []

    source_by_id = {
        str(article.get("id") or "").strip(): article
        for article in source_articles
        if str(article.get("id") or "").strip()
    }

    generated_by_id: dict[str, dict[str, str]] = {}
    for item in raw_items:
        if not isinstance(item, dict):
            continue

        article_id = str(
            item.get("id")
            or item.get("articleId")
            or item.get("article_id")
            or ""
        ).strip()
        if article_id not in source_by_id:
            continue

        tldr = str(
            item.get("tldr")
            or item.get("summary")
            or item.get("overview")
            or item.get("description")
            or ""
        ).strip()
        importance = str(
            item.get("importance")
            or item.get("whyItMatters")
            or item.get("why_it_matters")
            or item.get("significance")
            or ""
        ).strip()

        if tldr:
            generated_by_id[article_id] = {
                "id": article_id,
                "tldr": tldr[:1200],
                "importance": importance[:500],
            }

    validated: list[dict[str, str]] = []
    for article in source_articles:
        article_id = str(article.get("id") or "").strip()
        generated = generated_by_id.get(article_id)

        if generated:
            validated.append(generated)
            continue

        # A publisher summary is a truthful fallback when one item is omitted
        # or a provider returns partially malformed JSON.
        fallback = str(
            article.get("publisherSummary")
            or article.get("summary")
            or article.get("title")
            or "Summary unavailable."
        ).strip()

        validated.append({
            "id": article_id,
            "tldr": fallback[:1200],
            "importance": "",
        })

    return validated


def _merge_usage(
    total: dict[str, Any],
    current: dict[str, Any],
) -> None:
    """Accumulate numeric usage fields across batch requests."""
    for key, value in current.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            total[key] = total.get(key, 0) + value


def _call_article_tldr_batch(
    articles: list[dict[str, Any]],
    scope: str,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    prompt = _build_article_tldr_prompt(articles, scope)

    request_body = {
        "model": HF_NEWS_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a careful news editor. Summarise only the "
                    "supplied article metadata. Return exactly one valid "
                    "JSON object with one TLDR for every article."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": NEWS_SUMMARY_MAX_OUTPUT_TOKENS,
        "response_format": {"type": "json_object"},
        "stream": False,
    }

    response = _post_huggingface_chat(request_body)

    if response.status_code == 401:
        raise PermissionError(
            "The Hugging Face token is invalid or lacks "
            "Inference Providers permission"
        )
    if response.status_code == 402:
        raise RuntimeError(
            "The Hugging Face account has insufficient inference credits"
        )
    if response.status_code == 429:
        raise RuntimeError(
            "The AI summary service is currently rate limited"
        )
    if response.status_code >= 400:
        current_app.logger.error(
            "Hugging Face inference failed with status %s: %s",
            response.status_code,
            response.text[:2000],
        )
        raise RuntimeError(
            "The AI provider could not generate the news summary"
        )

    try:
        response_payload = response.json()
    except requests.exceptions.JSONDecodeError as error:
        raise RuntimeError(
            "The AI provider returned an unreadable response"
        ) from error

    choices = response_payload.get("choices")
    if isinstance(choices, list) and choices:
        finish_reason = str(
            choices[0].get("finish_reason") or ""
        ).strip().lower()
        if finish_reason in {"length", "max_tokens", "token_limit"}:
            raise RuntimeError(
                "The AI article TLDR batch was cut off before completion"
            )

    content = _extract_ai_message_content(response_payload)
    try:
        parsed = _parse_ai_json_response(content)
        summaries = _validate_article_tldr_batch(parsed, articles)
    except RuntimeError:
        current_app.logger.error(
            "Invalid AI article TLDR content: %r",
            content[:5000],
        )
        raise

    usage = response_payload.get("usage")
    if not isinstance(usage, dict):
        usage = {}

    return summaries, usage


def _generate_all_article_tldrs(
    articles: list[dict[str, Any]],
    scope: str,
) -> tuple[list[dict[str, str]], dict[str, Any], int]:
    """Generate per-article TLDRs in small requests to avoid truncation."""
    all_summaries: list[dict[str, str]] = []
    total_usage: dict[str, Any] = {}
    batch_count = 0

    for start in range(0, len(articles), NEWS_SUMMARY_BATCH_SIZE):
        batch = articles[start:start + NEWS_SUMMARY_BATCH_SIZE]
        summaries, usage = _call_article_tldr_batch(batch, scope)
        all_summaries.extend(summaries)
        _merge_usage(total_usage, usage)
        batch_count += 1

    return all_summaries, total_usage, batch_count


def _article_tldrs_to_summary(
    selected_articles: list[dict[str, Any]],
    tldrs: list[dict[str, str]],
    scope: str,
) -> dict[str, Any]:
    """Convert per-article TLDRs into the shape used by the existing UI."""
    tldr_by_id = {
        str(item.get("id") or ""): item
        for item in tldrs
    }

    events: list[dict[str, Any]] = []
    for article in selected_articles:
        article_id = str(article.get("id") or "")
        generated = tldr_by_id.get(article_id, {})
        source_name = str(article.get("source") or "CNA").strip() or "CNA"
        url = str(article.get("url") or "").strip()

        sources = []
        if url.startswith(("http://", "https://")):
            sources.append({"name": source_name, "url": url})

        events.append({
            "title": str(article.get("title") or "Untitled article").strip(),
            "summary": str(generated.get("tldr") or "").strip(),
            "importance": str(generated.get("importance") or "").strip(),
            "articleIds": [article_id] if article_id else [],
            "sources": sources,
            "publishedAt": article.get("publishedAt"),
            "imageUrl": str(article.get("imageUrl") or ""),
            "url": url,
        })

    scope_name = "Singapore" if scope == "singapore" else "Global"
    return {
        "headline": f"{scope_name} news from the past 24 hours",
        "overview": (
            f"TLDRs for all {len(events)} articles published in the selected "
            "news feed during the past 24 hours."
        ),
        "events": events,
        "developingStories": [],
    }


def _build_news_summary_prompt(
    articles: list[dict[str, Any]],
    scope: str,
) -> str:
    scope_description = (
        "Singapore"
        if scope == "singapore"
        else "the world"
    )

    article_json = json.dumps(
        articles,
        ensure_ascii=False,
        indent=2,
    )

    return f"""
Create a concise news TLDR covering the most important events in
{scope_description} during the past 24 hours.

You must use only the supplied article information.

Important rules:
1. Do not add facts that are not contained in the supplied articles.
2. Do not claim an event happened merely because an article discusses a
   prediction, opinion, allegation, possibility, or upcoming event.
3. Combine articles that describe the same event.
4. Prioritise public impact, safety, government decisions, international
   significance, economic consequences and major disruptions.
5. Ignore lifestyle, entertainment and minor human-interest stories unless
   they are genuinely among the most consequential events supplied.
6. Clearly distinguish confirmed facts from allegations or developing reports.
7. Keep each event understandable to someone who has not read the articles.
8. Return valid JSON only. Do not wrap the JSON in Markdown fences.

Use this exact JSON structure:

{{
  "headline": "One-sentence overview of the past 24 hours",
  "overview": "A short paragraph of no more than 100 words",
  "events": [
    {{
      "title": "Short event title",
      "summary": "Two or three concise sentences explaining what happened and why it matters",
      "importance": "One concise sentence explaining its significance",
      "articleIds": ["ID of supporting article"],
      "sources": [
        {{
          "name": "Publisher name",
          "url": "Article URL"
        }}
      ]
    }}
  ],
  "developingStories": [
    "Optional concise description of a story that remains uncertain or developing"
  ]
}}

Include no more than 7 events.
Use an empty developingStories array when there are none.

Articles:

{article_json}
""".strip()


def _generate_scheduled_news_summary(
    scope: str,
) -> dict[str, Any]:
    if scope not in {"singapore", "global"}:
        raise ValueError(
            "scope must be singapore or global"
        )

    feed_region = (
        "singapore"
        if scope == "singapore"
        else "world"
    )

    articles = _deduplicate_news_articles(
        _fetch_cna_feed(feed_region)
    )

    recent_articles = _articles_from_last_hours(
        articles,
        NEWS_SUMMARY_WINDOW_HOURS,
    )

    # For Telegram, use the highest-impact recent articles.
    selected_articles = _sort_news_articles(
        recent_articles,
        "importance",
    )[:TELEGRAM_NEWS_SUMMARY_MAX_ARTICLES]

    if not selected_articles:
        return {
            "headline": (
                f"No major {scope} news articles were "
                "available from the past 24 hours."
            ),
            "overview": (
                "No eligible articles were found in the current news feed."
            ),
            "events": [],
            "developingStories": [],
        }

    ai_articles = _prepare_articles_for_ai(
        selected_articles
    )

    # Use your combined-event summary function, rather than producing
    # one Telegram entry for every article.
    summary, _usage = _call_news_summary_model(
        ai_articles,
        scope,
    )

    return summary


def _extract_ai_message_content(
    response_payload: dict[str, Any],
) -> str:
    """Extract text from an OpenAI-compatible chat completion response."""
    try:
        choices = response_payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ValueError("No completion choices were returned")

        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            raise ValueError("The completion choice was invalid")

        message = first_choice.get("message")
        if not isinstance(message, dict):
            raise ValueError("The completion message was invalid")

        content = message.get("content")
        text_parts: list[str] = []

        if isinstance(content, str):
            text_parts.append(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, str):
                    text_parts.append(part)
                elif isinstance(part, dict):
                    part_text = part.get("text") or part.get("content")
                    if isinstance(part_text, str):
                        text_parts.append(part_text)

        # Some reasoning-model providers return the final answer in a
        # provider-specific field rather than message.content.
        if not any(part.strip() for part in text_parts):
            for field_name in (
                "final",
                "final_answer",
                "answer",
                "output_text",
            ):
                value = message.get(field_name)
                if isinstance(value, str) and value.strip():
                    text_parts.append(value)
                    break

        final_content = "\n".join(text_parts).strip()
        if not final_content:
            raise ValueError("The model returned an empty response")

        return final_content

    except (TypeError, ValueError) as error:
        current_app.logger.error(
            "Unexpected Hugging Face response: %r",
            response_payload,
        )
        raise RuntimeError(
            "The AI provider returned an unexpected response"
        ) from error


def _parse_ai_json_response(content: str) -> dict[str, Any]:
    """
    Parse a complete top-level JSON object from the model response.

    This parser deliberately avoids accepting nested event objects as the
    summary root. It also repairs the provider-specific case where the model
    returns all top-level fields but omits only the first opening brace.
    """
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("The AI returned an empty summary")

    cleaned = content.strip().lstrip("\ufeff")

    # Remove hidden reasoning and Markdown fences without touching JSON braces.
    cleaned = re.sub(
        r"<think>.*?</think>",
        "",
        cleaned,
        flags=re.IGNORECASE | re.DOTALL,
    ).strip()
    cleaned = re.sub(
        r"^\s*```(?:json)?\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\s*```\s*$", "", cleaned).strip()

    candidate_strings: list[str] = [cleaned]

    # Some provider responses have been observed to omit only the first "{"
    # while still returning the rest of the complete object.
    if (
        not cleaned.startswith("{")
        and cleaned.startswith(('"', "'"))
        and any(
            key in cleaned[:500]
            for key in ('"headline"', '"overview"', '"events"', '"articles"', '"summaries"', '"items"')
        )
    ):
        candidate_strings.append("{" + cleaned)

    parse_errors: list[json.JSONDecodeError] = []

    for candidate_text in candidate_strings:
        try:
            parsed = json.loads(candidate_text)
        except json.JSONDecodeError as error:
            parse_errors.append(error)
            continue

        if not isinstance(parsed, dict):
            continue

        root_keys = set(parsed.keys())
        if not root_keys.intersection(
            {
                "headline",
                "overview",
                "events",
                "stories",
                "keyEvents",
                "key_events",
                "summary",
                "result",
                "output",
                "data",
                "articles",
                "summaries",
                "items",
            }
        ):
            continue

        return parsed

    # As a final fallback, locate complete JSON objects, but only accept one
    # that looks like the requested top-level summary. This prevents a nested
    # event object from being mistaken for the full response.
    decoder = json.JSONDecoder()

    for match in re.finditer(r"\{", cleaned):
        try:
            candidate, consumed_length = decoder.raw_decode(
                cleaned[match.start():]
            )
        except json.JSONDecodeError as error:
            parse_errors.append(error)
            continue

        if not isinstance(candidate, dict):
            continue

        root_keys = set(candidate.keys())
        if not root_keys.intersection(
            {
                "headline",
                "overview",
                "events",
                "stories",
                "keyEvents",
                "key_events",
                "summary",
                "result",
                "output",
                "data",
                "articles",
                "summaries",
                "items",
            }
        ):
            continue

        trailing = cleaned[
            match.start() + consumed_length:
        ].strip()

        if trailing:
            current_app.logger.warning(
                "Ignored text after AI summary JSON: %r",
                trailing[:1000],
            )

        return candidate

    current_app.logger.error(
        "Could not parse AI summary JSON. Raw response: %r",
        cleaned[:5000],
    )

    cause = parse_errors[-1] if parse_errors else None
    raise RuntimeError(
        "The AI generated an invalid summary format"
    ) from cause


def _post_huggingface_chat(
    request_body: dict[str, Any],
) -> requests.Response:
    """Call Hugging Face, retrying without JSON mode if unsupported."""
    response = requests.post(
        HF_NEWS_API_URL,
        headers={
            "Authorization": f"Bearer {HF_TOKEN}",
            "Content-Type": "application/json",
        },
        json=request_body,
        timeout=HF_NEWS_TIMEOUT_SECONDS,
    )

    # Provider implementations vary. Featherless may reject response_format
    # even though the router accepts OpenAI-compatible requests generally.
    if response.status_code in {400, 422} and "response_format" in request_body:
        current_app.logger.warning(
            "Provider rejected JSON response_format; retrying without it: %s",
            response.text[:1000],
        )
        fallback_body = dict(request_body)
        fallback_body.pop("response_format", None)
        response = requests.post(
            HF_NEWS_API_URL,
            headers={
                "Authorization": f"Bearer {HF_TOKEN}",
                "Content-Type": "application/json",
            },
            json=fallback_body,
            timeout=HF_NEWS_TIMEOUT_SECONDS,
        )

    return response


def _call_news_summary_model(
    articles: list[dict[str, Any]],
    scope: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    prompt = _build_news_summary_prompt(articles, scope)

    request_body = {
        "model": HF_NEWS_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a careful news editor. Summarise only the "
                    "supplied source material. Avoid speculation. Return "
                    "exactly one valid JSON object matching the requested "
                    "structure, with no reasoning or commentary outside it."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": NEWS_SUMMARY_MAX_OUTPUT_TOKENS,
        "response_format": {"type": "json_object"},
        "stream": False,
    }

    response = _post_huggingface_chat(request_body)

    if response.status_code == 401:
        raise PermissionError(
            "The Hugging Face token is invalid or lacks "
            "Inference Providers permission"
        )
    if response.status_code == 402:
        raise RuntimeError(
            "The Hugging Face account has insufficient inference credits"
        )
    if response.status_code == 429:
        raise RuntimeError(
            "The AI summary service is currently rate limited"
        )
    if response.status_code >= 400:
        current_app.logger.error(
            "Hugging Face inference failed with status %s: %s",
            response.status_code,
            response.text[:2000],
        )
        raise RuntimeError(
            "The AI provider could not generate the news summary"
        )

    try:
        response_payload = response.json()
    except requests.exceptions.JSONDecodeError as error:
        current_app.logger.error(
            "Hugging Face returned non-JSON content: %r",
            response.text[:3000],
        )
        raise RuntimeError(
            "The AI provider returned an unreadable response"
        ) from error

    if current_app.debug:
        current_app.logger.debug(
            "Hugging Face response keys: %s",
            list(response_payload.keys()),
        )
        choices = response_payload.get("choices")
        if isinstance(choices, list) and choices:
            message = choices[0].get("message", {})
            current_app.logger.debug(
                "Hugging Face message keys: %s",
                list(message.keys()) if isinstance(message, dict) else [],
            )

    choices = response_payload.get("choices")
    if isinstance(choices, list) and choices:
        finish_reason = choices[0].get("finish_reason")
        if finish_reason == "length":
            raise RuntimeError(
                "The AI summary was cut off before completion. "
                "Reduce maxArticles and try again."
            )

    content = _extract_ai_message_content(response_payload)

    try:
        parsed_summary = _parse_ai_json_response(content)
        summary = _validate_news_summary(parsed_summary)
    except RuntimeError:
        current_app.logger.error(
            "Invalid AI news summary content: %r",
            content[:5000],
        )
        raise

    usage = response_payload.get("usage")
    if not isinstance(usage, dict):
        usage = {}

    return summary, usage


def _validate_news_summary(
    summary: dict[str, Any],
) -> dict[str, Any]:
    """
    Validate and normalize the model response.

    Some providers return the requested object under keys such as "summary",
    "result", or "output". Others use slightly different field names. Accept
    those harmless variations while still enforcing a safe final structure.
    """
    if not isinstance(summary, dict):
        raise RuntimeError("The AI summary was not returned as an object")

    for wrapper_key in ("summary", "result", "output", "data"):
        wrapped = summary.get(wrapper_key)
        if isinstance(wrapped, dict):
            summary = wrapped
            break

    headline = str(
        summary.get("headline")
        or summary.get("title")
        or summary.get("topHeadline")
        or summary.get("top_headline")
        or ""
    ).strip()

    overview = str(
        summary.get("overview")
        or summary.get("tldr")
        or summary.get("summary")
        or summary.get("description")
        or ""
    ).strip()

    raw_events = (
        summary.get("events")
        or summary.get("stories")
        or summary.get("keyEvents")
        or summary.get("key_events")
        or []
    )

    raw_developing = (
        summary.get("developingStories")
        or summary.get("developing_stories")
        or summary.get("developing")
        or []
    )

    if not isinstance(raw_events, list):
        raw_events = []

    if not isinstance(raw_developing, list):
        raw_developing = []

    validated_events: list[dict[str, Any]] = []

    for event in raw_events[:7]:
        if not isinstance(event, dict):
            continue

        title = str(
            event.get("title")
            or event.get("headline")
            or event.get("name")
            or ""
        ).strip()

        event_summary = str(
            event.get("summary")
            or event.get("overview")
            or event.get("description")
            or event.get("details")
            or ""
        ).strip()

        importance = str(
            event.get("importance")
            or event.get("significance")
            or event.get("whyItMatters")
            or event.get("why_it_matters")
            or ""
        ).strip()

        if not title or not event_summary:
            continue

        article_ids = (
            event.get("articleIds")
            or event.get("article_ids")
            or event.get("supportingArticleIds")
            or []
        )
        if not isinstance(article_ids, list):
            article_ids = []

        sources = event.get("sources") or []
        if not isinstance(sources, list):
            sources = []

        clean_sources: list[dict[str, str]] = []

        for source in sources:
            if isinstance(source, str):
                if source.startswith(("http://", "https://")):
                    clean_sources.append({
                        "name": urlparse(source).netloc or "Source",
                        "url": source,
                    })
                continue

            if not isinstance(source, dict):
                continue

            name = str(
                source.get("name")
                or source.get("source")
                or source.get("publisher")
                or ""
            ).strip()
            url = str(
                source.get("url")
                or source.get("link")
                or ""
            ).strip()

            if url.startswith(("http://", "https://")):
                clean_sources.append({
                    "name": name or urlparse(url).netloc or "Source",
                    "url": url,
                })

        validated_events.append({
            "title": title,
            "summary": event_summary,
            "importance": importance,
            "articleIds": [
                str(article_id).strip()
                for article_id in article_ids
                if str(article_id).strip()
            ],
            "sources": clean_sources,
        })

    if not validated_events:
        raise RuntimeError(
            "The AI summary did not contain any valid events"
        )

    # Avoid failing the whole request when the provider omitted only the
    # headline or overview. Derive conservative fallbacks from valid events.
    if not headline:
        headline = validated_events[0]["title"]

    if not overview:
        overview = " ".join(
            event["summary"] for event in validated_events[:2]
        )[:800].strip()

    return {
        "headline": headline,
        "overview": overview,
        "events": validated_events,
        "developingStories": [
            str(story).strip()
            for story in raw_developing
            if str(story).strip()
        ],
    }


NEWS_REGIONS = frozenset({"singapore", "global", "world", "asia", "latest"})
NEWS_SORT_METHODS = frozenset({"time", "importance"})
NEWS_COMMENT_VISIBILITIES = frozenset({"private", "public"})
NEWS_MAX_TAGS = 20
NEWS_MAX_TAG_LENGTH = 40
NEWS_MAX_COMMENT_LENGTH = 4000


def _normalize_news_region(value: Any) -> str:
    region = str(value or "singapore").strip().lower()
    if region == "global":
        return "world"
    return region


def _normalize_news_tags(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []

    tags: list[str] = []
    seen: set[str] = set()

    for raw_tag in value:
        tag = re.sub(r"\s+", " ", str(raw_tag or "").strip())
        normalized = tag.lower()

        if (
            not tag
            or len(tag) > NEWS_MAX_TAG_LENGTH
            or normalized in seen
        ):
            continue

        tags.append(tag)
        seen.add(normalized)

        if len(tags) >= NEWS_MAX_TAGS:
            break

    return tags


def _news_saved_article_id(uid: str, article_id: str) -> str:
    return hashlib.sha256(
        f"{uid}:{article_id}".encode("utf-8")
    ).hexdigest()


def _news_article_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    article_id = str(payload.get("id") or "").strip()
    title = _strip_html(payload.get("title"))
    url = str(payload.get("url") or "").strip()

    if not article_id:
        raise ValueError("Article id is required")
    if not title:
        raise ValueError("Article title is required")
    if not url.startswith(("https://", "http://")):
        raise ValueError("A valid article URL is required")

    return {
        "articleId": article_id,
        "title": title[:500],
        "summary": _strip_html(payload.get("summary"))[:3000],
        "url": url,
        "imageUrl": str(payload.get("imageUrl") or "").strip(),
        "source": str(payload.get("source") or "CNA").strip()[:100],
        "sourceDomain": str(payload.get("sourceDomain") or "").strip()[:200],
        "region": _normalize_news_region(payload.get("region")),
        "publishedAt": payload.get("publishedAt"),
        "importanceScore": float(payload.get("importanceScore") or 0),
        "importanceReasons": _normalize_string_list(
            payload.get("importanceReasons")
        )[:10],
    }


def _saved_news_response(
    document_id: str,
    saved: dict[str, Any],
) -> dict[str, Any]:
    article = saved.get("article") if isinstance(
        saved.get("article"), dict
    ) else {}

    return {
        "id": document_id,
        "articleId": saved.get("articleId") or article.get("articleId"),
        "article": article,
        "tags": _normalize_news_tags(saved.get("tags")),
        "savedAt": saved.get("savedAt"),
        "updatedAt": saved.get("updatedAt"),
    }


def _news_comment_response(
    comment_id: str,
    comment: dict[str, Any],
    viewer_uid: str,
) -> dict[str, Any]:
    owner_id = str(comment.get("ownerId") or "")

    return {
        "id": comment_id,
        "articleId": comment.get("articleId"),
        "text": str(comment.get("text") or ""),
        "visibility": comment.get("visibility"),
        "ownerId": owner_id,
        "ownerDisplayName": comment.get("ownerDisplayName") or "User",
        "ownerProfilePicLink": comment.get("ownerProfilePicLink") or "",
        "isOwner": owner_id == viewer_uid,
        "createdAt": comment.get("createdAt"),
        "updatedAt": comment.get("updatedAt"),
    }























# ---------------------------------------------------------------------------
# Task board helpers
# ---------------------------------------------------------------------------


TASK_BOARD_MEMBER_ROLES = frozenset({"viewer", "editor"})
TASK_BOARD_CARD_PRIORITIES = frozenset(
    {"none", "low", "medium", "high", "urgent"})
TASK_BOARD_DEFAULT_COLUMNS = [
    {"id": "backlog", "name": "Backlog", "position": 0},
    {"id": "in_progress", "name": "In Progress", "position": 1},
    {"id": "completed", "name": "Completed", "position": 2},
    {"id": "reminders", "name": "Reminders", "position": 3},
]




def _normalize_board_columns(value: Any) -> tuple[list[dict[str, Any]], str | None]:
    if value is None:
        return [dict(column) for column in TASK_BOARD_DEFAULT_COLUMNS], None
    if not isinstance(value, list):
        return [], "columns must be an array"
    if not value:
        return [], "A board must have at least one column"
    if len(value) > 20:
        return [], "A board can have at most 20 columns"

    columns: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for index, raw_column in enumerate(value):
        if not isinstance(raw_column, dict):
            return [], "Each column must be an object"
        name = str(raw_column.get("name") or "").strip()
        raw_id = str(raw_column.get("id") or name).strip().lower()
        column_id = re.sub(r"[^a-z0-9_-]+", "_", raw_id).strip("_")
        if not name:
            return [], "Every column must have a name"
        if len(name) > 80:
            return [], "Column names cannot exceed 80 characters"
        if not column_id:
            return [], "Every column must have a valid ID"
        if column_id in used_ids:
            return [], "Column IDs must be unique"
        used_ids.add(column_id)
        columns.append({"id": column_id, "name": name, "position": index})
    return columns, None


def _task_board_response(board_id: str, board: dict[str, Any]) -> dict[str, Any]:
    members = board.get("members") if isinstance(
        board.get("members"), dict) else {}
    return {
        "id": board_id,
        "name": str(board.get("name") or ""),
        "description": str(board.get("description") or ""),
        "ownerId": board.get("ownerId"),
        "ownerDisplayName": board.get("ownerDisplayName"),
        "ownerProfilePicLink": board.get("ownerProfilePicLink") or "",
        "columns": board.get("columns") or [],
        "members": members,
        "memberIds": sorted(members.keys()),
        "createdAt": board.get("createdAt"),
        "updatedAt": board.get("updatedAt"),
    }


def _task_board_card_response(card_id: str, card: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": card_id,
        "boardId": card.get("boardId"),
        "title": str(card.get("title") or ""),
        "description": str(card.get("description") or ""),
        "columnId": card.get("columnId"),
        "position": card.get("position", 0),
        "priority": card.get("priority", "none"),
        "dueAt": card.get("dueAt"),
        "allDay": bool(card.get("allDay", False)),
        "assigneeIds": _normalize_string_list(card.get("assigneeIds")),
        "assignees": card.get("assignees") or [],
        "labels": _normalize_string_list(card.get("labels")),
        "checklist": card.get("checklist") or [],
        "calendarItemId": card.get("calendarItemId"),
        "calendarOccurrenceKey": card.get("calendarOccurrenceKey"),
        "createdBy": card.get("createdBy"),
        "createdByDisplayName": card.get("createdByDisplayName"),
        "completedAt": card.get("completedAt"),
        "createdAt": card.get("createdAt"),
        "updatedAt": card.get("updatedAt"),
    }


def _load_task_board(board_id: str):
    board_ref = _document("taskBoards", board_id)
    snapshot = board_ref.get()
    if not snapshot.exists:
        return board_ref, None
    board = snapshot.to_dict()
    return board_ref, board if isinstance(board, dict) else {}


def _task_board_user_role(board: dict[str, Any], uid: str) -> str | None:
    if str(board.get("ownerId") or "") == uid:
        return "owner"
    members = board.get("members") if isinstance(
        board.get("members"), dict) else {}
    member = members.get(uid)
    if not isinstance(member, dict):
        return None
    role = str(member.get("role") or "").strip().lower()
    return role if role in TASK_BOARD_MEMBER_ROLES else None


def _require_task_board_access(board_id: str, uid: str, *, minimum_role: str = "viewer"):
    try:
        board_ref, board = _load_task_board(board_id)
    except Exception as database_error:
        current_app.logger.exception("Could not load task board: %s", database_error)
        return None, None, (jsonify({"error": "Could not load task board"}), 500)
    if board is None:
        return None, None, (jsonify({"error": "Task board not found"}), 404)
    role = _task_board_user_role(board, uid)
    if role is None:
        return None, None, (jsonify({"error": "You do not have access to this task board"}), 403)
    levels = {"viewer": 1, "editor": 2, "owner": 3}
    if levels[role] < levels[minimum_role]:
        return None, None, (jsonify({"error": f"{minimum_role.capitalize()} access is required"}), 403)
    return board_ref, board, None


def _board_column_ids(board: dict[str, Any]) -> set[str]:
    columns = board.get("columns") if isinstance(
        board.get("columns"), list) else []
    return {
        str(column.get("id") or "").strip()
        for column in columns
        if isinstance(column, dict) and str(column.get("id") or "").strip()
    }


def _resolve_board_assignees(assignee_ids: list[str], board: dict[str, Any]):
    allowed_ids = {str(board.get("ownerId") or "").strip()}
    members = board.get("members") if isinstance(
        board.get("members"), dict) else {}
    allowed_ids.update(members.keys())
    allowed_ids.discard("")
    valid_ids, assignees, invalid_ids = [], [], []
    for uid in assignee_ids:
        if uid not in allowed_ids:
            invalid_ids.append(uid)
            continue
        user = _read_document("users", uid)
        if not user:
            invalid_ids.append(uid)
            continue
        valid_ids.append(uid)
        assignees.append({
            "uid": uid,
            "displayName": str(user.get("displayName") or "User").strip(),
            "email": _normalize_email(user.get("email")),
            "profilePicLink": _profile_picture_link(user),
        })
    return valid_ids, assignees, invalid_ids


def _resolve_initial_board_members(value: Any, owner_uid: str):
    if value in (None, ""):
        return {}, None
    if not isinstance(value, list):
        return {}, "members must be an array"
    if len(value) > 50:
        return {}, "A board can have at most 50 members"

    members = {}
    for raw_member in value:
        if not isinstance(raw_member, dict):
            return {}, "Each member must be an object"
        uid = str(raw_member.get("uid") or "").strip()
        role = str(raw_member.get("role") or "editor").strip().lower()
        if not uid:
            return {}, "Every member must include a uid"
        if uid == owner_uid:
            continue
        if role not in TASK_BOARD_MEMBER_ROLES:
            return {}, "Member role must be viewer or editor"
        user = _read_document("users", uid)
        if not user:
            return {}, f"User {uid} was not found"
        now = _now_iso()
        members[uid] = {
            "uid": uid,
            "role": role,
            "displayName": str(user.get("displayName") or "User").strip(),
            "email": _normalize_email(user.get("email")),
            "profilePicLink": _profile_picture_link(user),
            "addedAt": now,
            "updatedAt": now,
        }
    return members, None


def _normalize_board_checklist(value: Any):
    if value in (None, ""):
        return [], None
    if not isinstance(value, list):
        return [], "checklist must be an array"
    if len(value) > 100:
        return [], "A checklist can contain at most 100 items"
    checklist = []
    for index, raw_item in enumerate(value):
        if isinstance(raw_item, str):
            text, completed, item_id = raw_item.strip(), False, secrets.token_hex(6)
        elif isinstance(raw_item, dict):
            text = str(raw_item.get("text") or "").strip()
            completed = bool(raw_item.get("completed", False))
            item_id = str(raw_item.get("id") or secrets.token_hex(6)).strip()
        else:
            return [], "Every checklist item must be text or an object"
        if not text:
            return [], "Checklist item text cannot be empty"
        if len(text) > 300:
            return [], "Checklist item text cannot exceed 300 characters"
        checklist.append({"id": item_id, "text": text,
                         "completed": completed, "position": index})
    return checklist, None


def _load_task_board_cards(board_id: str) -> list[dict[str, Any]]:
    snapshots = FIRESTORE_DB.collection("taskBoardCards").where(
        "boardId", "==", board_id).stream()
    cards = [_task_board_card_response(
        snapshot.id, snapshot.to_dict() or {}) for snapshot in snapshots]
    cards.sort(key=lambda card: (
        str(card.get("columnId") or ""),
        float(card.get("position") or 0),
        str(card.get("createdAt") or ""),
    ))
    return cards


# ---------------------------------------------------------------------------
# Task board routes
# ---------------------------------------------------------------------------























# ---------------------------------------------------------------------------
# Telegram routes
# ---------------------------------------------------------------------------

























# Export private helpers as well so route modules can share the original logic.
__all__ = [name for name in globals() if not name.startswith('__')]
