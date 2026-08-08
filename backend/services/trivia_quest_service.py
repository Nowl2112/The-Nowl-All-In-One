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
# Weekly health is based on the normal (medium) attack cadence. Keeping the
# target in successful attacks per player makes future damage rebalancing easy
# to reason about and keeps similarly active teams fair at every party size.
TARGET_SUCCESSFUL_ATTACKS_PER_PLAYER = 240
EXPECTED_ATTACK_DAMAGE = 50
INVITE_TOKEN_BYTES = 32
MAX_QUESTION_FETCH_ATTEMPTS = 12

EXCLUDED_QUESTION_CATEGORIES = {
    "music",
    "film and tv",
    "film tv",
}

DIFFICULTY_RULES = {
    "easy": {"damage": 30, "healing": 15, "penalty": 8},
    "medium": {"damage": 50, "healing": 25, "penalty": 16},
    "hard": {"damage": 80, "healing": 40, "penalty": 30},
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
    return size * TARGET_SUCCESSFUL_ATTACKS_PER_PLAYER * EXPECTED_ATTACK_DAMAGE


def boss_health_after_team_join(
    current_health: int,
    current_maximum: int,
    new_team_size: int,
) -> tuple[int, int]:
    """Scale the boss for a join while preserving damage already dealt."""
    new_maximum = boss_max_health(new_team_size)
    added_health = max(0, new_maximum - int(current_maximum))
    return max(0, int(current_health)) + added_health, new_maximum


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


def correct_answer_from_question(question: dict[str, Any]) -> str:
    """Recover the correct displayed choice without storing a plaintext answer."""
    for choice in question.get("answers", []):
        if answer_matches_digest(
            choice,
            question.get("answerSalt", ""),
            question.get("correctAnswerDigest", ""),
        ):
            return str(choice)
    return ""


def question_category_allowed(category: Any) -> bool:
    normalized = " ".join(
        str(category or "").replace("_", " ").replace(
            "&", " and ").casefold().split()
    )
    return normalized not in EXCLUDED_QUESTION_CATEGORIES


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

        # Some providers omit IDs. A text-derived fallback remains stable so
        # the weekly repeat guard still works instead of treating every fetch
        # of the same question as new.
        question_id = str(item.get("id") or "").strip()
        if not question_id:
            normalized_text = " ".join(str(text).casefold().split())
            question_id = "text-" + hashlib.sha256(
                normalized_text.encode("utf-8")
            ).hexdigest()

        return {
            "id": question_id,
            "text": str(text),
            "difficulty": normalize_difficulty(item.get("difficulty") or normalized),
            "category": category,
            "answers": answers,
            "correctAnswer": correct,
        }
