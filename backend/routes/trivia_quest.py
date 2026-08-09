
from __future__ import annotations

import os
import re
import secrets
from datetime import datetime
from urllib.parse import quote

import requests
from flask import Blueprint, current_app, jsonify, request

from core import (
    FIRESTORE_DB,
    SINGAPORE_TZ,
    _authenticated_identity,
    _get_or_create_user,
    _now_iso,
    _profile_picture_link,
    _require_firestore,
)
from services.trivia_quest_service import (
    MAX_TEAM_SIZE,
    MAX_QUESTION_FETCH_ATTEMPTS,
    PLAYER_MAX_HEALTH,
    TriviaClient,
    answer_digest,
    answer_matches_digest,
    amounts_for,
    boss_max_health,
    boss_health_after_team_join,
    correct_answer_from_question,
    new_invite_token,
    normalize_difficulty,
    public_question,
    question_category_allowed,
    token_hash,
    weekly_asset_index,
    week_bounds,
    week_key,
)

try:
    from google.cloud import firestore
except ImportError:  # pragma: no cover
    firestore = None


bp = Blueprint("trivia_quest", __name__)

TEAMS = "triviaQuestTeams"
MEMBERSHIPS = "triviaQuestMemberships"
INVITES = "triviaQuestInvites"
BATTLES = "triviaQuestBattles"
TURNS = "triviaQuestTurns"
QUESTION_HISTORY = "triviaQuestQuestionHistory"
SEASONS = "triviaQuestSeasons"
BOSS_ASSETS = "triviaQuestBosses"
AVATAR_ASSETS = "triviaQuestAvatars"


def _doc(collection: str, document_id: str):
    return FIRESTORE_DB.collection(collection).document(document_id)


def _snapshot_dict(snapshot):
    return snapshot.to_dict() if snapshot.exists else None


def _safe_asset_url(value) -> str:
    url = str(value or "").strip()
    return url if url.startswith("https://") else ""


def _boss_asset_response(asset_id: str, asset: dict) -> dict:
    return {
        "id": asset_id,
        "name": str(asset.get("name") or "The Weekly Nowl").strip(),
        "imageUrl": _safe_asset_url(asset.get("imageUrl")),
        "battlefieldImageUrl": _safe_asset_url(asset.get("battlefieldImageUrl")),
    }


def _avatar_asset_response(asset_id: str, asset: dict) -> dict:
    return {
        "id": asset_id,
        "name": str(asset.get("name") or "Avatar").strip(),
        "imageUrl": _safe_asset_url(asset.get("imageUrl")),
    }


def _get_or_create_weekly_boss_asset(current_week: str) -> dict:
    season_ref = _doc(SEASONS, current_week)
    existing = _snapshot_dict(season_ref.get()) or {}
    if isinstance(existing.get("boss"), dict):
        return existing["boss"]

    assets = []
    for snapshot in FIRESTORE_DB.collection(BOSS_ASSETS).where("active", "==", True).stream():
        value = snapshot.to_dict() or {}
        public = _boss_asset_response(snapshot.id, value)
        if public["imageUrl"]:
            assets.append(public)
    assets.sort(key=lambda item: item["id"])
    selected = (
        assets[weekly_asset_index(current_week, len(assets))]
        if assets
        else {
            "id": "default",
            "name": "The Weekly Nowl",
            "imageUrl": "",
            "battlefieldImageUrl": "",
        }
    )
    transaction = FIRESTORE_DB.transaction()

    @firestore.transactional
    def claim(transaction):
        snapshot = season_ref.get(transaction=transaction)
        season = _snapshot_dict(snapshot) or {}
        if isinstance(season.get("boss"), dict):
            return season["boss"]
        transaction.set(
            season_ref,
            {
                "weekKey": current_week,
                "boss": selected,
                "createdAt": _now_iso(),
            },
            merge=True,
        )
        return selected

    return claim(transaction)


def _membership_id(current_week: str, uid: str) -> str:
    return f"{current_week}_{uid}"


def _battle_id(team_id: str, current_week: str) -> str:
    return f"{current_week}_{team_id}"


def _parse_time(value: str | None) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(
            str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SINGAPORE_TZ)
    return parsed.astimezone(SINGAPORE_TZ)


def _identity_and_user():
    identity, error = _authenticated_identity()
    if error:
        return None, None, error
    user = _get_or_create_user(
        identity["uid"], identity["email"], identity["name"])
    return identity, user, None


def _public_member(uid: str, user: dict, state: dict | None = None) -> dict:
    result = {
        "uid": uid,
        "displayName": user.get("displayName") or "Player",
        "profilePicLink": _profile_picture_link(user),
        "avatarId": str(user.get("triviaQuestAvatarId") or ""),
        "avatarUrl": _safe_asset_url(user.get("triviaQuestAvatarUrl")),
    }
    if state is not None:
        result.update(state)
    return result


