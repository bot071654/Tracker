"""Backend settings, all from environment variables (Railway variables in production).

DATABASE: the same POSTGRES_HOST / PORT / DATABASE / USER / PASSWORD / SSLMODE variables database/db.py has always
read. They exist only here (the API process); the desktop app never has them.
"""

import os


def _flag(name, default):
    return (os.environ.get(name, default) or "").strip().lower() in ("1", "true", "yes", "on")


def _int(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return int(default)


SESSION_DAYS = _int("SESSION_DAYS", "30")
LOGIN_FAILURE_LIMIT = _int("LOGIN_FAILURE_LIMIT", "5")
LOGIN_FAILURE_WINDOW_SECONDS = _int("LOGIN_FAILURE_WINDOW_SECONDS", "900")
MAX_HANDS_PAGE = _int("MAX_HANDS_PAGE", "20000")


def registration_enabled():
    """Self-service sign-up is OFF unless explicitly enabled; accounts are normally created with backend/manage.py."""
    return _flag("REGISTRATION_ENABLED", "0")


def registration_invite_code():
    return os.environ.get("REGISTRATION_INVITE_CODE", "").strip()


def legacy_anonymous_api_enabled():
    """The unauthenticated /api/health and /api/hands the Chrome extension demo uses. ON by default so nothing that
    works today stops; set LEGACY_ANONYMOUS_API=0 on a production deployment that only serves the desktop app."""
    return _flag("LEGACY_ANONYMOUS_API", "1")


def cors_origins():
    """Browser origins allowed to call the API. Unset = ["*"] (the old Chrome-extension demo needs it). "none" = no browser
    origin at all, which is right for a desktop-only deployment (a desktop app is not subject to CORS)."""
    raw = os.environ.get("CORS_ALLOW_ORIGINS", "").strip()
    if raw.lower() == "none":
        return []
    return [item.strip() for item in raw.split(",") if item.strip()] if raw else ["*"]


def auto_migrate():
    """Apply pending SQL migrations when the API starts. OFF unless AUTO_MIGRATE=1 (set it on the deployed service, so a deploy can
    never serve v1 against a database that has not been migrated; a failing migration stops the start instead)."""
    return _flag("AUTO_MIGRATE", "0")
