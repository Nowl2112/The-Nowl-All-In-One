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

    leaderboard_entry = next(
        (
            player
            for player in _build_leaderboard()
            if player["userId"] == uid
        ),
        None,
    )

    player = {
        **_public_stats(stats),
        "id": uid,
        "userId": uid,
        "email": email,
        "displayName": (
            user.get("displayName")
            or (email.split("@")[0] if email else "Player")
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
        return jsonify({
            "error": (
                "Could not prepare today's Wordle. "
                "Please try again."
            ),
            "code": "daily_word_unavailable",
        }), 503

    stats = _get_wordle_stats(uid, email)
    today_game = stats.setdefault("daily", {}).get(date_key, {})

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
    guess = str(payload.get("guess") or "").strip().upper()

    if not re.fullmatch(r"[A-Z]{5}", guess):
        return jsonify({
            "error": "Guess must contain exactly five letters",
        }), 400

    _get_or_create_user(
        uid,
        email,
        identity["name"],
    )

    try:
        is_real_word = _dictionary_api_is_real_word(guess)
    except (
        requests.RequestException,
        ValueError,
        RuntimeError,
    ) as error:
        current_app.logger.exception(
            "Word validation failed: %s",
            error,
        )
        return jsonify({
            "error": (
                "The dictionary service is unavailable. "
                "Please try again."
            ),
            "code": "dictionary_unavailable",
        }), 503

    if not is_real_word:
        return jsonify({
            "error": (
                "That is not a recognised five-letter word. "
                "Enter another word."
            ),
            "code": "invalid_word",
        }), 400

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
        return jsonify({
            "error": (
                "Could not load today's Wordle. "
                "Please try again."
            ),
            "code": "daily_word_unavailable",
        }), 503

    stats = _get_wordle_stats(uid, email)

    today_game = stats.setdefault("daily", {}).setdefault(
        date_key,
        {
            "guesses": [],
            "status": "playing",
        },
    )

    if today_game.get("status") != "playing":
        return jsonify({
            "error": "You have already completed today's Wordle",
        }), 409

    guesses = today_game.setdefault("guesses", [])

    if len(guesses) >= MAX_ATTEMPTS:
        return jsonify({
            "error": "No attempts remaining",
        }), 409

    guesses.append(guess)

    attempts_used = len(guesses)
    did_win = guess == answer
    did_lose = not did_win and attempts_used >= MAX_ATTEMPTS
    points_gained = 0

    if did_win:
        month_key = _month_key(date_key)
        yesterday_key = _yesterday_key()
        previous_win_date = stats.get("lastWinDate")

        # The overall streak is allowed to continue across months.
        actual_streak = (
            int(
                stats.get(
                    "currentStreak",
                    stats.get("combo", 0),
                )
                or 0
            ) + 1
            if previous_win_date == yesterday_key
            else 1
        )

        # The scoring combo only continues if the previous win
        # was yesterday and occurred within the same month.
        previous_win_month = (
            _month_key(previous_win_date)
            if previous_win_date
            else None
        )

        continued_monthly_combo = (
            previous_win_date == yesterday_key
            and previous_win_month == month_key
        )

        if continued_monthly_combo:
            scoring_combo = min(
                int(stats.get("combo", 0) or 0) + 1,
                MAX_COMBO,
            )
        else:
            scoring_combo = 1

        base_points = (MAX_ATTEMPTS + 1) - attempts_used
        points_gained = base_points * scoring_combo

        monthly_scores = stats.setdefault(
            "monthlyRankScores",
            {},
        )

        current_month_score = int(
            monthly_scores.get(month_key, 0) or 0
        )

        monthly_scores[month_key] = max(
            0,
            current_month_score + points_gained,
        )

        stats["rankScore"] = monthly_scores[month_key]
        stats["rankScoreMonth"] = month_key
        stats["lifetimeRankScore"] = max(
            0,
            int(stats.get("lifetimeRankScore", 0) or 0)
            + points_gained,
        )

        stats["currentStreak"] = actual_streak
        stats["combo"] = scoring_combo

        stats["bestCombo"] = max(
            int(stats.get("bestCombo", 0) or 0),
            scoring_combo,
        )
        stats["bestStreak"] = max(
            int(stats.get("bestStreak", 0) or 0),
            actual_streak,
        )

        stats["wins"] = int(stats.get("wins", 0) or 0) + 1
        stats["gamesPlayed"] = (
            int(stats.get("gamesPlayed", 0) or 0) + 1
        )
        stats["lastPlayedDate"] = date_key
        stats["lastWinDate"] = date_key

        today_game.update({
            "status": "won",
            "pointsGained": points_gained,
            "basePoints": base_points,
            "scoringCombo": scoring_combo,
            "completedAt": _now_iso(),
        })

    elif did_lose:
        # A loss breaks both the overall streak and
        # the current month's scoring combo.
        stats["combo"] = 0
        stats["currentStreak"] = 0
        stats["gamesPlayed"] = (
            int(stats.get("gamesPlayed", 0) or 0) + 1
        )
        stats["lastPlayedDate"] = date_key

        today_game.update({
            "status": "lost",
            "pointsGained": 0,
            "basePoints": 0,
            "scoringCombo": 0,
            "completedAt": _now_iso(),
        })

    _save_wordle_stats(uid, stats)

    response = {
        "guess": guess,
        "evaluation": _letter_statuses(guess, answer),
        "status": today_game["status"],
        "attemptsUsed": attempts_used,
        "pointsGained": points_gained,
        "stats": _public_stats(stats),
    }

    if did_win:
        response["basePoints"] = today_game["basePoints"]
        response["scoringCombo"] = today_game["scoringCombo"]

    if today_game["status"] in {"won", "lost"}:
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
        limit = max(
            1,
            min(
                request.args.get("limit", 100, type=int),
                500,
            ),
        )

        month_key = str(
            request.args.get("month") or _month_key()
        ).strip()

        if not re.fullmatch(r"\d{4}-\d{2}", month_key):
            return jsonify({
                "error": "month must use YYYY-MM",
            }), 400

        players = _build_leaderboard(month_key)

        return jsonify({
            "leaderboard": players[:limit],
            "totalPlayers": len(players),
            "month": month_key,
        })

    except Exception as error:
        current_app.logger.exception(
            "Could not load Wordle leaderboard: %s",
            error,
        )
        return jsonify({
            "error": "Could not load the leaderboard",
        }), 500


@bp.get("/api/games/wordle/podiums/last-month")
def get_last_month_wordle_podium():
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error

    _, error = _authenticated_identity()
    if error:
        return error

    month_key = _previous_month_key()
    podium = _get_monthly_podium(month_key)

    # Merely viewing the homepage must not finalize an empty
    # podium. Finalization is handled by the protected endpoint.
    if not podium:
        return jsonify({
            "podium": None,
            "month": month_key,
            "finalized": False,
        })

    return jsonify({
        "podium": podium,
        "month": month_key,
        "finalized": True,
    })


@bp.get("/api/games/wordle/podiums")
def get_wordle_podium_history():
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error

    _, error = _authenticated_identity()
    if error:
        return error

    limit = max(
        1,
        min(
            request.args.get("limit", 24, type=int),
            120,
        ),
    )

    return jsonify({
        "podiums": _list_historical_wordle_podiums(limit),
    })


@bp.post("/api/internal/wordle/finalize-month")
def finalize_wordle_month():
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error

    if not _verify_cron_secret():
        return jsonify({
            "error": "Invalid cron secret",
        }), 403

    payload = request.get_json(silent=True) or {}
    month_key = str(
        payload.get("month") or _previous_month_key()
    ).strip()

    if not re.fullmatch(r"\d{4}-\d{2}", month_key):
        return jsonify({
            "error": "month must use YYYY-MM",
        }), 400

    # Do not allow the active or a future month to be finalized.
    if month_key >= _month_key():
        return jsonify({
            "error": (
                "Only completed months can be finalized"
            ),
        }), 400

    try:
        podium = _finalize_wordle_month(month_key)
        return jsonify({"podium": podium})

    except ValueError as error:
        return jsonify({
            "error": str(error),
        }), 400

    except Exception as error:
        current_app.logger.exception(
            "Could not finalize Wordle month %s: %s",
            month_key,
            error,
        )
        return jsonify({
            "error": "Could not finalize the Wordle month",
        }), 500
