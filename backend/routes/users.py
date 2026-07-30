"""HTTP routes for this feature area."""

from flask import Blueprint

from core import *  # noqa: F403 - shared legacy helpers during migration

bp = Blueprint("users", __name__)


@bp.get("/api/users/search")
def search_users_for_tagging():
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error

    identity, error = _authenticated_identity()
    if error:
        return error

    query = str(request.args.get("q") or "").strip().lower()
    limit = request.args.get("limit", default=20, type=int)
    limit = max(1, min(limit, 50))

    if len(query) < 2:
        return jsonify(
            {"error": "q must contain at least 2 characters"}
        ), 400

    matches: list[dict[str, Any]] = []
    for snapshot in FIRESTORE_DB.collection("users").stream():
        if snapshot.id == identity["uid"]:
            continue

        user = snapshot.to_dict() or {}
        display_name = str(user.get("displayName") or "").strip()
        email = _normalize_email(user.get("email"))
        searchable = f"{display_name} {email}".lower()

        if query in searchable:
            matches.append(
                {
                    "uid": snapshot.id,
                    "displayName": display_name or "User",
                    "email": email,
                    "profilePicLink": _profile_picture_link(user),
                }
            )

        if len(matches) >= limit:
            break

    return jsonify({"users": matches, "count": len(matches)})

