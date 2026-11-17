"""Application configuration.

Every setting is read from environment variables (or the .env file).
Copy .env.example to .env and fill in your values.
"""
import os
import secrets
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _env(name, default=None):
    """Return a stripped env var, or `default` when missing/blank."""
    value = os.getenv(name)
    if value is None:
        return default
    value = value.strip()
    return value if value else default


def _env_bool(name, default=False):
    value = _env(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _env_int(name, default):
    try:
        return int(_env(name, default))
    except (TypeError, ValueError):
        return default


def _load_secret_key():
    """Use SECRET_KEY if set, otherwise create one and keep it in instance/.

    Keeping it on disk means users stay logged in after a restart.
    """
    key = _env("SECRET_KEY")
    if key:
        return key
    path = BASE_DIR / "instance" / "secret.key"
    try:
        if path.exists():
            saved = path.read_text(encoding="utf-8").strip()
            if saved:
                return saved
        path.parent.mkdir(parents=True, exist_ok=True)
        key = secrets.token_hex(32)
        path.write_text(key, encoding="utf-8")
        return key
    except OSError:
        return secrets.token_hex(32)


# Speech-to-text + field-extraction providers. Both speak the OpenAI API format, so
# the same code works for both. Groq has a free plan (no payment needed to start).
PROVIDERS = {
    "openai": {"name": "OpenAI", "base_url": "https://api.openai.com/v1",
               "transcribe": "whisper-1", "chat": "gpt-4o-mini"},
    "groq": {"name": "Groq", "base_url": "https://api.groq.com/openai/v1",
             "transcribe": "whisper-large-v3-turbo", "chat": "openai/gpt-oss-120b"},
}


def pick_provider(provider, openai_key, groq_key):
    """AI_PROVIDER wins. Otherwise Groq is used when GROQ_API_KEY is set (even if an old
    OpenAI key is still in .env) or when OPENAI_API_KEY holds a Groq key (gsk_...)."""
    provider = (provider or "").strip().lower()
    if provider in PROVIDERS:
        return provider
    if groq_key or (openai_key or "").startswith("gsk_"):
        return "groq"
    return "openai"


class Config:
    # ---- Flask ---------------------------------------------------------
    SECRET_KEY = _load_secret_key()
    DEBUG = _env_bool("FLASK_DEBUG", False)
    HOST = _env("HOST", "127.0.0.1")
    PORT = _env_int("PORT", 5000)
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _env_bool("SESSION_COOKIE_SECURE", False)
    PERMANENT_SESSION_LIFETIME = timedelta(days=7)
    MAX_CONTENT_LENGTH = _env_int("MAX_UPLOAD_MB", 25) * 1024 * 1024  # Whisper limit is 25 MB

    # ---- Database ------------------------------------------------------
    # DB_BACKEND = "mysql" or "sqlite". When not set: MySQL if MYSQL_DATABASE
    # is filled in, otherwise SQLite (no setup needed).
    MYSQL_HOST = _env("MYSQL_HOST", "localhost")
    MYSQL_PORT = _env_int("MYSQL_PORT", 3306)
    MYSQL_USER = _env("MYSQL_USER", "root")
    MYSQL_PASSWORD = _env("MYSQL_PASSWORD", "")
    MYSQL_DATABASE = _env("MYSQL_DATABASE")
    DB_BACKEND = (_env("DB_BACKEND") or ("mysql" if MYSQL_DATABASE else "sqlite")).lower()
    SQLITE_PATH = _env("SQLITE_PATH", str(BASE_DIR / "instance" / "voice_crm.db"))

    # ---- AI: speech-to-text + field extraction ---------------------------
    # AI_PROVIDER = "openai" (paid) or "groq" (free plan). Blank = chosen from your key.
    # The old OPENAI_* names keep working and still override the provider defaults.
    AI_PROVIDER = pick_provider(_env("AI_PROVIDER"), _env("OPENAI_API_KEY"), _env("GROQ_API_KEY"))
    AI_PROVIDER_NAME = PROVIDERS[AI_PROVIDER]["name"]
    OPENAI_API_KEY = ((_env("GROQ_API_KEY") or _env("OPENAI_API_KEY")) if AI_PROVIDER == "groq"
                      else _env("OPENAI_API_KEY"))
    OPENAI_BASE_URL = _env("OPENAI_BASE_URL", PROVIDERS[AI_PROVIDER]["base_url"]).rstrip("/")
    OPENAI_TRANSCRIBE_MODEL = _env("OPENAI_TRANSCRIBE_MODEL", PROVIDERS[AI_PROVIDER]["transcribe"])
    OPENAI_CHAT_MODEL = _env("OPENAI_CHAT_MODEL", PROVIDERS[AI_PROVIDER]["chat"])
    TRANSCRIBE_LANGUAGE = _env("TRANSCRIBE_LANGUAGE")  # e.g. "en" or "hi"; blank = auto-detect
    OPENAI_TIMEOUT = _env_int("OPENAI_TIMEOUT", 120)

    # ---- Email reminders (SMTP) ----------------------------------------
    SMTP_HOST = _env("SMTP_HOST")
    SMTP_PORT = _env_int("SMTP_PORT", 587)
    SMTP_USER = _env("SMTP_USER")
    SMTP_PASSWORD = _env("SMTP_PASSWORD")
    SMTP_SECURITY = (_env("SMTP_SECURITY", "starttls") or "starttls").lower()  # starttls | ssl | none
    SMTP_FROM = _env("SMTP_FROM") or SMTP_USER
    REMINDER_DAYS_AHEAD = _env_int("REMINDER_DAYS_AHEAD", 1)

    # ---- App behaviour ---------------------------------------------------
    UPLOAD_DIR = _env("UPLOAD_DIR", str(BASE_DIR / "uploads"))
    ALLOW_REGISTRATION = _env_bool("ALLOW_REGISTRATION", True)
    # Secret needed to create an Administrator account on the sign-up page (the very first
    # account is always the administrator). Blank = nobody can sign up as administrator.
    ADMIN_SIGNUP_CODE = _env("ADMIN_SIGNUP_CODE")
    PER_PAGE = _env_int("PER_PAGE", 15)
    SCHEMA_DIR = str(BASE_DIR / "database")
