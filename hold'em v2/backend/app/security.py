"""Password hashing, session tokens and the sign-in rate limit. Standard library only.

* Passwords: scrypt with a per-user random salt, stored as  scrypt$n$r$p$salt$hash  (base64). The
  password itself is never stored or logged.
* Tokens: 256 random bits (secrets.token_urlsafe). Only the SHA-256 of a token is stored, so a database leak does
  not hand out working sessions.
* Rate limit: a failed sign-in counter per (e-mail, client address). In memory, per process: it slows guessing on a
  single instance; with several replicas put a shared limiter (or the platform's) in front.
"""

import base64
import collections
import hashlib
import hmac
import secrets
import threading
import time

SCRYPT_N, SCRYPT_R, SCRYPT_P, SALT_BYTES, KEY_BYTES = 2 ** 14, 8, 1, 16, 32
MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 256          # bounds the work an attacker can force with a huge password


def _b64(raw):
    return base64.b64encode(raw).decode("ascii")


def hash_password(password):
    if not isinstance(password, str) or not (MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH):
        raise ValueError("A password must be %d to %d characters." % (MIN_PASSWORD_LENGTH, MAX_PASSWORD_LENGTH))
    salt = secrets.token_bytes(SALT_BYTES)
    key = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=KEY_BYTES)
    return "scrypt$%d$%d$%d$%s$%s" % (SCRYPT_N, SCRYPT_R, SCRYPT_P, _b64(salt), _b64(key))


def verify_password(password, stored):
    """True when the password matches the stored hash. Constant-time comparison; False on any malformed input."""
    try:
        scheme, n, r, p, salt, expected = stored.split("$")
        if scheme != "scrypt" or not isinstance(password, str) or len(password) > MAX_PASSWORD_LENGTH:
            return False
        key = hashlib.scrypt(password.encode("utf-8"), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                             dklen=len(base64.b64decode(expected)))
        return hmac.compare_digest(key, base64.b64decode(expected))
    except (ValueError, TypeError, AttributeError):
        return False


# A real hash of a throwaway password: verifying against it costs the same as a real check, so an unknown e-mail
# and a wrong password take the same time.
DUMMY_HASH = hash_password("not-a-real-password-" + secrets.token_hex(4))


def new_token():
    return secrets.token_urlsafe(32)


def token_digest(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class LoginLimiter:
    """At most `limit` failures per `window` seconds for one (e-mail, address)."""

    def __init__(self, limit=5, window=900.0, clock=time.monotonic):
        self.limit, self.window, self._clock = limit, window, clock
        self._failures = collections.defaultdict(collections.deque)
        self._lock = threading.Lock()

    def _prune(self, key, now):
        queue = self._failures[key]
        while queue and now - queue[0] > self.window:
            queue.popleft()
        return queue

    def retry_after(self, key):
        """Seconds until another attempt is allowed, or 0."""
        with self._lock:
            now = self._clock()
            queue = self._prune(key, now)
            if len(queue) >= self.limit:
                return max(1, int(self.window - (now - queue[0])) + 1)
            return 0

    def failed(self, key):
        with self._lock:
            now = self._clock()
            self._prune(key, now).append(now)

    def succeeded(self, key):
        with self._lock:
            self._failures.pop(key, None)
