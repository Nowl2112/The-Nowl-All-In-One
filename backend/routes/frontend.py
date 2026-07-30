"""HTTP routes for this feature area."""

from flask import Blueprint

from core import *  # noqa: F403 - shared legacy helpers during migration

bp = Blueprint("frontend", __name__)


@bp.route("/", defaults={"path": ""})
@bp.route("/<path:path>")
def serve_frontend(path: str):
    if path.startswith("api/"):
        return jsonify({"error": "Not Found"}), 404

    requested_file = FRONTEND_DIST_DIR / path

    if (
        path
        and requested_file.exists()
        and requested_file.is_file()
    ):
        return send_from_directory(FRONTEND_DIST_DIR, path)

    index_file = FRONTEND_DIST_DIR / "index.html"
    if not index_file.exists():
        return jsonify(
            {
                "error": (
                    "Frontend build not found. Run the frontend development "
                    "server or build the frontend first."
                )
            }
        ), 404

    return send_from_directory(
        FRONTEND_DIST_DIR,
        "index.html",
    )

