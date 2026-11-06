"""The guest bot never sends a dashboard password that no longer works.

The sandbox keeps only a hash of its dashboard password (SANDBOX_PASSFILE, written
by AgentKennel's status_server: "<salt hex>$<pbkdf2-sha256 hex>"); the host keeps
the password the bot handed out (<data>/<name>.dashboard-password).

Owner, 06.11.2026 (Sveta could not get in):
- /web changes nothing. status() says whether the bot's password is the one in
  force; when the guest set their own it says so, since a hash can't be read back.
- /newpassword: reset() puts a new password into the sandbox, keeps it as the
  bot's copy and returns it.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import subprocess
import threading
from pathlib import Path
from typing import Callable, Optional, Tuple

SANDBOX_PASSFILE = "/app/.sessions/.dashpass"
ITERATIONS = 200_000                    # status_server._pw_store / _pw_verify
_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_LOCK = threading.Lock()                # a /web read never sees half a /newpassword


def hash_line(pw: str, salt: Optional[bytes] = None) -> str:
    salt = salt or os.urandom(16)
    return f"{salt.hex()}${hashlib.pbkdf2_hmac('sha256', pw.encode(), salt, ITERATIONS).hex()}"


def verifies(pw: str, line: str) -> bool:
    try:
        salt_hex, want = line.strip().split("$", 1)
        got = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt_hex), ITERATIONS)
        return hmac.compare_digest(got.hex(), want)
    except ValueError:
        return False


def _run(argv, input=None):
    return subprocess.run(argv, input=input, capture_output=True, text=True, timeout=30)


def _copy(name: str, data: Path) -> Path:
    return Path(data) / f"{name}.dashboard-password"


def status(name: str, data: Path, run: Callable = _run) -> Tuple[Optional[str], bool]:
    """(the bot's password if it opens the dashboard, whether the guest set their own).
    Changes nothing anywhere."""
    if not _NAME.match(name or ""):
        return None, False
    with _LOCK:
        try:
            pw = _copy(name, data).read_text().strip()
        except OSError:
            return None, False
        if not pw:
            return None, False
        r = run(["docker", "exec", f"sbx-{name}", "sh", "-c",
                 f"cat {SANDBOX_PASSFILE} 2>/dev/null || true"])
        stored = (r.stdout or "").strip() if r.returncode == 0 else ""
        # not running, or never logged into (the first login seeds the hash from the
        # sandbox's own first password, the bot's): the bot's is the one
        if not stored or verifies(pw, stored):
            return pw, False
        return None, True


def reset(name: str, data: Path, run: Callable = _run) -> Optional[str]:
    """/newpassword: a new password in force in sbx-<name>, also the bot's copy.
    None when it didn't take (the copy is left as it was)."""
    if not _NAME.match(name or ""):
        return None
    with _LOCK:
        new = secrets.token_urlsafe(12)
        tmp = SANDBOX_PASSFILE + ".new"
        w = run(["docker", "exec", "-i", f"sbx-{name}", "sh", "-c",
                 f"umask 077; cat > {tmp} && mv {tmp} {SANDBOX_PASSFILE}"],
                input=hash_line(new))
        if w.returncode != 0:
            return None
        f = _copy(name, data)
        old = os.umask(0o077)
        try:
            f.write_text(new + "\n")
        finally:
            os.umask(old)
        f.chmod(0o600)
        return new
