"""Flask application factory + DB setup.

Run locally:
    pip install -r requirements.txt
    python seed_data.py          # create + populate the DB
    flask --app app run          # or: python app.py

The factory pattern lets the test suite build an app bound to an in-memory DB
without touching the dev database.
"""
from flask import Flask, jsonify

from config import Config
from extensions import db


def create_app(config_object=Config):
    app = Flask(__name__)
    app.config.from_object(config_object)

    db.init_app(app)

    # Import models so SQLAlchemy is aware of them before create_all().
    from models import Patient, Encounter, CheckIn, TriageResult  # noqa: F401

    # Register blueprints.
    from routes.tool import bp as tool_bp

    app.register_blueprint(tool_bp)

    @app.get("/")
    def index():
        """Root: list the available endpoints so hitting / isn't a bare 404."""
        return jsonify(
            service="post-discharge-triage-agent backend",
            endpoints=[
                "GET  /health",
                "GET  /api/patients",
                "GET  /api/patients/<slug>",
                "POST /api/triage",
                "POST /api/draft-note",
            ],
        )

    @app.get("/health")
    def health():
        return jsonify(status="ok")

    @app.cli.command("init-db")
    def init_db():
        """flask --app app init-db  — create all tables."""
        db.create_all()
        print("Database tables created.")

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=True, port=5001)
