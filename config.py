"""Application configuration.

Loads settings from environment variables (via a .env file in development).
Keeps secrets — notably ANTHROPIC_API_KEY — out of source control and out of
any browser-facing code.
"""
import os

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


class Config:
    """Base config shared by all environments."""

    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")

    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", f"sqlite:///{os.path.join(BASE_DIR, 'triage.db')}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Held server-side only. The frontend never sees this — it calls our
    # /api/* endpoints, which call Claude on its behalf.
    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
    ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5")


class TestConfig(Config):
    """Config used by the test suite — in-memory DB, testing flag on."""

    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
