"""The Server tab's data: /api/server (server_status collector) and /api/services.

Part of status_server (the facade re-exports every name here)."""

import time
import os
import envcompat
import server_status
import services_keeper

from backend.library_api import LibError


# ── Server page: GET /api/server (see server_status.py) ─────────────────────
# The collector samples /proc every 5 s in a daemon thread (started in __main__);
# a request only reads the latest snapshot. Same-origin only, never cached.
SERVER_ROUTE = "/api/server"
# a guest's sandbox has a disk of its own (AGENTKENNEL_DISK_PATH=/work, guests/disk.py)
SERVER_COLLECTOR = server_status.Collector(disk_path=envcompat.get("DISK_PATH") or "/")


# ── Server page, Services block: /api/services (2.0) ────────────────────────
# The services keeper (services_keeper.py, its own unit / the Docker entrypoint) owns the
# services; this only relays its /status and actions and reads the log tail from the
# same folder it uses. Same-origin only, like the library.
SVC_ROUTE = "/api/services"
SVC_LOG_LINES = 200


def svc_listing():
    services = services_keeper.Client().status()
    return {"available": services is not None, "services": services or {},
            "dir": str(services_keeper.default_dir()), "now": time.time()}


def svc_log(name):
    if not services_keeper.NAME_RE.match(name):
        raise LibError(400, "bad service name")
    path = services_keeper.default_dir() / f"{name}.log"
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 256 * 1024))
            text = f.read().decode("utf-8", "replace")
    except FileNotFoundError:
        return {"name": name, "lines": []}
    return {"name": name, "lines": text.splitlines()[-SVC_LOG_LINES:]}


def svc_post(route):
    parts = route.split("/")
    if len(parts) != 2 or parts[0] not in services_keeper.ACTIONS:
        raise LibError(404, "not found")
    if not services_keeper.NAME_RE.match(parts[1]):
        raise LibError(400, "bad service name")
    if not services_keeper.Client().act(parts[0], parts[1]):
        raise LibError(404, "no such service (or the keeper isn't running)")
    return {"ok": True}
