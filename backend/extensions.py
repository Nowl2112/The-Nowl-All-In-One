"""Flask extensions initialized by the application factory."""

from flask_cors import CORS


cors = CORS()


def init_extensions(app):
    cors.init_app(
        app,
        resources={
            r"/api/*": {
                "origins": app.config["FRONTEND_ORIGINS"],
            }
        },
        supports_credentials=False,
    )

