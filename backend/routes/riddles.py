from flask import Blueprint, current_app, jsonify, request

from core import (
    _authenticated_identity,
    _get_or_create_user,
    _profile_picture_link,
    _require_firestore,
)
from services.firebase import get_db
from services.riddle_service import (
    LEADERBOARD_BEST_SCORES,
    LEADERBOARD_WINDOW_DAYS,
    MAX_WRONG_GUESSES,
    all_time_score,
    answer_matches,
    calculate_points,
    get_or_create_daily_riddle,
    get_player,
    make_hint,
    now_iso,
    public_riddle,
    public_stats,
    rolling_score,
    save_player,
    today_key,
)


bp = Blueprint("riddles", __name__)


def _identity():
    firestore_error = _require_firestore()
    if firestore_error:
        return None, firestore_error
    return _authenticated_identity()


def _today_state(identity):
    daily = get_or_create_daily_riddle()
    player = get_player(identity["uid"], identity["email"])
    game = (player.get("daily") or {}).get(
        daily["date"],
        {
            "status": "playing",
            "wrongGuesses": 0,
            "hintsUsed": 0,
            "guesses": [],
        },
    )
    return daily, player, game


@bp.get("/api/games/riddles/today")
def get_daily_riddle():
    identity, error = _identity()
    if error:
        return error
    try:
        _get_or_create_user(
            identity["uid"], identity["email"], identity["name"])
        daily, player, game = _today_state(identity)
        finished = game.get("status") in {"won", "lost"}
        return jsonify(
            {
                "riddle": public_riddle(daily, reveal_answer=finished),
                "game": {
                    "status": game.get("status", "playing"),
                    "wrongGuesses": int(game.get("wrongGuesses", 0)),
                    "attemptsRemaining": max(
                        0, MAX_WRONG_GUESSES - int(game.get("wrongGuesses", 0))
                    ),
                    "hintsUsed": int(game.get("hintsUsed", 0)),
                    "hint": game.get("hint"),
                    "pointsGained": int(game.get("pointsGained", 0)),
                    "saved": daily["id"] in (player.get("savedRiddleIds") or []),
                },
                "stats": public_stats(player),
            }
        )
    except Exception as exc:
        current_app.logger.exception("Could not load daily riddle: %s", exc)
        return jsonify({"error": "Could not load today's riddle"}), 503


@bp.post("/api/games/riddles/guess")
def submit_riddle_guess():
    identity, error = _identity()
    if error:
        return error

    guess = str((request.get_json(silent=True)
                or {}).get("guess") or "").strip()
    if not guess or len(guess) > 120:
        return jsonify({"error": "Enter an answer between 1 and 120 characters"}), 400

    daily, player, game = _today_state(identity)
    if game.get("status") != "playing":
        return jsonify({"error": "You have already completed today's riddle"}), 409

    game.setdefault("guesses", []).append(guess)
    did_win = answer_matches(guess, daily)
    points = 0

    if did_win:
        points = calculate_points(
            int(game.get("wrongGuesses", 0)),
            int(game.get("hintsUsed", 0)),
        )
        game.update(
            {
                "status": "won",
                "pointsGained": points,
                "completedAt": now_iso(),
            }
        )
        player["wins"] = int(player.get("wins", 0)) + 1
        player["gamesPlayed"] = int(player.get("gamesPlayed", 0)) + 1
        player["lifetimePoints"] = int(
            player.get("lifetimePoints", 0)) + points
    else:
        game["wrongGuesses"] = int(game.get("wrongGuesses", 0)) + 1
        if game["wrongGuesses"] >= MAX_WRONG_GUESSES:
            game.update(
                {
                    "status": "lost",
                    "pointsGained": 0,
                    "completedAt": now_iso(),
                }
            )
            player["gamesPlayed"] = int(player.get("gamesPlayed", 0)) + 1

    player.setdefault("daily", {})[daily["date"]] = game
    save_player(identity["uid"], player)
    finished = game["status"] in {"won", "lost"}

    return jsonify(
        {
            "correct": did_win,
            "status": game["status"],
            "wrongGuesses": int(game.get("wrongGuesses", 0)),
            "attemptsRemaining": max(
                0, MAX_WRONG_GUESSES - int(game.get("wrongGuesses", 0))
            ),
            "pointsGained": points,
            "answer": daily.get("answer") if finished else None,
            "stats": public_stats(player),
        }
    )


