"""Production-safe logging helpers: secret redaction and the startup banner.

The tracker already logs to a rotating file (config.settings.setup_logging). This adds, without changing
what it logs: a filter that scrubs anything that looks like a credential before it reaches any handler, and
one structured start-up block (version, platform, locations) so a support request starts from facts.
"""

import logging
import re

from desktop import paths, version

REDACTED = "***"

# Order matters: the connection-string pattern runs before the generic key=value one.
_PATTERNS = [
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=\-]{8,}"), r"\1 " + REDACTED),
    (re.compile(r"(?i)(postgres(?:ql)?://[^:/\s@]+:)[^@\s]+(@)"), r"\1" + REDACTED + r"\2"),
    (re.compile(r"(?i)\b((?:access_|refresh_|session_|api_)?token|password|passwd|secret|authorization|api[_-]?key"
                r"|postgres_password)\b(\s*[=:]\s*|[\"']\s*:\s*[\"'])([^\s,;&\"'}]+)"),
     lambda m: m.group(1) + m.group(2) + REDACTED),
]


def redact(text):
    """Return text with credential-looking values replaced. Safe on any input."""
    if not isinstance(text, str) or not text:
        return text
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class RedactingFilter(logging.Filter):
    """Scrubs the fully formatted message and any exception text before a handler sees it."""

    def filter(self, record):
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - a bad format string must not break logging
            return True
        scrubbed = redact(message)
        if scrubbed != message:
            record.msg, record.args = scrubbed, ()
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        return True


def install_redaction(root=None):
    """Attach the filter to every handler of the root logger (idempotent)."""
    root = root or logging.getLogger()
    for handler in root.handlers:
        if not any(isinstance(f, RedactingFilter) for f in handler.filters):
            handler.addFilter(RedactingFilter())


def log_startup(logger=None):
    """One block that says what is running and where it keeps things. No secrets by construction."""
    logger = logger or logging.getLogger("startup")
    logger.info("Application startup: %s", version.describe())
    info = version.as_dict()
    logger.info("Runtime: python %s, frozen=%s, commit=%s", info["python"], info["frozen"], info["commit"])
    for name, where in paths.all_locations().items():
        logger.info("Location %-9s %s", name, where)
