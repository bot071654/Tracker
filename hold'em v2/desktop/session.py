"""Where the signed-in session lives on this computer.

  Windows   encrypted with DPAPI (bound to this Windows user account) in <state>/session.bin
  macOS     the login Keychain (via the system `security` tool); if that fails, a 0600 file
  other     a 0600 file (development only)

Only the revocable session token is stored, never the password. The device id (issued by the backend, not
derived from hardware) is not a secret and sits in <state>/device.json.
"""

import json
import logging
import os
import subprocess
import sys

from desktop import paths
from desktop.version import APP_ID

logger = logging.getLogger(__name__)

SESSION_FILE = "session.bin"
DEVICE_FILE = "device.json"
KEYCHAIN_SERVICE = APP_ID
KEYCHAIN_ACCOUNT = "session"


# ------------------------------------------------------------------------------ backends

class FileBackend:
    """A plain file readable only by its owner."""

    def __init__(self, path):
        self.path = path

    def read(self):
        try:
            with open(self.path, "rb") as handle:
                return handle.read()
        except OSError:
            return None

    def write(self, data):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def delete(self):
        try:
            os.remove(self.path)
        except FileNotFoundError:
            pass


class DpapiBackend(FileBackend):
    """File contents encrypted with the Windows Data Protection API for the current user."""

    @staticmethod
    def _blob(data):
        import ctypes
        from ctypes import wintypes

        class Blob(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

        buffer = ctypes.create_string_buffer(data, len(data))
        return Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer, Blob

    @classmethod
    def protect(cls, data):
        import ctypes
        blob_in, keep, Blob = cls._blob(data)
        blob_out = Blob()
        if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(blob_in), "PokerHandTracker", None, None, None, 0,
                                                      ctypes.byref(blob_out)):
            raise OSError("CryptProtectData failed (%d)" % ctypes.GetLastError())
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(blob_out.pbData)
            del keep

    @classmethod
    def unprotect(cls, data):
        import ctypes
        blob_in, keep, Blob = cls._blob(data)
        blob_out = Blob()
        if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(blob_in), None, None, None, None, 0,
                                                        ctypes.byref(blob_out)):
            raise OSError("CryptUnprotectData failed (%d)" % ctypes.GetLastError())
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(blob_out.pbData)
            del keep

    def read(self):
        raw = super().read()
        if raw is None:
            return None
        try:
            return self.unprotect(raw)
        except OSError as exc:
            logger.warning("The saved sign-in could not be decrypted (%s); signing in again is needed.", exc)
            return None

    def write(self, data):
        super().write(self.protect(data))


class KeychainBackend:
    """macOS login Keychain through /usr/bin/security. Falls back to a 0600 file if the tool refuses."""

    TOOL = "/usr/bin/security"

    def __init__(self, fallback_path):
        self._fallback = FileBackend(fallback_path)

    def _run(self, *args):
        return subprocess.run([self.TOOL, *args], capture_output=True, text=True, timeout=15)

    def read(self):
        try:
            result = self._run("find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT, "-w")
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip().encode("utf-8")
        except (OSError, subprocess.SubprocessError) as exc:
            logger.info("Keychain unavailable (%s); using the protected file.", exc)
        return self._fallback.read()

    def write(self, data):
        try:
            result = self._run("add-generic-password", "-U", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT,
                               "-w", data.decode("utf-8"))
            if result.returncode == 0:
                self._fallback.delete()
                return
            logger.info("Keychain refused the item (%s); using the protected file.", result.returncode)
        except (OSError, subprocess.SubprocessError) as exc:
            logger.info("Keychain unavailable (%s); using the protected file.", exc)
        self._fallback.write(data)

    def delete(self):
        try:
            self._run("delete-generic-password", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT)
        except (OSError, subprocess.SubprocessError):
            pass
        self._fallback.delete()


def default_backend(state_dir=None):
    folder = state_dir or paths.state_dir()
    path = os.path.join(folder, SESSION_FILE)
    if sys.platform == "win32":
        return DpapiBackend(path)
    if sys.platform == "darwin":
        return KeychainBackend(path)
    return FileBackend(path)


# ------------------------------------------------------------------------------ store

class SessionStore:
    """The signed-in session (token, expiry, e-mail) and this installation's backend-issued device id."""

    def __init__(self, backend=None, state_dir=None):
        self.state_dir = state_dir or paths.state_dir()
        self._backend = backend or default_backend(self.state_dir)

    # session -------------------------------------------------------------------
    def load(self):
        raw = self._backend.read()
        if not raw:
            return None
        try:
            data = json.loads(raw.decode("utf-8"))
        except ValueError:
            return None
        return data if isinstance(data, dict) and data.get("access_token") else None

    def save(self, session):
        keep = {key: session[key] for key in ("access_token", "expires_at", "email") if key in session}
        self._backend.write(json.dumps(keep).encode("utf-8"))

    def clear(self):
        self._backend.delete()

    # device id ------------------------------------------------------------------
    def _device_path(self):
        return os.path.join(self.state_dir, DEVICE_FILE)

    def device_id(self):
        try:
            with open(self._device_path(), "r", encoding="utf-8") as handle:
                value = json.load(handle).get("device_id")
            return str(value) if value else None
        except (OSError, ValueError, AttributeError):
            return None

    def save_device_id(self, value):
        os.makedirs(self.state_dir, exist_ok=True)
        with open(self._device_path(), "w", encoding="utf-8") as handle:
            json.dump({"device_id": str(value)}, handle)

    def forget_device(self):
        try:
            os.remove(self._device_path())
        except FileNotFoundError:
            pass