def _team_response(team_id: str, team: dict) -> dict:
    members = []
    for uid in team.get("memberIds", []):
        user_snapshot = _doc("users", uid).get()
        members.append(_public_member(
            uid, _snapshot_dict(user_snapshot) or {}))
    return {
        "id": team_id,
        "name": team.get("name", "Boss Team"),
        "leaderId": team.get("leaderId"),
        "memberCount": len(team.get("memberIds", [])),
        "maxMembers": MAX_TEAM_SIZE,
        "members": members,
        "status": team.get("status", "forming"),
        "weekKey": team.get("weekKey"),
        "createdAt": team.get("createdAt"),
    }


def _battle_response(battle_id: str, battle: dict) -> dict:
    member_states = battle.get("memberStates", {})
    members = []
    for uid, state in member_states.items():
        user_snapshot = _doc("users", uid).get()
        members.append(_public_member(
            uid, _snapshot_dict(user_snapshot) or {}, state))
    return {
        "id": battle_id,
        "teamId": battle.get("teamId"),
        "weekKey": battle.get("weekKey"),
        "status": battle.get("status"),
        "boss": {
            "id": battle.get("bossId", "default"),
            "name": battle.get("bossName", "The Weekly Nowl"),
            "health": battle.get("bossHealth", 0),
            "maxHealth": battle.get("bossMaxHealth", 0),
            "imageUrl": _safe_asset_url(battle.get("bossImageUrl")),
            "battlefieldImageUrl": _safe_asset_url(battle.get("battlefieldImageUrl")),
        },
        "members": members,
        "questionsAnswered": battle.get("questionsAnswered", 0),
        "correctAnswers": battle.get("correctAnswers", 0),
        "startedAt": battle.get("startedAt"),
        "defeatedAt": battle.get("defeatedAt"),
        "winners": _battle_winners(battle),
    }


def _battle_winners(battle: dict) -> list[dict]:
    """Return the immutable victory result, with a legacy-data fallback."""
    stored = battle.get("winners")
    if isinstance(stored, list):
        return [winner for winner in stored if isinstance(winner, dict)]
    if battle.get("status") != "victory":
        return []
    return [
        {"uid": uid, "damage": int(state.get("damageDealt", 0))}
        for uid, state in (battle.get("memberStates") or {}).items()
        if int(state.get("damageDealt", 0)) > 0
    ]


def _current_team(uid: str):
    current_week = week_key()
    membership = _snapshot_dict(
        _doc(MEMBERSHIPS, _membership_id(current_week, uid)).get())
    if not membership:
        return None, None
    team_id = str(membership.get("teamId") or "")
    team = _snapshot_dict(_doc(TEAMS, team_id).get()) if team_id else None
    return team_id or None, team


def _trivia_client() -> TriviaClient:
    return TriviaClient(
        base_url=os.getenv("TRIVIA_API_BASE_URL",
                           "https://the-trivia-api.com/v2"),
        timeout_seconds=int(os.getenv("TRIVIA_API_TIMEOUT_SECONDS", "10")),
    )


@bp.get("/api/games/trivia-quest/assets")
def get_asset_catalog():
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error
    _, user, error = _identity_and_user()
    if error:
        return error

    bosses = []
    for snapshot in FIRESTORE_DB.collection(BOSS_ASSETS).where("active", "==", True).stream():
        asset = _boss_asset_response(snapshot.id, snapshot.to_dict() or {})
        if asset["imageUrl"]:
            bosses.append(asset)

    avatars = []
    for snapshot in FIRESTORE_DB.collection(AVATAR_ASSETS).where("active", "==", True).stream():
        asset = _avatar_asset_response(snapshot.id, snapshot.to_dict() or {})
        if asset["imageUrl"]:
            avatars.append(asset)

    bosses.sort(key=lambda item: item["name"].casefold())
    avatars.sort(key=lambda item: item["name"].casefold())
    return jsonify({
        "bosses": bosses,
        "avatars": avatars,
        "selectedAvatarId": str(user.get("triviaQuestAvatarId") or ""),
    })


