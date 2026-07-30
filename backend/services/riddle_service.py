from __future__ import annotations

import hashlib
import os
import re
import unicodedata
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import requests

from services.firebase import get_db


SGT = ZoneInfo("Asia/Singapore")
MAX_WRONG_GUESSES = 5
BASE_POINTS = 100
WRONG_GUESS_PENALTY = 10
HINT_PENALTY = 15
MINIMUM_WIN_POINTS = 40
LEADERBOARD_WINDOW_DAYS = 30
LEADERBOARD_BEST_SCORES = 20


def now_iso() -> str:
    return datetime.now(SGT).isoformat()


def today_key() -> str:
    return datetime.now(SGT).date().isoformat()


def normalize_answer(value: Any) -> str:
    value = unicodedata.normalize("NFKD", str(value or "").lower())
    value = "".join(
        character for character in value if not unicodedata.combining(character))
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
        - (wrong_guesses * WRONG_GUESS_PENALTY)
        - (hints_used * HINT_PENALTY),
    )


def make_hint(daily: dict[str, Any]) -> str:
    answer = normalize_answer(daily.get("answer"))
    words = answer.split()
    if not words:
        return "No hint is available."

    first_letters = " ".join(f"{word[0].upper()}…" for word in words)
    lengths = ", ".join(str(len(word)) for word in words)
    return (
        f"It has {len(words)} word{'s' if len(words) != 1 else ''} "
        f"with length{'s' if len(words) != 1 else ''} {lengths}, "
        f"starting with {first_letters}."
    )


def public_riddle(daily: dict[str, Any], *, reveal_answer: bool = False) -> dict[str, Any]:
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


def _candidate(date_string: str) -> dict[str, Any]:
    return _api_league_riddle()


def get_or_create_daily_riddle(date_string: str | None = None) -> dict[str, Any]:
    db = get_db()
    if db is None:
        raise RuntimeError("Firestore is not configured")

    date_string = date_string or today_key()
    reference = db.collection("riddleDaily").document(date_string)
    existing = reference.get()
    if existing.exists:
        return existing.to_dict() or {}

    candidate = _candidate(date_string)
    document = {
        **candidate,
        "date": date_string,
        "createdAt": now_iso(),
    }

    # create() is atomic: only the first request can claim this date.
    try:
        reference.create(document)
        return document
    except Exception:
        winner = reference.get()
        if winner.exists:
            return winner.to_dict() or {}
        raise


def default_player(uid: str, email: str) -> dict[str, Any]:
    return {
        "userId": uid,
        "email": email,
        "lifetimePoints": 0,
        "wins": 0,
        "gamesPlayed": 0,
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
    player["updatedAt"] = now_iso()
    get_db().collection("riddleUsers").document(uid).set(player)


def public_stats(player: dict[str, Any]) -> dict[str, Any]:
    all_time_score = int(player.get("lifetimePoints", 0))
    return {
        "userId": player.get("userId"),
        "allTimeScore": all_time_score,
        "monthlyScore": rolling_score(player),
        # Kept for compatibility with older frontend code.
        "lifetimePoints": all_time_score,
        "rollingScore": rolling_score(player),
        "wins": int(player.get("wins", 0)),
        "gamesPlayed": int(player.get("gamesPlayed", 0)),
    }


def rolling_score(player: dict[str, Any], today: str | None = None) -> int:
    end = date.fromisoformat(today or today_key())
    start = end - timedelta(days=LEADERBOARD_WINDOW_DAYS - 1)
    scores = []
    for date_string, game in (player.get("daily") or {}).items():
        try:
            played_date = date.fromisoformat(date_string)
        except ValueError:
            continue
        if start <= played_date <= end and game.get("status") == "won":
            scores.append(int(game.get("pointsGained", 0)))
    return sum(sorted(scores, reverse=True)[:LEADERBOARD_BEST_SCORES])


def all_time_score(player: dict[str, Any]) -> int:
    """Return the permanent ranked score stored in Firestore."""
    return int(player.get("lifetimePoints", 0))
