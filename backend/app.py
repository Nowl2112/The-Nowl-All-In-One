import os

from flask import Flask

from config import Config
from extensions import init_extensions


def create_app(config_object=Config):
    app = Flask(__name__)
    app.config.from_object(config_object)
    init_extensions(app)

    # Firebase must be initialized before importing route modules because the
    # route functions share the initialized Firestore references.
    with app.app_context():
        from services.firebase import initialize_firebase

        initialize_firebase()

    from routes.auth import bp as auth_bp
    from routes.calendar import bp as calendar_bp
    from routes.frontend import bp as frontend_bp
    from routes.health import bp as health_bp
    from routes.news import bp as news_bp
    from routes.riddles import bp as riddles_bp
    from routes.task_boards import bp as task_boards_bp
    from routes.telegram import bp as telegram_bp
    from routes.trivia_quest import bp as trivia_quest_bp
    from routes.users import bp as users_bp
    from routes.wordle import bp as wordle_bp

    # Register the frontend catch-all last.
    for blueprint in (
        health_bp,
        auth_bp,
        users_bp,
        calendar_bp,
        news_bp,
        riddles_bp,
        task_boards_bp,
        telegram_bp,
        wordle_bp,
        trivia_quest_bp,
        frontend_bp,
    ):
        app.register_blueprint(blueprint)

    return app


app = create_app()


if __name__ == "__main__":
    app.run(
        debug=os.getenv("FLASK_DEBUG", "false").lower() == "true",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5000")),
    )
