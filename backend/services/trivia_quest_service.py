from __future__ import annotations

import hashlib
import hmac
import random
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

SINGAPORE_TZ = ZoneInfo("Asia/Singapore")
MAX_TEAM_SIZE = 5
PLAYER_MAX_HEALTH = 100
BASE_BOSS_HEALTH = 1400
BOSS_HEALTH_PER_EXTRA_PLAYER = 1400
INVITE_TOKEN_BYTES = 32

DIFFICULTY_RULES = {
    "easy": {"damage": 25, "healing": 8, "penalty": 5},
    "medium": {"damage": 40, "healing": 15, "penalty": 10},
    "hard": {"damage": 55, "healing": 25, "penalty": 18},
}


def now_singapore() -> datetime:
    return datetime.now(SINGAPORE_TZ)


def week_bounds(value: datetime | None = None) -> tuple[datetime, datetime]:
    current = (value or now_singapore()).astimezone(SINGAPORE_TZ)
    start = current.replace(hour=0, minute=0, second=0, microsecond=0)
    start -= timedelta(days=start.weekday())
    return start, start + timedelta(days=7)


def week_key(value: datetime | None = None) -> str:
    start, _ = week_bounds(value)
    iso_year, iso_week, _ = start.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def boss_max_health(team_size: int) -> int:
    size = max(1, min(int(team_size), MAX_TEAM_SIZE))
    return BASE_BOSS_HEALTH + BOSS_HEALTH_PER_EXTRA_PLAYER * (size - 1)


def normalize_difficulty(value: Any) -> str:
    difficulty = str(value or "medium").strip().lower()
    if difficulty not in DIFFICULTY_RULES:
        raise ValueError("difficulty must be easy, medium, or hard")
    return difficulty


def amounts_for(difficulty: str) -> dict[str, int]:
    return dict(DIFFICULTY_RULES[normalize_difficulty(difficulty)])


def new_invite_token() -> str:
    return secrets.token_urlsafe(INVITE_TOKEN_BYTES)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def weekly_asset_index(current_week: str, asset_count: int) -> int:
    if asset_count < 1:
        raise ValueError("asset_count must be at least 1")
    digest = hashlib.sha256(current_week.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % asset_count


def answers_match(submitted: Any, correct: Any) -> bool:
    return str(submitted or "").strip().casefold() == str(
        correct or ""
    ).strip().casefold()


def answer_digest(answer: Any, salt: str) -> str:
    normalized = str(answer or "").strip().casefold()
    return hashlib.sha256(f"{salt}\0{normalized}".encode("utf-8")).hexdigest()


def answer_matches_digest(submitted: Any, salt: str, digest: str) -> bool:
    return hmac.compare_digest(answer_digest(submitted, salt), str(digest or ""))


def public_question(question: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": question["id"],
        "text": question["text"],
        "difficulty": question["difficulty"],
        "category": question.get("category", ""),
        "answers": list(question["answers"]),
    }


@dataclass(frozen=True)
class TriviaClient:
    base_url: str = "https://the-trivia-api.com/v2"
    timeout_seconds: int = 10

    def get_question(self, difficulty: str) -> dict[str, Any]:
        import requests

        normalized = normalize_difficulty(difficulty)
        response = requests.get(
            f"{self.base_url.rstrip('/')}/questions",
            params={"limit": 1, "difficulties": normalized},
            headers={"Accept": "application/json"},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list) or not payload:
            raise ValueError("The trivia service returned no questions")

        item = payload[0]
        question_value = item.get("question")
        text = (
            question_value.get("text")
            if isinstance(question_value, dict)
            else question_value
        )
        correct = str(item.get("correctAnswer") or "").strip()
        incorrect = item.get("incorrectAnswers") or []
        if not text or not correct or not isinstance(incorrect, list):
            raise ValueError("The trivia service returned an invalid question")

        answers = [correct, *[str(answer) for answer in incorrect]]
        random.SystemRandom().shuffle(answers)
        category_value = item.get("category")
        category = (
            category_value.get("name", "")
            if isinstance(category_value, dict)
            else str(category_value or "")
        )

        return {
            "id": str(item.get("id") or secrets.token_hex(12)),
            "text": str(text),
            "difficulty": normalize_difficulty(item.get("difficulty") or normalized),
            "category": category,
            "answers": answers,
            "correctAnswer": correct,
        }
