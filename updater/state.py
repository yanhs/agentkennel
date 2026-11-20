"""The update's files, in <install>/.sessions/update/ (AGENTKENNEL_UPDATE_DIR elsewhere):

    latest.json    the last answer about the latest release (release.check)
    settings.json  {"auto_patch": false} — night-time patch updates, off until turned on
    pending.json   "update when all agents are idle" / tonight's automatic update, waiting
    job.json       the update running now (or the last one): its steps, as it goes
    last.json      the result the panel has not shown yet
    backups/       a copy before each update (backup.py), the newest few"""
from __future__ import annotations

import json
import os
from pathlib import Path

LATEST, SETTINGS, PENDING, JOB, LAST = ("latest.json", "settings.json", "pending.json",
                                         "job.json", "last.json")
BACKUPS = "backups"


def update_dir(install_dir, env=None) -> Path:
    env = os.environ if env is None else env
    own = env.get("AGENTKENNEL_UPDATE_DIR") or env.get("AGENTDECK_UPDATE_DIR")
    return Path(own) if own else Path(install_dir) / ".sessions" / "update"


def read(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write(path, data):
    """Written whole (a temporary file, then a rename), readable by you only."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def remove(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
