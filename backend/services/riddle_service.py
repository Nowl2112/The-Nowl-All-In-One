from __future__ import annotations

import hashlib
import os
import re
import unicodedata
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import requests
from google.cloud import firestore

from services.firebase import get_db


SGT = ZoneInfo("Asia/Singapore")
MAX_WRONG_GUESSES = 5
BASE_POINTS = 100
WRONG_GUESS_PENALTY = 10
HINT_PENALTY = 15
MINIMUM_WIN_POINTS = 40
MAX_COMBO = 10


def now_iso() -> str:
    return datetime.now(SGT).isoformat()


def today_key() -> str:
    return datetime.now(SGT).date().isoformat()


def current_month_key(value: str | date | None = None) -> str:
    if value is None:
        parsed = datetime.now(SGT).date()
    elif isinstance(value, date):
        parsed = value
    else:
        parsed = date.fromisoformat(value)
    return parsed.strftime("%Y-%m")


def normalize_answer(value: Any) -> str:
    value = unicodedata.normalize("NFKD", str(value or "").lower())
    value = "".join(c for c in value if not unicodedata.combining(c))
    value = re.sub(r"[^a-z0-9\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return re.sub(r"^(?:a|an|the)\s+", "", value)


def answer_matches(guess: str, daily: dict[str, Any]) -> bool:
    accepted = [daily.get("answer"), *(daily.get("acceptedAnswers") or [])]
    normalized_guess = normalize_answer(guess)
    return bool(normalized_guess) and normalized_guess in {
        normalize_answer(answer) for answer in accepted if answer
    }


def calculate_points(wrong_guesses: int, hints_used: int) -> int:
    return max(
        MINIMUM_WIN_POINTS,
        BASE_POINTS
        - wrong_guesses * WRONG_GUESS_PENALTY
        - hints_used * HINT_PENALTY,
    )


def make_hint(daily: dict[str, Any]) -> str:
    words = normalize_answer(daily.get("answer")).split()
    if not words:
        return "No hint is available."
    first_letters = " ".join(f"{word[0].upper()}…" for word in words)
    lengths = ", ".join(str(len(word)) for word in words)
    return (
        f"It has {len(words)} word{'s' if len(words) != 1 else ''} "
        f"with length{'s' if len(words) != 1 else ''} {lengths}, "
        f"starting with {first_letters}."
    )


def public_riddle(
    daily: dict[str, Any], *, reveal_answer: bool = False
) -> dict[str, Any]:
    result = {
        "id": daily["id"],
        "date": daily["date"],
        "title": daily.get("title") or "Daily Riddle",
        "question": daily["question"],
        "difficulty": daily.get("difficulty") or "unknown",
        "category": daily.get("category") or "general",
        "source": daily.get("source") or "local",
    }
    if reveal_answer:
        result["answer"] = daily.get("answer")
    return result


def _api_league_riddle() -> dict[str, Any]:
    api_key = os.getenv("API_LEAGUE_KEY", "").strip()
    if not api_key:
        raise RuntimeError("API_LEAGUE_KEY is not configured")
    difficulty = os.getenv("RIDDLE_DIFFICULTY", "").strip().lower()
    if difficulty not in {"", "easy", "medium", "hard"}:
        raise RuntimeError(
            "RIDDLE_DIFFICULTY must be easy, medium, hard, or left blank"
        )
    response = requests.get(
        "https://api.apileague.com/retrieve-random-riddle",
        headers={"x-api-key": api_key},
        params={"difficulty": difficulty} if difficulty else None,
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("API League returned an invalid response")
    question = str(payload.get("riddle") or "").strip()
    answer = str(payload.get("answer") or "").strip()
    if not question or not answer:
        raise ValueError("API League returned a riddle without an answer")
    digest = hashlib.sha256(f"{question}|{answer}".encode()).hexdigest()[:20]
    return {
        "id": f"api-league-{digest}",
        "title": "Daily Riddle",
        "question": question,
        "answer": answer,
        "acceptedAnswers": [],
        "difficulty": str(payload.get("difficulty") or difficulty or "unknown"),
        "category": "general",
        "source": "api-league",
    }


def get_or_create_daily_riddle(date_string: str | None = None) -> dict[str, Any]:
    db = get_db()
    if db is None:
        raise RuntimeError("Firestore is not configured")

    date_string = date_string or today_key()
    daily_reference = db.collection("riddleDaily").document(date_string)
    existing = daily_reference.get()
    if existing.exists:
        return existing.to_dict() or {}

    try:
        max_attempts = max(1, int(os.getenv("RIDDLE_MAX_FETCH_ATTEMPTS", "25")))
    except ValueError as exc:
        raise RuntimeError("RIDDLE_MAX_FETCH_ATTEMPTS must be an integer") from exc

    for _ in range(max_attempts):
        candidate = _api_league_riddle()
        history_reference = db.collection("riddleHistory").document(candidate["id"])
        document = {
            **candidate,
            "date": date_string,
            "createdAt": now_iso(),
        }
        transaction = db.transaction()

        @firestore.transactional
        def claim_riddle(current_transaction):
            current_daily = daily_reference.get(transaction=current_transaction)
            if current_daily.exists:
                return current_daily.to_dict() or {}

            historical = history_reference.get(transaction=current_transaction)
            if historical.exists:
                return None

            # This also protects riddles saved before riddleHistory was introduced.
            previous_daily = list(
                db.collection("riddleDaily")
                .where("id", "==", candidate["id"])
                .limit(1)
                .stream(transaction=current_transaction)
            )
            if previous_daily:
                return None

            current_transaction.create(daily_reference, document)
            current_transaction.create(
                history_reference,
                {
                    "riddleId": candidate["id"],
                    "firstUsedDate": date_string,
                    "question": candidate["question"],
                    "answer": candidate["answer"],
                    "source": candidate["source"],
                    "createdAt": document["createdAt"],
                },
            )
            return document

        claimed = claim_riddle(transaction)
        if claimed is not None:
            return claimed

    raise RuntimeError(
        "API League repeatedly returned riddles that have already been used. "
        f"Tried {max_attempts} times."
    )


def default_player(uid: str, email: str) -> dict[str, Any]:
    return {
        "userId": uid,
        "email": email,
        "lifetimePoints": 0,
        "wins": 0,
        "gamesPlayed": 0,
        "combo": 0,
        "daily": {},
        "savedRiddleIds": [],
        "createdAt": now_iso(),
        "updatedAt": now_iso(),
    }


def get_player(uid: str, email: str) -> dict[str, Any]:
    db = get_db()
    reference = db.collection("riddleUsers").document(uid)
    snapshot = reference.get()
    if not snapshot.exists:
        player = default_player(uid, email)
        reference.set(player)
        return player
    player = snapshot.to_dict() or default_player(uid, email)
    if email and player.get("email") != email:
        player["email"] = email
        reference.set({"email": email, "updatedAt": now_iso()}, merge=True)
    return player


def save_player(uid: str, player: dict[str, Any]) -> None:
    player["userId"] = uid
    player["combo"] = player_combo(player)
    player["updatedAt"] = now_iso()
    get_db().collection("riddleUsers").document(uid).set(player)


def monthly_games(
    player: dict[str, Any], month: str | None = None
) -> list[tuple[str, dict[str, Any]]]:
    month = month or current_month_key()
    games = []
    for date_string, game in (player.get("daily") or {}).items():
        try:
            if current_month_key(date_string) == month:
                games.append((date_string, game))
        except ValueError:
            continue
    return sorted(games, key=lambda item: item[0])


def monthly_score(player: dict[str, Any], month: str | None = None) -> int:
    return sum(
        int(game.get("pointsGained", 0))
        for _, game in monthly_games(player, month)
        if game.get("status") == "won"
    )


def monthly_wins(player: dict[str, Any], month: str | None = None) -> int:
    return sum(
        1 for _, game in monthly_games(player, month) if game.get("status") == "won"
    )


def player_combo(player: dict[str, Any], through_date: str | None = None) -> int:
    month = current_month_key(
        through_date) if through_date else current_month_key()
    combo = 0
    for date_string, game in monthly_games(player, month):
        if through_date and date_string > through_date:
            break
        status = game.get("status")
        if status == "won":
            combo = min(MAX_COMBO, combo + 1)
        elif status == "lost":
            combo = 0
    return combo


def public_stats(player: dict[str, Any]) -> dict[str, Any]:
    permanent_score = int(player.get("lifetimePoints", 0))
    month_score = monthly_score(player)
    return {
        "userId": player.get("userId"),
        "allTimeScore": permanent_score,
        "monthlyScore": month_score,
        "lifetimePoints": permanent_score,
        "rollingScore": month_score,
        "combo": player_combo(player),
        "maxCombo": MAX_COMBO,
        "wins": int(player.get("wins", 0)),
        "gamesPlayed": int(player.get("gamesPlayed", 0)),
    }


def all_time_score(player: dict[str, Any]) -> int:
    return int(player.get("lifetimePoints", 0))


# Temporary aliases keep older imports/frontends working during deployment.
def rolling_score(player: dict[str, Any], today: str | None = None) -> int:
    return monthly_score(player, current_month_key(today) if today else None)


LEADERBOARD_WINDOW_DAYS = 0
LEADERBOARD_BEST_SCORES = 0
