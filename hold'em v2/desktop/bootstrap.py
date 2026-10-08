"""Start-up for the desktop build: log the banner, make sure the user is signed in, show the version.

app.main() calls startup(root) once before it builds the window and finish(app) once after. From source with no backend
configured both are close to no-ops (they only write the log banner), so developers see no change.

The sign-in logic lives in SignInController (no widgets) so it can be tested without a display; the dialogs are thin.
"""

import logging
import os
import subprocess
import sys
import threading

from desktop import appconfig, logsetup, paths, version
from desktop.api_client import ApiError, AuthError, OfflineError

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- controller (no widgets)

class SignInController:
    """Turns a button press into a sign-in and a readable result. Never raises; never logs the password."""

    def __init__(self, backend):
        self.backend = backend

    def configured(self):
        try:
            return bool(appconfig.api_url())
        except appconfig.ConfigError:
            return False

    def set_backend_address(self, url):
        """(ok, message). Saves a validated address for this user."""
        try:
            saved = appconfig.save_user_api_url(url)
        except appconfig.ConfigError as exc:
            return False, str(exc)
        if not saved:
            return False, "Enter the backend address."
        self.backend._client = None            # rebuild the client against the new address
        return True, "Backend address saved."

    def sign_in(self, email, password):
        email = (email or "").strip()
        if not email or not password:
            return False, "Enter your e-mail and password."
        try:
            self.backend.sign_in(email, password)
        except AuthError as exc:
            return False, str(exc) or "Incorrect e-mail or password."
        except OfflineError:
            return False, "Cannot reach the server. Check your internet connection, or work offline and sign in later."
        except ApiError as exc:
            return False, "Sign-in failed: %s" % exc
        except Exception as exc:  # noqa: BLE001 - configuration errors etc.; the dialog must stay usable
            return False, str(exc)
        return True, "Signed in as %s." % email

    def needs_sign_in(self):
        return appconfig.remote_mode() and self.configured() and not self.backend.signed_in()


def describe_account(backend):
    session = backend.store.load() if backend else None
    if session:
        return "Signed in as %s" % session.get("email", "?")
    return "Not signed in"


def diagnostics_text(backend=None):
    """What support needs, and nothing secret: version, platform, folders, backend address, sign-in state."""
    info = version.as_dict()
    lines = ["%s %s (build %s, commit %s)" % (info["name"], info["version"], info["build"], info["commit"]),
             "Platform: %s %s, Python %s%s" % (info["platform"], info["architecture"], info["python"],
                                                " (packaged)" if info["frozen"] else ""),
             "Mode: %s" % appconfig.describe()]
    if backend is not None:
        lines.append("Account: %s; %d hand(s) waiting to be sent" % (describe_account(backend), backend.pending_count()))
    lines.append("Folders:")
    lines += ["  %-9s %s" % (name, where) for name, where in paths.all_locations().items()]
    return "\n".join(lines)


def open_folder(path):
    """Show a folder in the system file browser (support: 'where are my logs?')."""
    os.makedirs(path, exist_ok=True)
    try:
        if sys.platform == "win32":
            os.startfile(path)                                     # noqa: S606 - a folder we created, nothing external
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except OSError as exc:
        logger.warning("Could not open %s: %s", path, exc)


# --------------------------------------------------------------------------- dialogs (thin)