@bp.post("/api/games/trivia-quest/avatar")
def select_avatar():
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error
    identity, _, error = _identity_and_user()
    if error:
        return error
    payload = request.get_json(silent=True) or {}
    avatar_id = str(payload.get("avatarId") or "").strip()
    if not avatar_id:
        _doc("users", identity["uid"]).set(
            {"triviaQuestAvatarId": "", "triviaQuestAvatarUrl": "",
                "updatedAt": _now_iso()},
            merge=True,
        )
        return jsonify({"avatar": None})

    snapshot = _doc(AVATAR_ASSETS, avatar_id).get()
    asset_data = _snapshot_dict(snapshot)
    if not asset_data or not asset_data.get("active"):
        return jsonify({"error": "Avatar not found or unavailable"}), 404
    avatar = _avatar_asset_response(avatar_id, asset_data)
    if not avatar["imageUrl"]:
        return jsonify({"error": "Avatar does not have a valid HTTPS image URL"}), 409
    _doc("users", identity["uid"]).set(
        {
            "triviaQuestAvatarId": avatar_id,
            "triviaQuestAvatarUrl": avatar["imageUrl"],
            "updatedAt": _now_iso(),
        },
        merge=True,
    )
    return jsonify({"avatar": avatar})


@bp.post("/api/games/trivia-quest/teams")
def create_team():
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error
    identity, user, error = _identity_and_user()
    if error:
        return error

    payload = request.get_json(silent=True) or {}
    name = str(payload.get("name") or "").strip()
    if not 2 <= len(name) <= 40:
        return jsonify({"error": "Team name must contain 2 to 40 characters"}), 400

    current_week = week_key()
    team_id = secrets.token_urlsafe(12)
    now = _now_iso()
    team = {
        "name": name,
        "leaderId": identity["uid"],
        "memberIds": [identity["uid"]],
        "status": "forming",
        "weekKey": current_week,
        "createdAt": now,
        "updatedAt": now,
    }
    transaction = FIRESTORE_DB.transaction()

    @firestore.transactional
    def run(transaction):
        membership_ref = _doc(MEMBERSHIPS, _membership_id(
            current_week, identity["uid"]))
        if membership_ref.get(transaction=transaction).exists:
            raise ValueError("You already belong to a team this week")
        transaction.set(_doc(TEAMS, team_id), team)
        transaction.set(membership_ref, {
            "teamId": team_id,
            "uid": identity["uid"],
            "weekKey": current_week,
            "joinedAt": now,
        })

    try:
        run(transaction)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 409
    return jsonify({"team": _team_response(team_id, team)}), 201


@bp.get("/api/games/trivia-quest/teams/me")
def get_my_team():
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error
    identity, _, error = _identity_and_user()
    if error:
        return error
    team_id, team = _current_team(identity["uid"])
    if not team:
        return jsonify({"team": None, "weekKey": week_key()})
    return jsonify({"team": _team_response(team_id, team)})


@bp.post("/api/games/trivia-quest/teams/<team_id>/invites")
def create_invite(team_id: str):
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error
    identity, _, error = _identity_and_user()
    if error:
        return error
    team = _snapshot_dict(_doc(TEAMS, team_id).get())
    if not team:
        return jsonify({"error": "Team not found"}), 404
    if team.get("leaderId") != identity["uid"]:
        return jsonify({"error": "Only the team leader can create an invite"}), 403
    if team.get("status") not in {"forming", "active"}:
        return jsonify({"error": "This team is no longer accepting members"}), 409
    if team.get("status") == "active":
        battle_id = team.get("battleId") or _battle_id(team_id, week_key())
        battle = _snapshot_dict(_doc(BATTLES, battle_id).get())
        if not battle or battle.get("status") != "active":
            return jsonify({"error": "This battle is already complete"}), 409
    if len(team.get("memberIds", [])) >= MAX_TEAM_SIZE:
        return jsonify({"error": "The team is full"}), 409

    raw_token = new_invite_token()
    hashed = token_hash(raw_token)
    _, week_end = week_bounds()
    now = _now_iso()
    _doc(INVITES, hashed).set({
        "teamId": team_id,
        "weekKey": team["weekKey"],
        "createdBy": identity["uid"],
        "createdAt": now,
        "expiresAt": week_end.isoformat(),
        "active": True,
        "useCount": 0,
    })
    public_url = os.getenv(
        "APP_PUBLIC_URL", request.host_url.rstrip("/")).rstrip("/")
    invite_url = f"{public_url}/trivia-quest/join?invite={quote(raw_token)}"
    message = f"Join {team.get('name', 'my team')} for this week's Nowl boss fight!"
    telegram_share_url = (
        "https://t.me/share/url?url=" + quote(invite_url, safe="")
        + "&text=" + quote(message, safe="")
    )
    return jsonify({
        "inviteUrl": invite_url,
        "telegramShareUrl": telegram_share_url,
        "expiresAt": week_end.isoformat(),
        "remainingPlaces": MAX_TEAM_SIZE - len(team.get("memberIds", [])),
    }), 201


