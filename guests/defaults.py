"""Defaults for new guests: <data>/new_guest_defaults.json. A missing file is exactly
DEFAULTS. Only shape is checked here; "an owner key must exist" and "understood" are the
caller's (guestctl) business."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

DEFAULTS = {"claude_mode": "owner_key", "usd_week": 20, "notify_at": 0.8,
            "week_limit": 140_000_000, "web": True, "rules": {}}
MODES = ("owner_key", "owner_sub")     # "own" is a guest's own choice, never a default
FILE = "new_guest_defaults.json"


def _copy(d: dict) -> dict:
    return {**d, "rules": dict(d["rules"])}


def validate(d) -> dict:
    """The complete defaults, ValueError when malformed (unknown keys included)."""
    if not isinstance(d, dict):
        raise ValueError("defaults: an object")
    unknown = sorted(set(d) - set(DEFAULTS))
    if unknown:
        raise ValueError(f"defaults: unknown field(s): {', '.join(map(str, unknown))}")
    out = _copy(DEFAULTS)
    out.update(d)
    if out["claude_mode"] not in MODES:
        raise ValueError(f"claude_mode: one of {', '.join(MODES)}")
    usd = out["usd_week"]
    if isinstance(usd, bool) or not isinstance(usd, (int, float)) or not 0 < usd <= 100000:
        raise ValueError("usd_week must be a number above 0 and up to 100000")
    n = out["notify_at"]
    if isinstance(n, bool) or not isinstance(n, (int, float)) or not 0 < n <= 1:
        raise ValueError("notify_at must be a number above 0 and up to 1")
    w = out["week_limit"]
    if isinstance(w, bool) or not isinstance(w, int) or w <= 0:
        raise ValueError("week_limit must be a positive whole number")
    if not isinstance(out["web"], bool):
        raise ValueError("web must be true or false")
    if not isinstance(out["rules"], dict):
        raise ValueError("rules: an object")
    if out["rules"]:                    # the same check `guestctl rules` makes
        import turnstile
        out["rules"] = turnstile.validate_rules(out["rules"])
    return out


def load(data: Path) -> dict:
    try:
        d = json.loads((Path(data) / FILE).read_text())
        return validate(d)
    except (OSError, ValueError):
        return _copy(DEFAULTS)


def save(data: Path, d: dict) -> dict:
    out = validate(d)
    data = Path(data)
    data.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=data, prefix=".tmp-defaults-")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(out, f)
        os.replace(tmp, data / FILE)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return out
