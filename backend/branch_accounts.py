"""Assign or revoke a branch login using operator access to the BakePlan database."""

import argparse
import base64
import getpass
import re
import sys
import warnings
from pathlib import Path

import bcrypt

from backend.storage import connection, initialize_database


def set_account(username: str, bakery_id: str, password_hash: str):
    if not re.fullmatch(r"[A-Za-z0-9_.@-]{1,128}", username):
        raise ValueError("Invalid login")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", bakery_id):
        raise ValueError("Invalid branch ID")
    if not re.fullmatch(r"\$2[aby]\$(?:0[4-9]|[12][0-9]|3[01])\$[./a-zA-Z0-9]{53}", password_hash):
        raise ValueError("Expected a bcrypt password hash")
    with connection() as con:
        con.execute(
            "INSERT INTO branch_accounts(username,bakery_id,password_hash) VALUES(?,?,?) "
            "ON CONFLICT(username) DO UPDATE SET bakery_id=excluded.bakery_id, password_hash=excluded.password_hash",
            (username, bakery_id, password_hash),
        )


def revoke_account(username: str):
    with connection() as con:
        if not con.execute("UPDATE branch_accounts SET password_hash=NULL WHERE username=?", (username,)).rowcount:
            raise ValueError("Unknown login")


def authenticate(authorization: list[str], local_demo: bool):
    """Return (login, branch), (None, None) for local fixtures, or None for denial."""
    if not authorization and local_demo:
        with connection() as con:
            return None if con.execute("SELECT 1 FROM branch_accounts LIMIT 1").fetchone() else (None, None)
    if len(authorization) != 1:
        return None
    scheme, _, token = authorization[0].partition(" ")
    if scheme.lower() != "basic" or len(token) > 1024:
        return None
    try:
        credentials = base64.b64decode(token, validate=True)
        login, password = credentials.split(b":", 1)
        username = login.decode("utf-8")
        if not password or len(password) > 72:
            return None
        with connection() as con:
            row = con.execute("SELECT bakery_id,password_hash FROM branch_accounts WHERE username=?", (username,)).fetchone()
        if row and row[1] and bcrypt.checkpw(password, row[1].encode("ascii")):
            return username, row[0]
    except ValueError:
        pass
    return None


def _caddy_hash(source, username):
    lines = sys.stdin if source == "-" else Path(source).read_text(encoding="utf-8").splitlines()
    entries = [fields[1] for line in lines if len(fields := line.split()) == 2 and fields[0] == username]
    if len(entries) != 1:
        raise ValueError("Expected a single entry for this login in the Caddy users file")
    return entries[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    assign = commands.add_parser("assign")
    assign.add_argument("username")
    assign.add_argument("bakery_id")
    assign.add_argument("--caddy-users", metavar="FILE", help="Reuse the existing login hash. Use - for stdin.")
    commands.add_parser("revoke").add_argument("username")
    args = parser.parse_args()
    initialize_database()
    try:
        if args.command == "revoke":
            revoke_account(args.username)
        else:
            if args.caddy_users:
                password_hash = _caddy_hash(args.caddy_users, args.username)
            else:
                with warnings.catch_warnings():
                    warnings.simplefilter("error", getpass.GetPassWarning)
                    password = getpass.getpass("Password: ").encode("utf-8")
                if not password or len(password) > 72:
                    raise ValueError("Password must contain 1 to 72 UTF-8 bytes")
                password_hash = bcrypt.hashpw(password, bcrypt.gensalt()).decode("ascii")
            set_account(args.username, args.bakery_id, password_hash)
    except (getpass.GetPassWarning, EOFError):
        parser.exit(2, "A terminal with hidden password input is required\n")
    except (ValueError, OSError) as exc:
        parser.exit(2, f"{exc}\n")


if __name__ == "__main__":
    main()