@bp.delete("/api/games/trivia-quest/teams/<team_id>/members/<member_uid>")
def remove_team_member(team_id: str, member_uid: str):
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error
    identity, _, error = _identity_and_user()
    if error:
        return error

    current_week = week_key()
    transaction = FIRESTORE_DB.transaction()

    @firestore.transactional
    def run(transaction):
        team_ref = _doc(TEAMS, team_id)
        team = _snapshot_dict(team_ref.get(transaction=transaction))
        if not team or team.get("weekKey") != current_week:
            raise LookupError("Team not found")
        if team.get("leaderId") != identity["uid"]:
            raise PermissionError("Only the team leader can remove members")
        if team.get("status") != "forming":
            raise ValueError("Members can only be removed during team setup")
        if member_uid == identity["uid"]:
            raise ValueError("The team leader cannot remove themselves")

        members = list(team.get("memberIds", []))
        if member_uid not in members:
            raise LookupError("Team member not found")

        membership_ref = _doc(
            MEMBERSHIPS, _membership_id(current_week, member_uid))
        membership = _snapshot_dict(
            membership_ref.get(transaction=transaction))
        if not membership or membership.get("teamId") != team_id:
            raise ValueError("The member's team record is inconsistent")

        members.remove(member_uid)
        updated_team = {
            **team,
            "memberIds": members,
            "updatedAt": _now_iso(),
        }
        transaction.update(team_ref, {
            "memberIds": members,
            "updatedAt": updated_team["updatedAt"],
        })
        transaction.delete(membership_ref)
        return updated_team

    try:
        team = run(transaction)
    except LookupError as exc:
        return jsonify({"error": str(exc)}), 404
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 403
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 409
    return jsonify({"team": _team_response(team_id, team)})


@bp.get("/api/games/trivia-quest/invites/<raw_token>")
def preview_invite(raw_token: str):
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error
    invite = _snapshot_dict(_doc(INVITES, token_hash(raw_token)).get())
    if not invite or not invite.get("active"):
        return jsonify({"error": "Invite not found or no longer active"}), 404
    if invite.get("weekKey") != week_key() or (_parse_time(invite.get("expiresAt")) or datetime.min.replace(tzinfo=SINGAPORE_TZ)) <= datetime.now(SINGAPORE_TZ):
        return jsonify({"error": "This invite has expired"}), 410
    team = _snapshot_dict(_doc(TEAMS, invite["teamId"]).get())
    if not team or team.get("status") not in {"forming", "active"}:
        return jsonify({"error": "This team is no longer accepting members"}), 409
    if team.get("status") == "active":
        battle_id = team.get("battleId") or _battle_id(
            invite["teamId"], week_key())
        battle = _snapshot_dict(_doc(BATTLES, battle_id).get())
        if not battle or battle.get("status") != "active":
            return jsonify({"error": "This battle is already complete"}), 409
    return jsonify({"team": _team_response(invite["teamId"], team), "expiresAt": invite["expiresAt"]})


@bp.post("/api/games/trivia-quest/invites/<raw_token>/accept")
def accept_invite(raw_token: str):
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error
    identity, _, error = _identity_and_user()
    if error:
        return error
    current_week = week_key()
    invite_ref = _doc(INVITES, token_hash(raw_token))
    membership_ref = _doc(MEMBERSHIPS, _membership_id(
        current_week, identity["uid"]))
    transaction = FIRESTORE_DB.transaction()

    @firestore.transactional
    def run(transaction):
        invite_snapshot = invite_ref.get(transaction=transaction)
        invite = _snapshot_dict(invite_snapshot)
        if not invite or not invite.get("active") or invite.get("weekKey") != current_week:
            raise LookupError("Invite not found or expired")
        expires_at = _parse_time(invite.get("expiresAt"))
        if not expires_at or expires_at <= datetime.now(SINGAPORE_TZ):
            raise LookupError("Invite not found or expired")
        existing_membership = membership_ref.get(transaction=transaction)
        if existing_membership.exists:
            existing = existing_membership.to_dict() or {}
            if existing.get("teamId") == invite.get("teamId"):
                return invite["teamId"]
            raise ValueError("You already belong to another team this week")
        team_ref = _doc(TEAMS, invite["teamId"])
        team = _snapshot_dict(team_ref.get(transaction=transaction))
        if not team or team.get("status") not in {"forming", "active"}:
            raise ValueError("This team is no longer accepting members")
        members = list(team.get("memberIds", []))
        if len(members) >= MAX_TEAM_SIZE:
            raise ValueError("The team is full")
        battle_ref = None
        battle = None
        if team.get("status") == "active":
            battle_id = team.get("battleId") or _battle_id(
                invite["teamId"], current_week)
            battle_ref = _doc(BATTLES, battle_id)
            battle = _snapshot_dict(battle_ref.get(transaction=transaction))
            if not battle or battle.get("status") != "active":
                raise ValueError("This battle is already complete")
        members.append(identity["uid"])
        now = _now_iso()
        transaction.update(team_ref, {"memberIds": members, "updatedAt": now})
        transaction.set(membership_ref, {
            "teamId": invite["teamId"], "uid": identity["uid"],
            "weekKey": current_week, "joinedAt": now,
        })
        transaction.update(
            invite_ref, {"useCount": int(invite.get("useCount", 0)) + 1})
        if battle_ref is not None and battle is not None:
            new_health, new_maximum = boss_health_after_team_join(
                battle.get("bossHealth", 0),
                battle.get("bossMaxHealth", 0),
                len(members),
            )
            states = dict(battle.get("memberStates", {}))
            states[identity["uid"]] = {
                "health": PLAYER_MAX_HEALTH,
                "maxHealth": PLAYER_MAX_HEALTH,
                "questionsAnswered": 0,
                "correctAnswers": 0,
                "damageDealt": 0,
                "healingDone": 0,
            }
            transaction.update(battle_ref, {
                "bossHealth": new_health,
                "bossMaxHealth": new_maximum,
                "memberStates": states,
                "updatedAt": now,
            })
        return invite["teamId"]

    try:
        team_id = run(transaction)
    except LookupError as exc:
        return jsonify({"error": str(exc)}), 410
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 409
    team = _snapshot_dict(_doc(TEAMS, team_id).get())
    return jsonify({"team": _team_response(team_id, team)})


