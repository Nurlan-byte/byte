"""Run the real authenticated API with two synthetic administrators."""

import os
import tempfile
from pathlib import Path

import bcrypt
import uvicorn

from backend.branch_accounts import set_account
from backend.storage import initialize_database


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="bakeplan-branches-") as folder:
        os.environ["BAKEPLAN_DB"] = str(Path(folder) / "test.sqlite3")
        os.environ.pop("BAKEPLAN_LOCAL_DEMO", None)
        initialize_database()
        for branch in ("north", "south"):
            password_hash = bcrypt.hashpw(b"fixture-only", bcrypt.gensalt(rounds=4))
            set_account(f"admin-{branch}", branch, password_hash.decode("ascii"))
        uvicorn.run("backend.app:app", host="127.0.0.1", port=18007)
