"""HTTP routes for this feature area."""

from flask import Blueprint

from core import *  # noqa: F403 - shared legacy helpers during migration

bp = Blueprint("auth", __name__)


@bp.post("/api/admin/users")
def admin_create_user():
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error

    if not ADMIN_REGISTRATION_KEY:
        return jsonify(
            {
                "error": (
                    "ADMIN_REGISTRATION_KEY is not configured"
                )
            }
        ), 503

    provided_key = request.headers.get(
        "X-Admin-Key",
        "",
    ).strip()

    if provided_key != ADMIN_REGISTRATION_KEY:
        return jsonify(
            {"error": "Invalid admin key"}
        ), 403

    if firebase_auth is None:
        return jsonify(
            {
                "error": (
                    "Firebase Admin authentication is unavailable"
                )
            }
        ), 503

    payload = request.get_json(silent=True) or {}
    email = _normalize_email(payload.get("email"))
    temporary_password = str(
        payload.get("temporaryPassword") or ""
    )
    display_name = (
        str(payload.get("displayName") or "").strip()
        or None
    )

    if not re.fullmatch(
        r"[^@\s]+@[^@\s]+\.[^@\s]+",
        email,
    ):
        return jsonify(
            {"error": "Enter a valid email address"}
        ), 400

    if len(temporary_password) < 8:
        return jsonify(
            {
                "error": (
                    "Temporary password must be at least "
                    "8 characters"
                )
            }
        ), 400

    created_user = None

    try:
        created_user = firebase_auth.create_user(
            email=email,
            password=temporary_password,
            display_name=display_name,
            email_verified=False,
        )

        firebase_auth.set_custom_user_claims(
            created_user.uid,
            {"mustResetPassword": True},
        )

        user_document = _default_user(
            created_user.uid,
            email,
            display_name,
        )
        user_document["mustResetPassword"] = True

        _write_document(
            "users",
            created_user.uid,
            user_document,
        )

        _write_document(
            "wordleUsers",
            created_user.uid,
            _default_wordle_stats(
                created_user.uid,
                email,
            ),
        )

    except firebase_auth.EmailAlreadyExistsError:
        return jsonify(
            {
                "error": (
                    "A Firebase user already exists "
                    "with this email"
                )
            }
        ), 409

    except Exception as error:
        current_app.logger.exception(
            "Manual user creation failed: %s",
            error,
        )

        if created_user is not None:
            try:
                firebase_auth.delete_user(
                    created_user.uid
                )
            except Exception:
                current_app.logger.exception(
                    "Could not roll back Firebase "
                    "Authentication user creation"
                )

        return jsonify(
            {"error": "Could not create user"}
        ), 500

    return jsonify(
        {
            "message": (
                "User created with a temporary password"
            ),
            "user": {
                "uid": created_user.uid,
                "email": email,
                "displayName": display_name,
                "mustResetPassword": True,
            },
        }
    ), 201



@bp.get("/api/auth/session")
def auth_session():
    identity, error = _authenticated_identity()
    if error:
        return error

    user = _get_or_create_user(
        identity["uid"],
        identity["email"],
        identity["name"],
    )

    return jsonify(
        {
            "uid": identity["uid"],
            "email": identity["email"],
            "displayName": user.get("displayName"),
            "profilePicLink": _profile_picture_link(user),
            "mustResetPassword": bool(
                identity["claims"].get(
                    "mustResetPassword",
                    False,
                )
            ),
        }
    )



@bp.post("/api/auth/complete-password-reset")
def complete_password_reset():
    firestore_error = _require_firestore()
    if firestore_error:
        return firestore_error

    identity, error = _authenticated_identity()
    if error:
        return error

    uid = identity["uid"]

    try:
        firebase_auth.set_custom_user_claims(
            uid,
            {"mustResetPassword": False},
        )

        _get_or_create_user(
            uid,
            identity["email"],
            identity["name"],
        )

        _write_document(
            "users",
            uid,
            {
                "mustResetPassword": False,
                "passwordResetAt": _now_iso(),
            },
            merge=True,
        )

    except Exception as error:
        current_app.logger.exception(
            "Could not complete password reset: %s",
            error,
        )
        return jsonify(
            {
                "error": (
                    "Could not complete password reset"
                )
            }
        ), 500

    return jsonify(
        {
            "message": (
                "Password reset requirement cleared"
            )
        }
    )