@bp.post("/api/games/trivia-quest/teams/<team_id>/start")
def start_battle(team_id: str):
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error
    identity, _, error = _identity_and_user()
    if error:
        return error
    current_week = week_key()
    boss_asset = _get_or_create_weekly_boss_asset(current_week)
    battle_id = _battle_id(team_id, current_week)
    transaction = FIRESTORE_DB.transaction()

    @firestore.transactional
    def run(transaction):
        team_ref = _doc(TEAMS, team_id)
        team = _snapshot_dict(team_ref.get(transaction=transaction))
        if not team or team.get("weekKey") != current_week:
            raise LookupError("Team not found")
        if team.get("leaderId") != identity["uid"]:
            raise PermissionError("Only the team leader can start the battle")
        battle_ref = _doc(BATTLES, battle_id)
        existing = _snapshot_dict(battle_ref.get(transaction=transaction))
        if existing:
            return existing
        if team.get("status") != "forming":
            raise ValueError("The battle cannot be started")
        members = list(team.get("memberIds", []))
        maximum = boss_max_health(len(members))
        now = _now_iso()
        battle = {
            "teamId": team_id, "weekKey": current_week, "status": "active",
            "bossId": boss_asset["id"],
            "bossName": boss_asset["name"],
            "bossImageUrl": boss_asset["imageUrl"],
            "battlefieldImageUrl": boss_asset["battlefieldImageUrl"],
            "bossHealth": maximum,
            "bossMaxHealth": maximum, "questionsAnswered": 0,
            "correctAnswers": 0, "startedAt": now, "updatedAt": now,
            "memberStates": {
                uid: {"health": PLAYER_MAX_HEALTH, "maxHealth": PLAYER_MAX_HEALTH,
                      "questionsAnswered": 0, "correctAnswers": 0,
                      "damageDealt": 0, "healingDone": 0}
                for uid in members
            },
        }
        transaction.set(battle_ref, battle)
        transaction.update(
            team_ref, {"status": "active", "battleId": battle_id, "updatedAt": now})
        return battle

    try:
        battle = run(transaction)
    except LookupError as exc:
        return jsonify({"error": str(exc)}), 404
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 403
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 409
    return jsonify({"battle": _battle_response(battle_id, battle)}), 201


@bp.get("/api/games/trivia-quest/battle")
def get_battle():
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error
    identity, _, error = _identity_and_user()
    if error:
        return error
    team_id, team = _current_team(identity["uid"])
    if not team:
        return jsonify({"battle": None, "weekKey": week_key()})
    battle_id = team.get("battleId") or _battle_id(team_id, week_key())
    battle = _snapshot_dict(_doc(BATTLES, battle_id).get())
    return jsonify({"battle": _battle_response(battle_id, battle) if battle else None})


