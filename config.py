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

    # ElevenLabs Agents (voice check-in calls). If any of these are missing,
    # the Call button falls back to a simulated call so the whole workflow
    # can be demoed without credentials.
    ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY")
    ELEVENLABS_AGENT_ID = os.environ.get("ELEVENLABS_AGENT_ID")
    ELEVENLABS_PHONE_NUMBER_ID = os.environ.get("ELEVENLABS_PHONE_NUMBER_ID")

    # Seconds between simulated transcript lines (0 = run synchronously,
    # used by the test suite).
    SIM_CALL_DELAY = float(os.environ.get("SIM_CALL_DELAY", "1.2"))


class TestConfig(Config):
    """Config used by the test suite — in-memory DB, testing flag on."""

    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SIM_CALL_DELAY = 0  # run simulated calls synchronously in tests
    # Never hit the real ElevenLabs API from tests.
    ELEVENLABS_API_KEY = None
    ELEVENLABS_AGENT_ID = None
    ELEVENLABS_PHONE_NUMBER_ID = None
