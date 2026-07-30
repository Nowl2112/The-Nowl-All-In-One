"""HTTP routes for this feature area."""

from flask import Blueprint

from core import *  # noqa: F403 - shared legacy helpers during migration

bp = Blueprint("health", __name__)


@bp.get("/health")
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