@bp.post("/api/games/trivia-quest/battle/questions")
def issue_question():
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error
    identity, _, error = _identity_and_user()
    if error:
        return error
    try:
        difficulty = normalize_difficulty(
            (request.get_json(silent=True) or {}).get("difficulty"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    team_id, team = _current_team(identity["uid"])
    if not team:
        return jsonify({"error": "Join a team first"}), 409
    battle_id = team.get("battleId") or _battle_id(team_id, week_key())
    battle = _snapshot_dict(_doc(BATTLES, battle_id).get())
    state = (battle or {}).get("memberStates", {}).get(identity["uid"], {})
    if not battle or battle.get("status") != "active":
        return jsonify({"error": "There is no active battle"}), 409
    if int(state.get("health", 0)) <= 0:
        return jsonify({"error": "You must be healed before answering another question"}), 409
    current_week = week_key()
    history_ref = _doc(QUESTION_HISTORY, identity["uid"])
    history = _snapshot_dict(history_ref.get()) or {}
    seen_question_ids = set(
        history.get("questionIds", [])
        if history.get("weekKey") == current_week
        else []
    )
    question = None
    try:
        client = _trivia_client()
        for _ in range(MAX_QUESTION_FETCH_ATTEMPTS):
            candidate = client.get_question(difficulty)
            if not question_category_allowed(candidate.get("category")):
                continue
            if candidate["id"] not in seen_question_ids:
                question = candidate
                break
    except (requests.RequestException, ValueError) as exc:
        current_app.logger.warning("Trivia question request failed: %s", exc)
        return jsonify({"error": "The trivia service is temporarily unavailable"}), 503
    if question is None:
        return jsonify({
            "error": "No new questions are available at this difficulty right now. Try another difficulty."
        }), 409
    turn_id = secrets.token_urlsafe(18)
    now = _now_iso()
    correct_answer = question.pop("correctAnswer")
    answer_salt = secrets.token_hex(16)
    stored = {
        **question,
        "correctAnswerDigest": answer_digest(correct_answer, answer_salt),
        "answerSalt": answer_salt,
        "battleId": battle_id, "teamId": team_id,
        "weekKey": current_week, "userId": identity["uid"],
        "status": "awaiting_answer", "createdAt": now,
    }
    turn_ref = _doc(TURNS, turn_id)
    battle_ref = _doc(BATTLES, battle_id)
    transaction = FIRESTORE_DB.transaction()

    @firestore.transactional
    def reserve_turn(transaction):
        current = _snapshot_dict(battle_ref.get(transaction=transaction))
        if not current or current.get("status") != "active":
            raise ValueError("There is no active battle")
        states = dict(current.get("memberStates", {}))
        player = dict(states.get(identity["uid"], {}))
        if int(player.get("health", 0)) <= 0:
            raise ValueError(
                "You must be healed before answering another question")
        if player.get("activeTurnId"):
            raise ValueError(
                "Finish your current question before requesting another")
        current_history = _snapshot_dict(
            history_ref.get(transaction=transaction)) or {}
        current_ids = list(
            current_history.get("questionIds", [])
            if current_history.get("weekKey") == current_week
            else []
        )
        if question["id"] in current_ids:
            raise ValueError(
                "That question was already shown. Please draw another question")
        current_ids.append(question["id"])
        player["activeTurnId"] = turn_id
        states[identity["uid"]] = player
        transaction.update(
            battle_ref, {"memberStates": states, "updatedAt": now})
        transaction.set(history_ref, {
            "userId": identity["uid"],
            "weekKey": current_week,
            "questionIds": current_ids,
            "updatedAt": now,
        })
        transaction.set(turn_ref, stored)

    try:
        reserve_turn(transaction)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 409
    response_question = public_question({**question, "id": turn_id})
    return jsonify({"turnId": turn_id, "question": response_question}), 201


@bp.get("/api/games/trivia-quest/battle/turn")
def get_active_turn():
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error
    identity, _, error = _identity_and_user()
    if error:
        return error
    team_id, team = _current_team(identity["uid"])
    if not team:
        return jsonify({"turn": None})
    battle_id = team.get("battleId") or _battle_id(team_id, week_key())
    battle = _snapshot_dict(_doc(BATTLES, battle_id).get())
    player = (battle or {}).get("memberStates", {}).get(identity["uid"], {})
    turn_id = str(player.get("activeTurnId") or "")
    if not turn_id:
        return jsonify({"turn": None})
    turn = _snapshot_dict(_doc(TURNS, turn_id).get())
    if (
        not turn
        or turn.get("userId") != identity["uid"]
        or turn.get("weekKey") != week_key()
        or turn.get("status") not in {"awaiting_answer", "awaiting_action"}
    ):
        return jsonify({"turn": None})
    response = {
        "turnId": turn_id,
        "status": turn["status"],
        "question": public_question({**turn, "id": turn_id}),
    }
    if turn["status"] == "awaiting_action":
        rules = amounts_for(turn["difficulty"])
        response["answerResult"] = {
            "correct": True,
            "difficulty": turn["difficulty"],
            "actions": {
                "attack": rules["damage"],
                "heal": rules["healing"],
            },
        }
    return jsonify({"turn": response})


@bp.post("/api/games/trivia-quest/battle/questions/<turn_id>/answer")
def answer_question(turn_id: str):
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error
    identity, _, error = _identity_and_user()
    if error:
        return error
    submitted = (request.get_json(silent=True) or {}).get("answer")
    if not str(submitted or "").strip():
        return jsonify({"error": "answer is required"}), 400
    turn_ref = _doc(TURNS, turn_id)
    transaction = FIRESTORE_DB.transaction()

    @firestore.transactional
    def run(transaction):
        turn = _snapshot_dict(turn_ref.get(transaction=transaction))
        if not turn or turn.get("userId") != identity["uid"]:
            raise LookupError("Question not found")
        if turn.get("weekKey") != week_key():
            raise ValueError("This question expired at the weekly reset")
        if turn.get("status") != "awaiting_answer":
            raise ValueError("This question has already been answered")
        battle_ref = _doc(BATTLES, turn["battleId"])
        battle = _snapshot_dict(battle_ref.get(transaction=transaction))
        if not battle or battle.get("status") != "active":
            raise ValueError("The battle is no longer active")
        states = dict(battle.get("memberStates", {}))
        state = dict(states.get(identity["uid"], {}))
        if int(state.get("health", 0)) <= 0:
            raise ValueError("You must be healed before answering")
        correct = answer_matches_digest(
            submitted,
            turn.get("answerSalt", ""),
            turn.get("correctAnswerDigest", ""),
        )
        rules = amounts_for(turn["difficulty"])
        correct_answer = correct_answer_from_question(turn)
        if not correct_answer:
            raise ValueError("The correct answer could not be verified")
        state["questionsAnswered"] = int(state.get("questionsAnswered", 0)) + 1
        battle["questionsAnswered"] = int(
            battle.get("questionsAnswered", 0)) + 1
        update = {"answeredAt": _now_iso()}
        if correct:
            state["correctAnswers"] = int(state.get("correctAnswers", 0)) + 1
            battle["correctAnswers"] = int(battle.get("correctAnswers", 0)) + 1
            update.update({"status": "awaiting_action", "wasCorrect": True})
        else:
            state["health"] = max(
                0, int(state.get("health", 0)) - rules["penalty"])
            state["activeTurnId"] = None
            if all(int((states.get(uid) if uid != identity["uid"] else state).get("health", 0)) <= 0 for uid in states):
                battle["status"] = "party_defeated"
                battle["partyDefeatedAt"] = _now_iso()
        states[identity["uid"]] = state
        battle["memberStates"] = states
        battle["updatedAt"] = _now_iso()
        transaction.update(battle_ref, battle)
        if correct:
            transaction.update(turn_ref, update)
        else:
            # Incorrect turns need no follow-up action, so remove the temporary
            # question immediately rather than retaining an answer log.
            transaction.delete(turn_ref)
        return correct, rules, state, battle, turn["difficulty"], correct_answer

    try:
        correct, rules, state, battle, answered_difficulty, correct_answer = run(
            transaction
        )
    except LookupError as exc:
        return jsonify({"error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 409
    response = {
        "correct": correct,
        "difficulty": normalize_difficulty(answered_difficulty),
    }
    if correct:
        response["actions"] = {
            "attack": rules["damage"], "heal": rules["healing"]}
    else:
        response.update(
            {
                "correctAnswer": correct_answer,
                "damageTaken": rules["penalty"],
                "health": state["health"],
                "battleStatus": battle["status"],
            }
        )
    return jsonify(response)


@bp.post("/api/games/trivia-quest/battle/questions/<turn_id>/action")
def choose_action(turn_id: str):
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error
    identity, _, error = _identity_and_user()
    if error:
        return error
    payload = request.get_json(silent=True) or {}
    action = str(payload.get("action") or "").strip().lower()
    target_uid = str(payload.get("targetUserId") or "").strip()
    if action not in {"attack", "heal"}:
        return jsonify({"error": "action must be attack or heal"}), 400
    turn_ref = _doc(TURNS, turn_id)
    transaction = FIRESTORE_DB.transaction()

    @firestore.transactional
    def run(transaction):
        turn = _snapshot_dict(turn_ref.get(transaction=transaction))
        if not turn or turn.get("userId") != identity["uid"]:
            raise LookupError("Question not found")
        if turn.get("weekKey") != week_key():
            raise ValueError("This action expired at the weekly reset")
        if turn.get("status") != "awaiting_action" or not turn.get("wasCorrect"):
            raise ValueError("This turn has no available action")
        battle_ref = _doc(BATTLES, turn["battleId"])
        battle = _snapshot_dict(battle_ref.get(transaction=transaction))
        if not battle or battle.get("status") != "active":
            raise ValueError("The battle is no longer active")
        states = dict(battle.get("memberStates", {}))
        rules = amounts_for(turn["difficulty"])
        result = {"action": action}
        damage_done = 0
        healing_done = 0
        if action == "attack":
            before = int(battle.get("bossHealth", 0))
            battle["bossHealth"] = max(0, before - rules["damage"])
            damage_done = before - battle["bossHealth"]
            result.update(
                {"amount": damage_done, "bossHealth": battle["bossHealth"]})
            if battle["bossHealth"] == 0:
                battle["status"] = "victory"
                battle["defeatedAt"] = _now_iso()
                started_at = _parse_time(battle.get("startedAt"))
                defeated_at = _parse_time(battle["defeatedAt"])
                if started_at and defeated_at:
                    battle["victoryDurationSeconds"] = max(
                        0,
                        int((defeated_at - started_at).total_seconds()),
                    )
        else:
            if target_uid not in states:
                raise ValueError("Choose a teammate to heal")
            target = dict(states[target_uid])
            before = int(target.get("health", 0))
            target["health"] = min(
                int(target.get("maxHealth", PLAYER_MAX_HEALTH)), before + rules["healing"])
            healing_done = target["health"] - before
            states[target_uid] = target
            result.update({"targetUserId": target_uid,
                          "amount": healing_done, "targetHealth": target["health"]})
        actor = dict(states.get(identity["uid"], {}))
        actor["activeTurnId"] = None
        actor["damageDealt"] = int(actor.get("damageDealt", 0)) + damage_done
        actor["healingDone"] = int(actor.get("healingDone", 0)) + healing_done
        states[identity["uid"]] = actor
        battle["memberStates"] = states
        if battle.get("status") == "victory":
            battle["winners"] = [
                {"uid": uid, "damage": int(state.get("damageDealt", 0))}
                for uid, state in states.items()
                if int(state.get("damageDealt", 0)) > 0
            ]
        battle["updatedAt"] = _now_iso()
        transaction.update(battle_ref, battle)
        # Correct turns are retained only while the player is choosing an
        # action. Once resolved, the temporary question document is deleted.
        transaction.delete(turn_ref)
        result["battleStatus"] = battle["status"]
        return result

    try:
        result = run(transaction)
    except LookupError as exc:
        return jsonify({"error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 409
    return jsonify(result)


@bp.get("/api/games/trivia-quest/leaderboard")
def leaderboard():
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error
    _, _, error = _identity_and_user()
    if error:
        return error
    selected_week = str(request.args.get("week") or week_key()).strip()
    if not re.fullmatch(r"\d{4}-W\d{2}", selected_week):
        return jsonify({"error": "week must use YYYY-Www"}), 400
    teams = []
    completed_battles = []
    for snapshot in FIRESTORE_DB.collection(BATTLES).where("weekKey", "==", selected_week).stream():
        battle = snapshot.to_dict() or {}
        team = _snapshot_dict(
            _doc(TEAMS, battle.get("teamId", "")).get()) or {}
        maximum = max(1, int(battle.get("bossMaxHealth", 1)))
        defeated = battle.get("status") == "victory"
        winners = _battle_winners(battle)
        completed_battles.append({
            "teamId": battle.get("teamId"), "teamName": team.get("name", "Team"),
            "defeated": defeated,
            "defeatedAt": battle.get("defeatedAt"),
            "winners": winners,
        })
        states = battle.get("memberStates") or {}
        damage = sum(
            max(0, int(state.get("damageDealt", 0)))
            for state in states.values()
        )
        teams.append({
            "teamId": battle.get("teamId"),
            "teamName": team.get("name", "Team"),
            "memberCount": len(states),
            "damage": damage,
            "damageDealt": damage,
            "damagePercent": round(damage * 100 / maximum, 2),
            "questionsAnswered": int(battle.get("questionsAnswered", 0)),
            "correctAnswers": int(battle.get("correctAnswers", 0)),
            "defeated": defeated,
            "defeatedAt": battle.get("defeatedAt"),
            "winnerCount": len(winners),
            "winners": winners,
        })
    teams.sort(key=lambda item: (
        -item["damage"],
        -item["correctAnswers"],
        item["teamName"].casefold(),
        item["teamId"] or "",
    ))
    for index, entry in enumerate(teams, 1):
        entry["rank"] = index
    return jsonify({
        "weekKey": selected_week,
        "leaderboard": teams,
        "completedBattles": completed_battles,
    })
