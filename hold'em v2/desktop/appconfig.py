"""Where the backend is, resolved without touching source code.

The API base URL comes from, in order:

  1. the POKER_TRACKER_API_URL environment variable      (development / staging / support)
  2. "api_url" in <config dir>/app.json                  (a per-user setting)
  3. "api_url" in desktop/api_defaults.json              (written into a release build by the build script)

Nothing else is configurable here and nothing secret lives in any of these: the URL is public, the
credentials are the user's own sign-in, and the database is reachable only from the backend.

Remote mode (every hand goes to the backend over HTTPS) is on when a URL is configured, and always in a
packaged build. From source with no URL the app keeps its original direct-database behaviour, for
developers only.
"""

import json
import logging
import os
import sys
from urllib.parse import urlparse

from desktop import paths

logger = logging.getLogger(__name__)

API_URL_ENV = "POKER_TRACKER_API_URL"
APP_JSON = "app.json"
LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1")


class ConfigError(ValueError):
    """The configured backend address is not acceptable (for example plain http to a remote host)."""


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _bundled_defaults_path():
    return os.path.join(paths.resource_root(), "desktop", "api_defaults.json")


def user_config_path():
    return os.path.join(paths.config_dir(), APP_JSON)


def normalize_base_url(url):
    """Validate and tidy a backend address. HTTPS only, except to this machine (development)."""
    url = (url or "").strip()
    if not url:
        return ""
    parsed = urlparse(url)
    if parsed.scheme not in ("https", "http") or not parsed.hostname:
        raise ConfigError("The backend address %r is not a valid http(s) URL." % url)
    if parsed.scheme == "http" and parsed.hostname not in LOOPBACK_HOSTS:
        raise ConfigError(
            "Refusing to use plain http for %s: hands and sign-in must travel over HTTPS. "
            "(http is only accepted for localhost during development.)" % parsed.hostname)
    if parsed.username or parsed.password:
        raise ConfigError("Do not put a user name or password in the backend address.")
    return url.rstrip("/")


def api_url():
    """The configured, validated base URL, or '' when none is configured."""
    for source in (os.environ.get(API_URL_ENV),
                   _read_json(user_config_path()).get("api_url"),
                   _read_json(_bundled_defaults_path()).get("api_url")):
        if source and str(source).strip():
            return normalize_base_url(str(source))
    return ""


def is_backend_process():
    """The FastAPI server itself must always talk to PostgreSQL directly, never to another API."""
    return os.environ.get("POKER_TRACKER_BACKEND") == "1"


def remote_mode():
    """True when hands go through the HTTPS API. Always true in a packaged build."""
    if is_backend_process():
        return False
    if paths.is_frozen():
        return True
    return bool(api_url())


def save_user_api_url(url):
    """Persist a per-user backend address (validated)."""
    cleaned = normalize_base_url(url)
    path = user_config_path()
    data = _read_json(path)
    data["api_url"] = cleaned
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
    return cleaned


def describe():
    try:
        url = api_url()
    except ConfigError as exc:
        return "invalid backend address (%s)" % exc
    mode = "remote (HTTPS API)" if remote_mode() else "direct database (development)"
    return "%s, backend %s" % (mode, url or "not configured")