@bp.post("/api/games/riddles/hint")
def get_riddle_hint():
    identity, error = _identity()
    if error:
        return error

    daily, player, game = _today_state(identity)
    if game.get("status") != "playing":
        return jsonify({"error": "Today's riddle is already complete"}), 409

    if not game.get("hint"):
        game["hint"] = make_hint(daily)
        game["hintsUsed"] = 1
        player.setdefault("daily", {})[daily["date"]] = game
        save_player(identity["uid"], player)

    return jsonify({"hint": game["hint"], "hintsUsed": game["hintsUsed"]})


@bp.get("/api/games/riddles/me")
def get_riddle_player():
    identity, error = _identity()
    if error:
        return error
    player = get_player(identity["uid"], identity["email"])
    return jsonify(
        {
            "player": {
                **public_stats(player),
                "savedCount": len(player.get("savedRiddleIds") or []),
            }
        }
    )


@bp.get("/api/games/riddles/leaderboard")
def get_riddle_leaderboard():
    identity, error = _identity()
    if error:
        return error

    limit = max(1, min(request.args.get("limit", 100, type=int), 500))
    users_by_id = {
        snapshot.id: (snapshot.to_dict() or {})
        for snapshot in get_db().collection("users").stream()
    }
    players = []
    for snapshot in get_db().collection("riddleUsers").stream():
        player = snapshot.to_dict() or {}
        user = users_by_id.get(snapshot.id, {})
        email = player.get("email") or user.get("email") or ""
        players.append(
            {
                "userId": snapshot.id,
                "displayName": user.get("displayName")
                or (email.split("@")[0] if email else "Player"),
                "profilePicLink": _profile_picture_link(user),
                "allTimeScore": all_time_score(player),
                "monthlyScore": rolling_score(player),
                # Compatibility aliases for older clients.
                "lifetimePoints": all_time_score(player),
                "rollingScore": rolling_score(player),
                "wins": int(player.get("wins", 0)),
            }
        )

    # Permanent all-time points determine the main leaderboard rank.
    players.sort(
        key=lambda item: (
            -item["allTimeScore"],
            -item["wins"],
            item["displayName"].lower(),
        )
    )
    for index, player in enumerate(players, start=1):
        player["rank"] = index
        player["allTimeRank"] = index

    monthly_players = sorted(
        players,
        key=lambda item: (
            -item["monthlyScore"],
            -item["wins"],
            item["displayName"].lower(),
        ),
    )
    monthly_ranks = {
        player["userId"]: index
        for index, player in enumerate(monthly_players, start=1)
    }
    for player in players:
        player["monthlyRank"] = monthly_ranks[player["userId"]]

    return jsonify(
        {
            "leaderboard": players[:limit],
            "totalPlayers": len(players),
            "scoringWindow": {
                "days": LEADERBOARD_WINDOW_DAYS,
                "bestScoresCounted": LEADERBOARD_BEST_SCORES,
            },
            "rankedBy": "allTimeScore",
        }
    )


@bp.post("/api/games/riddles/saved")
def save_riddle():
    identity, error = _identity()
    if error:
        return error

    payload = request.get_json(silent=True) or {}
    riddle_id = str(payload.get("riddleId") or "").strip()
    if not riddle_id:
        return jsonify({"error": "riddleId is required"}), 400

    daily_matches = [
        snapshot
        for snapshot in get_db().collection("riddleDaily").stream()
        if (snapshot.to_dict() or {}).get("id") == riddle_id
    ]
    if not daily_matches:
        return jsonify({"error": "Riddle not found"}), 404

    player = get_player(identity["uid"], identity["email"])
    saved = player.setdefault("savedRiddleIds", [])
    if riddle_id not in saved:
        saved.append(riddle_id)
        save_player(identity["uid"], player)
    return jsonify({"saved": True, "riddleId": riddle_id}), 201


@bp.delete("/api/games/riddles/saved/<riddle_id>")
def unsave_riddle(riddle_id):
    identity, error = _identity()
    if error:
        return error
    player = get_player(identity["uid"], identity["email"])
    player["savedRiddleIds"] = [
        value for value in player.get("savedRiddleIds", []) if value != riddle_id
    ]
    save_player(identity["uid"], player)
    return "", 204


@bp.get("/api/games/riddles/saved")
def list_saved_riddles():
    identity, error = _identity()
    if error:
        return error

    player = get_player(identity["uid"], identity["email"])
    saved_ids = set(player.get("savedRiddleIds") or [])
    results = {}
    for snapshot in get_db().collection("riddleDaily").stream():
        daily = snapshot.to_dict() or {}
        if daily.get("id") in saved_ids:
            results[daily["id"]] = public_riddle(daily, reveal_answer=True)
    return jsonify({"riddles": list(results.values())})
