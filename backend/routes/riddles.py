from flask import Blueprint, current_app, jsonify, request

from core import (
    _authenticated_identity,
    _get_or_create_user,
    _is_main_user,
    _profile_picture_link,
    _require_firestore,
)
from services.firebase import get_db
from services.riddle_ai_service import (
    generate_riddle_hints,
    judge_riddle_answer,
)
from services.riddle_service import (
    MAX_COMBO,
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
    current_month_key,
    monthly_score,
    monthly_wins,
    player_combo,
    save_player,
    today_key,
)


bp = Blueprint("riddles", __name__)


def _archive_finished_months(users_by_id, player_documents):
    """Create immutable monthly podium snapshots on the first later request."""
    db = get_db()
    active_month = current_month_key()
    finished_months = set()
    for _, player in player_documents:
        for date_string in (player.get("daily") or {}):
            try:
                month = current_month_key(date_string)
            except ValueError:
                continue
            if month < active_month:
                finished_months.add(month)

    for month in sorted(finished_months):
        reference = db.collection("riddlePodiumHistory").document(month)
        if reference.get().exists:
            continue
        standings = []
        for uid, player in player_documents:
            score = monthly_score(player, month)
            wins = monthly_wins(player, month)
            if score <= 0 and wins <= 0:
                continue
            user = users_by_id.get(uid, {})
            email = player.get("email") or user.get("email") or ""
            standings.append(
                {
                    "userId": uid,
                    "displayName": user.get("displayName")
                    or (email.split("@")[0] if email else "Player"),
                    "profilePicLink": _profile_picture_link(user),
                    "monthlyScore": score,
                    "wins": wins,
                }
            )
        standings.sort(
            key=lambda item: (
                -item["monthlyScore"],
                -item["wins"],
                item["displayName"].lower(),
            )
        )
        podium = []
        for rank, entry in enumerate(standings[:3], start=1):
            podium.append({**entry, "rank": rank})
        document = {
            "month": month,
            "podium": podium,
            "totalPlayers": len(standings),
            "archivedAt": now_iso(),
        }
        try:
            reference.create(document)
        except Exception:
            # Another request may have archived the same month concurrently.
            if not reference.get().exists:
                raise


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
    # Use the fast, deterministic matcher first. The model is only needed for
    # non-exact guesses such as synonyms, small misspellings, or equivalent
    # phrasings.
    did_win = answer_matches(guess, daily)
    if not did_win:
        try:
            did_win = judge_riddle_answer(
                question=str(daily.get("question") or ""),
                expected_answer=str(daily.get("answer") or ""),
                user_guess=guess,
            )
        except Exception as exc:
            # A provider outage must not prevent the user from playing. In that
            # case, retain the safe result from the exact local matcher.
            current_app.logger.exception(
                "AI riddle answer judging failed; using exact-match result: %s",
                exc,
            )
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
        # A combo is a consecutive daily-win streak inside the current
        # Singapore calendar month. The displayed/awarded value is capped at 10.
        player["combo"] = player_combo(player, daily["date"])
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
            player["combo"] = 0

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

    hints = daily.get("hints")
    if not isinstance(hints, list) or len(hints) != 3:
        try:
            hints = generate_riddle_hints(
                str(daily.get("question") or ""),
                str(daily.get("answer") or ""),
            )
            # Store one shared set on the daily riddle. Future users and future
            # hint requests reuse it instead of calling the model again.
            get_db().collection("riddleDaily").document(daily["date"]).set(
                {
                    "hints": hints,
                    "hintsGeneratedAt": now_iso(),
                    "hintsSource": "ai",
                },
                merge=True,
            )
            daily["hints"] = hints
        except Exception as exc:
            current_app.logger.exception(
                "AI riddle hint generation failed; using local fallback: %s",
                exc,
            )
            hints = [make_hint(daily)]

    hints_used = int(game.get("hintsUsed", 0))
    if hints_used >= len(hints):
        return jsonify(
            {
                "error": "You have already revealed all available hints",
                "hint": game.get("hint"),
                "hintsUsed": hints_used,
            }
        ), 409

    hint = str(hints[hints_used]).strip()
    game["hintsUsed"] = hints_used + 1
    game["hint"] = hint
    game.setdefault("revealedHints", []).append(hint)
    player.setdefault("daily", {})[daily["date"]] = game
    save_player(identity["uid"], player)

    return jsonify(
        {
            "hint": hint,
            "hints": game["revealedHints"],
            "hintsUsed": game["hintsUsed"],
            "hintsRemaining": max(0, len(hints) - game["hintsUsed"]),
        }
    )


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

    requesting_user = _get_or_create_user(
        identity["uid"], identity["email"], identity.get("name")
    )
    scope = str(request.args.get("scope") or "global").strip().lower()
    if scope not in {"global", "main"}:
        return jsonify({"error": "scope must be global or main"}), 400
    if scope == "main" and not _is_main_user(requesting_user):
        return jsonify({"error": "The main-user leaderboard is private"}), 403

    limit = max(1, min(request.args.get("limit", 100, type=int), 500))
    users_by_id = {
        snapshot.id: (snapshot.to_dict() or {})
        for snapshot in get_db().collection("users").stream()
    }
    active_month = current_month_key()
    player_documents = [
        (snapshot.id, snapshot.to_dict() or {})
        for snapshot in get_db().collection("riddleUsers").stream()
    ]
    _archive_finished_months(users_by_id, player_documents)
    players = []
    for uid, player in player_documents:
        user = users_by_id.get(uid, {})
        if scope == "main" and not _is_main_user(user):
            continue
        email = player.get("email") or user.get("email") or ""
        players.append(
            {
                "userId": uid,
                "displayName": user.get("displayName")
                or (email.split("@")[0] if email else "Player"),
                "profilePicLink": _profile_picture_link(user),
                "allTimeScore": all_time_score(player),
                "monthlyScore": monthly_score(player, active_month),
                # Compatibility aliases for older clients.
                "lifetimePoints": all_time_score(player),
                "rollingScore": monthly_score(player, active_month),
                "wins": int(player.get("wins", 0)),
                "combo": player_combo(player),
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

    monthly_podium = [
        {**player, "rank": index}
        for index, player in enumerate(monthly_players[:3], start=1)
    ]

    history = []
    for snapshot in get_db().collection("riddlePodiumHistory").stream():
        item = snapshot.to_dict() or {}
        item.setdefault("month", snapshot.id)
        history.append(item)
    history.sort(key=lambda item: item.get("month", ""), reverse=True)

    return jsonify(
        {
            "leaderboard": players[:limit],
            "totalPlayers": len(players),
            "activeMonth": active_month,
            "maxCombo": MAX_COMBO,
            "monthlyPodium": monthly_podium,
            "previousPodium": (
                history[0] if history and scope == "global" else None
            ),
            "rankedBy": "allTimeScore",
            "scope": scope,
        }
    )


@bp.get("/api/games/riddles/podium-history")
def get_riddle_podium_history():
    identity, error = _identity()
    if error:
        return error

    limit = max(1, min(request.args.get("limit", 12, type=int), 60))
    history = []
    for snapshot in get_db().collection("riddlePodiumHistory").stream():
        item = snapshot.to_dict() or {}
        item.setdefault("month", snapshot.id)
        history.append(item)
    history.sort(key=lambda item: item.get("month", ""), reverse=True)
    return jsonify({"history": history[:limit]})


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