def _sign_in_dialog(root, controller):
    """Modal sign-in. Returns True when signed in, False when the user chose to work offline."""
    import tkinter as tk
    from tkinter import ttk

    result = {"signed_in": False}
    top = tk.Toplevel(root)
    top.title("Sign in - %s" % version.APP_NAME)
    top.resizable(False, False)
    top.transient(root)
    frame = ttk.Frame(top, padding=16)
    frame.grid()
    ttk.Label(frame, text="Sign in to record your hands to the shared database.", wraplength=360).grid(
        row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))

    rows = 1
    address_var = tk.StringVar()
    if not controller.configured():
        ttk.Label(frame, text="Backend address (https://...)").grid(row=rows, column=0, sticky="w")
        ttk.Entry(frame, textvariable=address_var, width=36).grid(row=rows, column=1, pady=2)
        rows += 1
    email_var, password_var, status_var = tk.StringVar(), tk.StringVar(), tk.StringVar()
    ttk.Label(frame, text="E-mail").grid(row=rows, column=0, sticky="w")
    email_entry = ttk.Entry(frame, textvariable=email_var, width=36)
    email_entry.grid(row=rows, column=1, pady=2)
    ttk.Label(frame, text="Password").grid(row=rows + 1, column=0, sticky="w")
    ttk.Entry(frame, textvariable=password_var, show="*", width=36).grid(row=rows + 1, column=1, pady=2)
    ttk.Label(frame, textvariable=status_var, foreground="#b00020", wraplength=360).grid(
        row=rows + 2, column=0, columnspan=2, sticky="w", pady=(8, 0))
    buttons = ttk.Frame(frame)
    buttons.grid(row=rows + 3, column=0, columnspan=2, sticky="e", pady=(12, 0))

    def finish_attempt(ok, message):
        sign_in_button.state(["!disabled"])
        password_var.set("")
        if ok:
            result["signed_in"] = True
            top.destroy()
        else:
            status_var.set(message)

    def attempt(*_):
        if address_var.get().strip():
            ok, message = controller.set_backend_address(address_var.get())
            if not ok:
                status_var.set(message)
                return
        status_var.set("Signing in...")
        sign_in_button.state(["disabled"])
        email, password = email_var.get(), password_var.get()
        outcome = {}

        def work():
            outcome["value"] = controller.sign_in(email, password)

        thread = threading.Thread(target=work, daemon=True)
        thread.start()

        def poll():
            if thread.is_alive():
                top.after(100, poll)
            else:
                finish_attempt(*outcome["value"])
        poll()

    sign_in_button = ttk.Button(buttons, text="Sign in", command=attempt)
    sign_in_button.grid(row=0, column=0, padx=4)
    ttk.Button(buttons, text="Work offline", command=top.destroy).grid(row=0, column=1, padx=4)
    top.bind("<Return>", attempt)
    email_entry.focus_set()
    top.grab_set()
    root.wait_window(top)
    return result["signed_in"]


def show_about(root, backend):
    import tkinter as tk
    from tkinter import ttk

    top = tk.Toplevel(root)
    top.title("About - %s" % version.APP_NAME)
    frame = ttk.Frame(top, padding=14)
    frame.grid()
    text = tk.Text(frame, width=86, height=16, wrap="none")
    text.insert("1.0", diagnostics_text(backend))
    text.configure(state="disabled")
    text.grid(row=0, column=0, columnspan=4)
    bar = ttk.Frame(frame)
    bar.grid(row=1, column=0, columnspan=4, sticky="e", pady=(10, 0))

    def copy():
        root.clipboard_clear()
        root.clipboard_append(diagnostics_text(backend))

    def sign_out():
        backend.sign_out()
        top.destroy()

    ttk.Button(bar, text="Copy details", command=copy).grid(row=0, column=0, padx=3)
    ttk.Button(bar, text="Open logs folder", command=lambda: open_folder(paths.log_dir())).grid(row=0, column=1, padx=3)
    if backend is not None:
        ttk.Button(bar, text="Sign out", command=sign_out).grid(row=0, column=2, padx=3)
    ttk.Button(bar, text="Close", command=top.destroy).grid(row=0, column=3, padx=3)


# --------------------------------------------------------------------------- entry points used by app.main()

def startup(root):
    """Before the main window exists: banner in the log, then sign in if this build needs it."""
    logsetup.log_startup(logging.getLogger("startup"))
    logger.info("Mode: %s", appconfig.describe())
    if not appconfig.remote_mode():
        return
    try:
        from desktop import backend as remote
        controller = SignInController(remote.get())
        if controller.needs_sign_in() or not controller.configured():
            _sign_in_dialog(root, controller)
    except Exception:  # noqa: BLE001 - a broken dialog must not stop the tracker from opening
        logger.exception("Sign-in step failed; continuing without a session")


def finish(app, root):
    """After the window exists: put the version and account state in the message line, bind F1 for details."""
    try:
        backend = None
        if appconfig.remote_mode():
            from desktop import backend as remote
            backend = remote.get()
        line = "%s  |  %s" % (version.describe(), describe_account(backend) if backend else "direct database (development)")
        app.set_message(line)
        root.bind("<F1>", lambda _event: show_about(root, backend))
    except Exception:  # noqa: BLE001
        logger.exception("Could not show the version line")
