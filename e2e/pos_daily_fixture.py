"""Run the real scheduler at a fixed business time with the existing synthetic POS."""

import os
import threading
import time
from datetime import datetime
from pathlib import Path
from urllib.error import URLError

import uvicorn

from backend import pos_providers, pos_sync
from backend.app import app
from backend.pos_vault import init_key
from e2e.pos_fixture import PosterFixture


class UnavailablePoster:
    def open(self, request, timeout):
        raise URLError("fixture unavailable")


if __name__ == "__main__":
    if os.environ.get("POS_TEST_PROVISION") == "1":
        init_key(Path(os.environ["BAKEPLAN_POS_MASTER_KEY_FILE"]))
    fixed_now = datetime.fromisoformat(os.environ["POS_TEST_NOW"])
    pos_sync.now_utc = lambda: fixed_now
    pos_sync.POLL_SECONDS = 0.2
    vendor = (
        UnavailablePoster()
        if os.environ.get("POS_TEST_OUTAGE") == "1"
        else PosterFixture()
    )
    pos_providers.build_opener = lambda *args: vendor
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    sock = config.bind_socket()
    Path(os.environ["POS_TEST_PORT_FILE"]).write_text(str(sock.getsockname()[1]))
    server = uvicorn.Server(config)
    stop = Path(os.environ["POS_TEST_STOP_FILE"])

    def wait_for_stop():
        while not stop.exists():
            time.sleep(0.1)
        server.should_exit = True

    threading.Thread(target=wait_for_stop, daemon=True).start()
    server.run(sockets=[sock])
