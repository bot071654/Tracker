"""Account management for the backend. Run from the project root against the server's database:

    python -m backend.manage create-user  alice@example.com        # asks for the password
    python -m backend.manage set-password alice@example.com
    python -m backend.manage disable-user alice@example.com        # also ends all of that user's sign-ins
    python -m backend.manage enable-user  alice@example.com
    python -m backend.manage revoke-sessions alice@example.com
    python -m backend.manage list-users

The password is read from the prompt (or from the NEW_USER_PASSWORD environment variable for scripted use); it is
never taken from the command line, so it does not end up in shell history or a process list.
"""

import getpass
import os
import re
import sys

from backend.app import security
from database import db

EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]+$")


def _password(confirm=True):
    value = os.environ.get("NEW_USER_PASSWORD")
    if value:
        return value
    first = getpass.getpass("Password (%d+ characters): " % security.MIN_PASSWORD_LENGTH)
    if confirm and getpass.getpass("Again: ") != first:
        raise SystemExit("The passwords do not match.")
    return first


def create_user(conn, email, password):
    email = email.strip()
    if not EMAIL_RE.match(email):
        raise ValueError("That is not a valid e-mail address.")
    password_hash = security.hash_password(password)             # raises ValueError for a weak password
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM users WHERE lower(email) = lower(%s)", (email,))
        if cur.fetchone():
            raise ValueError("An account with this e-mail already exists.")
        cur.execute("INSERT INTO users (email, password_hash) VALUES (%s, %s) RETURNING id", (email, password_hash))
        user_id = cur.fetchone()[0]
    conn.commit()
    return user_id


def set_password(conn, email, password):
    password_hash = security.hash_password(password)
    with conn.cursor() as cur:
        cur.execute("UPDATE users SET password_hash = %s WHERE lower(email) = lower(%s) RETURNING id",
                    (password_hash, email.strip()))
        row = cur.fetchone()
        if row is None:
            raise ValueError("No such user.")
        cur.execute("UPDATE sessions SET revoked_at = now() WHERE user_id = %s AND revoked_at IS NULL", (row[0],))
    conn.commit()


def set_active(conn, email, active):
    with conn.cursor() as cur:
        cur.execute("UPDATE users SET is_active = %s WHERE lower(email) = lower(%s) RETURNING id",
                    (active, email.strip()))
        row = cur.fetchone()
        if row is None:
            raise ValueError("No such user.")
        if not active:
            cur.execute("UPDATE sessions SET revoked_at = now() WHERE user_id = %s AND revoked_at IS NULL", (row[0],))
    conn.commit()


def revoke_sessions(conn, email):
    with conn.cursor() as cur:
        cur.execute("UPDATE sessions SET revoked_at = now() WHERE revoked_at IS NULL AND user_id = "
                    "(SELECT id FROM users WHERE lower(email) = lower(%s))", (email.strip(),))
        count = cur.rowcount
    conn.commit()
    return count


def list_users(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT id, email, is_active, created_at, last_login_at FROM users ORDER BY id")
        return cur.fetchall()


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    command, args = argv[0], argv[1:]
    try:
        conn = db.get_connection()
    except db.DatabaseError as exc:
        print("Cannot connect: %s" % exc, file=sys.stderr)
        return 1
    try:
        if command == "create-user" and len(args) == 1:
            print("Created user #%d" % create_user(conn, args[0], _password()))
        elif command == "set-password" and len(args) == 1:
            set_password(conn, args[0], _password())
            print("Password changed; existing sign-ins ended.")
        elif command == "disable-user" and len(args) == 1:
            set_active(conn, args[0], False)
            print("Disabled.")
        elif command == "enable-user" and len(args) == 1:
            set_active(conn, args[0], True)
            print("Enabled.")
        elif command == "revoke-sessions" and len(args) == 1:
            print("Ended %d sign-in(s)." % revoke_sessions(conn, args[0]))
        elif command == "list-users":
            for row in list_users(conn):
                print("%4d  %-40s %s  created %s  last sign-in %s"
                      % (row[0], row[1], "active  " if row[2] else "DISABLED", row[3], row[4]))
        else:
            print(__doc__)
            return 2
    except ValueError as exc:
        print("Error: %s" % exc, file=sys.stderr)
        return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
