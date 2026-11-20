"""The update's result in Telegram, when the install has a bot.

The bot: the dashboard's Telegram page (<install>/.sessions/telegram.json, its
notifications bot first), else the bridge's own service settings (TG_BRIDGE_TOKEN and
TG_BRIDGE_OWNER in <install>/.env, the file its systemd unit reads)."""
from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path

MAX_TEXT = 3500


def _env_file(path):
    out = {}
    try:
        lines = Path(path).read_text().splitlines()
    except OSError:
        return out
    for line in lines:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip("'\"")
    return out


def target(install_dir, env=None):
    """(token, chat id) or None."""
    env = os.environ if env is None else env
    conf = (env.get("AGENTKENNEL_TG_CONF") or env.get("AGENTDECK_TG_CONF")
            or os.path.join(install_dir, ".sessions", "telegram.json"))
    try:
        cfg = json.loads(Path(conf).read_text())
    except (OSError, ValueError):
        cfg = {}
    if isinstance(cfg, dict) and cfg.get("token") and cfg.get("owner"):
        return cfg.get("notify_token") or cfg["token"], str(cfg["owner"])
    dot = _env_file(os.path.join(install_dir, ".env"))
    token, owner = dot.get("TG_BRIDGE_TOKEN"), dot.get("TG_BRIDGE_OWNER")
    return (token, str(owner)) if token and owner else None


def _plain(markdown):
    return re.sub(r"\*\*(.+?)\*\*", r"\1", re.sub(r"`([^`]*)`", r"\1", markdown or ""))


def message(r):
    frm, to = r.get("from", "?"), r.get("to", "?")
    how = "automatically (patch release, at night)" if r.get("auto") else "from the panel"
    state = r.get("state")
    if state == "done":
        lines = [f"✅ AgentKennel updated: v{frm} → v{to}, {how}.", ""]
        for s in r.get("notes") or []:
            lines.append(f"v{s['version']} — {s.get('title', '')}".rstrip(" —"))
            lines.append(_plain(s.get("body", "")))
            lines.append("")
        for note in r.get("todo") or []:
            lines.append("To do: " + note)
        text = "\n".join(lines).strip()
    else:
        step = r.get("step") or "the update"
        text = (f"❌ AgentKennel update v{frm} → v{to} ({how}) failed at {step}: "
                f"{r.get('error') or 'unknown error'}.")
        if state == "rolled-back":
            text += f"\nRolled back: v{frm} is running as before."
        elif state == "rollback-failed":
            text += (f"\nThe rollback failed too ({r.get('rollback_error')}): fix it by hand — "
                     f"the backup is {r.get('backup') or 'in .sessions/update/backups'}.")
        elif state == "refused":
            text += "\nNothing was changed."
    return text if len(text) <= MAX_TEXT else text[:MAX_TEXT - 1] + "…"


def _post(url, data):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(),
                                 headers={"User-Agent": "AgentKennel-updater"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read() or b"{}")


def send(token, chat, text, post=None):
    """(ok, why)."""
    try:
        d = (post or _post)(f"https://api.telegram.org/bot{token}/sendMessage",
                            {"chat_id": chat, "text": text})
    except (OSError, ValueError) as e:
        return False, str(e)[:200] or type(e).__name__
    return (True, "") if d.get("ok") else (False, str(d.get("description") or d)[:200])
