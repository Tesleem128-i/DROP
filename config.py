import os
from datetime import timedelta

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


def _sqlite_safe_uri(uri):
    """Normalize a sqlite:/// URI to forward slashes.

    On Windows, SQLAlchemy/sqlite3 fails with "unable to open database file"
    if the path portion contains backslashes. This guards against that no
    matter where the URI came from (a .env DATABASE_URL, an OS env var, or
    our own default), since a user-supplied value always overrides the
    default and could reintroduce the same bug.
    """
    if uri.startswith("sqlite:///"):
        prefix = "sqlite:///"
        path_part = uri[len(prefix):].replace("\\", "/")
        return prefix + path_part
    return uri


class Config:
    """Central configuration for the DROP platform."""

    # --- Core Flask ---
    SECRET_KEY = os.environ.get("SECRET_KEY", "drop-dev-secret-change-me")

    _default_db_path = os.path.join(BASE_DIR, "instance", "drop.db").replace("\\", "/")
    SQLALCHEMY_DATABASE_URI = _sqlite_safe_uri(
        os.environ.get("DATABASE_URL", f"sqlite:///{_default_db_path}")
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # --- Uploads ---
    UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB
    ALLOWED_EXTENSIONS = {"pdf", "docx", "txt", "md"}

    # --- Sessions ---
    PERMANENT_SESSION_LIFETIME = timedelta(days=14)

    # --- AI configuration (Groq, OpenAI-compatible endpoint) ---
    # DROP calls whatever OpenAI-compatible Chat Completions endpoint is
    # configured here for every AI feature: course generation, lesson
    # authoring, the AI tutor, auto-grading, and analytics insights.
    # Currently pointed at Groq (api.groq.com) — swap these three values to
    # use xAI, OpenAI, or any other OpenAI-compatible provider instead.
    XAI_API_KEY = os.environ.get("XAI_API_KEY", "")
    XAI_BASE_URL = os.environ.get("XAI_BASE_URL", "https://api.groq.com/openai/v1")
    GROK_MODEL = os.environ.get("GROK_MODEL", "openai/gpt-oss-120b")

    # If no API key is configured, DROP falls back to structured mock
    # content so the whole product still works end-to-end in a demo.
    AI_MOCK_FALLBACK = os.environ.get("AI_MOCK_FALLBACK", "1") == "1"