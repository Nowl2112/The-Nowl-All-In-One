"""HTTP routes for this feature area."""

from flask import Blueprint

from core import *  # noqa: F403 - shared legacy helpers during migration

bp = Blueprint("wordle", __name__)


@bp.get("/api/games/wordle/me")
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



@bp.get("/api/games/wordle")
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
        current_app.logger.exception(
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



@bp.post("/api/games/wordle/guess")
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
        current_app.logger.exception(
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
        current_app.logger.exception(
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



@bp.get("/api/games/wordle/leaderboard")
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
        current_app.logger.exception(
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

