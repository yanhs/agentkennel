"""The /use menu of the Telegram bridge (owner 27.11.2026): one command for everything a
terminal needs. Its head carries the Claude allowance; under the terminal buttons —
🆕 New and 🗄 Archive, and for the current terminal 🗜 Compact and ⟳ Restart.

This module holds what does not need the bridge: the allowance (from the dashboard's
GET /api/quota, which probes Anthropic at most once per 10 minutes and answers from its
cache) and the rows of action buttons as (label, callback data). tg_bridge.py turns
them into Telegram buttons and handles the taps (on_use_action_cb)."""

from __future__ import annotations

import json
import urllib.request

QUOTA_URL = "http://127.0.0.1:3011/api/quota"   # status_server, like the agent labels
QUOTA_TIMEOUT = 3

NEW, ARCHIVE, COMPACT, RESTART = "unew", "uarc", "ucmp", "urst"
PATTERN = r"^(unew|uarc|ucmp:[^:]+|urst:[^:]+)$"


def fetch_quota() -> dict | None:
    """The dashboard's /api/quota answer, or None when it cannot be had."""
    try:
        with urllib.request.urlopen(QUOTA_URL, timeout=QUOTA_TIMEOUT) as r:
            data = json.load(r)
    except Exception:  # noqa: BLE001 — the menu goes on without the numbers
        return None
    return data if isinstance(data, dict) else None


def _percent(window) -> int | None:
    u = window.get("utilization") if isinstance(window, dict) else None
    if isinstance(u, bool) or not isinstance(u, (int, float)) or u < 0:
        return None
    return min(100, round(u * 100))


def quota_line(data) -> str | None:
    """'📊 Claude: session 12% · week 45%' — the 5-hour session and the week, as Claude's
    /usage counts them. None when the dashboard has no numbers."""
    if not isinstance(data, dict) or not data.get("available"):
        return None
    five, week = _percent(data.get("five_hour")), _percent(data.get("seven_day"))
    if five is None or week is None:
        return None
    return f"📊 Claude: session {five}% · week {week}%"


def action_rows(cur: str | None, restartable: bool, n_archived: int) -> list[list[tuple[str, str]]]:
    """The rows under the terminal buttons: the current terminal's Compact and Restart
    (Restart only for a library terminal), then New and — when it has any — Archive."""
    rows = []
    if cur:
        row = [("🗜 Compact", f"{COMPACT}:{cur}")]
        if restartable:
            row.append(("⟳ Restart", f"{RESTART}:{cur}"))
        rows.append(row)
    row = [("🆕 New terminal", NEW)]
    if n_archived:
        row.append((f"🗄 Archive ({n_archived})", ARCHIVE))
    rows.append(row)
    return rows
