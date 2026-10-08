"""HTTPS client for the backend API. Standard library only: nothing extra to bundle.

Every operation is an explicit, named call with a fixed shape; nothing here can upload an arbitrary file,
a screenshot, or anything the caller did not pass as a field of a hand record or a sign-in. Requests carry a
short timeout, retry only what is safe to repeat, and never log a header or a body.
"""

import json
import logging
import socket
import ssl
import time
import urllib.error
import urllib.request

from desktop import appconfig, version

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 10.0
MAX_BODY_BYTES = 64 * 1024
RETRY_STATUSES = (429, 502, 503, 504)
MAX_RETRY_AFTER = 5.0


class ApiError(RuntimeError):
    """A call to the backend failed. `status` is the HTTP status or None; `retriable` is advice, not a promise."""

    def __init__(self, message, status=None, code=None, retriable=False):
        super().__init__(message)
        self.status, self.code, self.retriable = status, code, retriable


class OfflineError(ApiError):
    """The backend could not be reached at all (no network, DNS, TLS, timeout)."""

    def __init__(self, message):
        super().__init__(message, status=None, code="offline", retriable=True)


class AuthError(ApiError):
    """Not signed in, or the session is no longer valid."""

    def __init__(self, message, status=401, code="unauthorized"):
        super().__init__(message, status=status, code=code, retriable=False)


def _ssl_context():
    context = ssl.create_default_context()
    try:                                    # python.org builds on macOS ship no CA bundle: use certifi when present
        import certifi
        context.load_verify_locations(cafile=certifi.where())
    except Exception:  # noqa: BLE001 - certifi is optional
        pass
    return context


def _user_agent():
    return "PokerHandTracker/%s (%s %s)" % (version.VERSION, version.platform_name(), version.architecture())


class ApiClient:
    """Thin JSON client. `token` and `device_id` may be strings or zero-argument callables."""

    def __init__(self, base_url, token=None, device_id=None, timeout=DEFAULT_TIMEOUT, retries=2,
                 backoff=0.5, sleep=time.sleep, opener=None):
        self.base_url = appconfig.normalize_base_url(base_url)
        if not self.base_url:
            raise appconfig.ConfigError("No backend address is configured.")
        self._token, self._device_id = token, device_id
        self.timeout, self.retries, self.backoff, self._sleep = timeout, retries, backoff, sleep
        self._opener = opener or urllib.request.build_opener(urllib.request.HTTPSHandler(context=_ssl_context()))

    @staticmethod
    def _value(item):
        return item() if callable(item) else item

    def request(self, method, path, body=None, auth=True, idempotent=None):
        """Send one request and return the decoded JSON (a dict). Raises ApiError subclasses."""
        method = method.upper()
        if idempotent is None:
            idempotent = method in ("GET", "HEAD", "DELETE")
        data = None
        headers = {"Accept": "application/json", "User-Agent": _user_agent()}
        if body is not None:
            data = json.dumps(body, separators=(",", ":"), default=str).encode("utf-8")
            if len(data) > MAX_BODY_BYTES:
                raise ApiError("Request is too large (%d bytes)." % len(data), code="too_large")
            headers["Content-Type"] = "application/json"
        if auth:
            token = self._value(self._token)
            if not token:
                raise AuthError("You are not signed in.", status=None, code="not_signed_in")
            headers["Authorization"] = "Bearer " + token
        device_id = self._value(self._device_id)
        if device_id:
            headers["X-Device-Id"] = str(device_id)

        url = self.base_url + path
        attempts = (self.retries if idempotent else 0) + 1
        last = None
        for attempt in range(attempts):
            try:
                return self._send(urllib.request.Request(url, data=data, headers=headers, method=method))
            except ApiError as exc:
                last = exc
                if not exc.retriable or attempt == attempts - 1:
                    break
                delay = min(self.backoff * (2 ** attempt), MAX_RETRY_AFTER)
                retry_after = getattr(exc, "retry_after", None)
                if retry_after:
                    delay = min(float(retry_after), MAX_RETRY_AFTER)
                logger.info("%s %s failed (%s); retrying in %.1fs", method, path, exc.code or exc.status, delay)
                self._sleep(delay)
        raise last

    def _send(self, request):
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                return self._decode(response.read(), response.status)
        except urllib.error.HTTPError as exc:
            payload = {}
            try:
                payload = json.loads(exc.read().decode("utf-8") or "{}")
            except Exception:  # noqa: BLE001
                pass
            detail = payload.get("detail") if isinstance(payload, dict) else None
            message = detail if isinstance(detail, str) else "The server returned HTTP %d." % exc.code
            code = payload.get("code") if isinstance(payload, dict) else None
            if exc.code in (401, 403):
                raise AuthError(message, status=exc.code, code=code or ("unauthorized" if exc.code == 401 else "forbidden")) from None
            error = ApiError(message, status=exc.code, code=code, retriable=exc.code in RETRY_STATUSES)
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            if retry_after and str(retry_after).replace(".", "", 1).isdigit():
                error.retry_after = float(retry_after)
            raise error from None
        except (urllib.error.URLError, socket.timeout, TimeoutError, ssl.SSLError, ConnectionError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise OfflineError("Cannot reach the backend (%s)." % (reason,)) from None

    @staticmethod
    def _decode(raw, status):
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except ValueError:
            raise ApiError("The server sent an unreadable response.", status=status, code="bad_response") from None
        return data if isinstance(data, dict) else {"data": data}

    # ---- the explicit API operations (nothing else is ever sent) -------------------------------

    def health(self):
        return self.request("GET", "/api/v1/health", auth=False)

    def login(self, email, password):
        return self.request("POST", "/api/v1/auth/login", {"email": email, "password": password}, auth=False)

    def logout(self):
        return self.request("POST", "/api/v1/auth/logout", {})

    def me(self):
        return self.request("GET", "/api/v1/me")

    def register_device(self, name, platform_name, architecture, app_version):
        return self.request("POST", "/api/v1/devices", {
            "name": name, "platform": platform_name, "architecture": architecture, "app_version": app_version})

    def submit_hand(self, record):
        """Idempotent on the server (the hand fingerprint is unique), so it is safe to retry."""
        return self.request("POST", "/api/v1/hands", record, idempotent=True)

    def list_hands(self, limit=None):
        path = "/api/v1/hands" + ("?limit=%d" % int(limit) if limit else "")
        return self.request("GET", path)

    def stats(self):
        return self.request("GET", "/api/v1/stats")
