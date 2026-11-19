"""The Telegram bridge from the dashboard: its settings file, start/stop/autostart, a test
message and the /telegram setup page.

Part of status_server (the facade re-exports every name here)."""

import json
import subprocess
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import envcompat

from backend import _HERE


# ── Telegram bridge: configure + start/stop from the dashboard ────────────────
TG_CONF = os.path.join(_HERE, ".sessions", "telegram.json")
TG_PID = os.path.join(_HERE, ".sessions", "tg_bridge.pid")
TG_SCRIPT = os.path.join(_HERE, "tg_bridge.py")


def tg_load():
    try:
        return json.load(open(TG_CONF))
    except (OSError, ValueError):
        return {}


def tg_save(cfg):
    os.makedirs(os.path.dirname(TG_CONF), exist_ok=True)
    old = os.umask(0o077)
    try:
        json.dump(cfg, open(TG_CONF, "w"))
    finally:
        os.umask(old)


TG_SYSTEMD_UNIT = os.environ.get("TG_SYSTEMD_UNIT", "claude-tg-bridge")


def tg_systemd_active():
    """The bridge runs as a systemd user service on this server (not started by
    this page): then the page must neither report it stopped nor start a second
    one on the same token (two pollers fight over Telegram updates)."""
    try:
        r = subprocess.run(["systemctl", "--user", "is-active", TG_SYSTEMD_UNIT],
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip() == "active"
    except (OSError, subprocess.SubprocessError):
        return False


def tg_running():
    try:
        pid = int(open(TG_PID).read().strip())
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError):
        return 0


def tg_stop():
    pid = tg_running()
    if pid:
        try:
            os.kill(pid, 15)
        except OSError:
            pass
    try:
        os.remove(TG_PID)
    except OSError:
        pass


def tg_start(token, owner):
    tg_stop()
    os.makedirs("/work/tg-uploads", exist_ok=True)
    env = {**os.environ, "TG_BRIDGE_TOKEN": token, "TG_BRIDGE_OWNER": str(owner),
           "TG_FILES_DIR": "/work/tg-uploads", "TG_FILES_URL": "",
           "TG_AGENT_CWD": envcompat.get("WORKDIR", "/work")}
    try:
        p = subprocess.Popen(["python3", TG_SCRIPT], env=env, cwd=_HERE,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        open(TG_PID, "w").write(str(p.pid))
        return True
    except Exception:
        return False


def tg_autostart():
    """Called at startup: if the bridge was configured + enabled, bring it back up."""
    cfg = tg_load()
    if tg_systemd_active():
        return
    if cfg.get("enabled") and cfg.get("token") and cfg.get("owner") and not tg_running():
        tg_start(cfg["token"], cfg["owner"])


def tg_action(action, token, owner):
    """Start/stop from the setup page; refused when systemd owns the bridge."""
    if tg_systemd_active():
        return (f"The bot is managed by the systemd service {TG_SYSTEMD_UNIT} on this "
                "server — nothing changed here.")
    if action == "stop":
        tg_stop()
        cfg = tg_load(); cfg["enabled"] = False; tg_save(cfg)
        return "Bot stopped."
    if not token or not owner:
        return "Enter both the bot token and your Telegram user id."
    tg_save({**tg_load(), "token": token, "owner": owner, "enabled": True})   # keeps notify_token
    return ("Bot started — open Telegram and message it."
            if tg_start(token, owner)
            else "Could not start the bot — check the container logs.")


TG_TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_-]+$")
TG_NOTIFY_TEST = ("🔔 AgentKennel's notifications will come here: guests' spending, strange "
                  "requests, published sites. Your agents stay in the main bot.")


def tg_send(token, chat, text):
    """One sendMessage: (ok, Telegram's description when it refused)."""
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage",
                                 data=urllib.parse.urlencode({"chat_id": chat, "text": text}).encode())
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return bool(json.loads(resp.read() or b"{}").get("ok")), ""
    except urllib.error.HTTPError as e:
        try:
            return False, json.loads(e.read() or b"{}").get("description") or f"HTTP {e.code}"
        except ValueError:
            return False, f"HTTP {e.code}"
    except (OSError, ValueError) as e:
        return False, str(e)


def tg_notify_action(notify_token):
    """The second bot, for notifications (owner 12.11.2026): warnings go there, the chat
    with the agents stays clean. Saved only after it has written to you once; empty =
    notifications go to the main bot again. Works with a systemd bridge too: the bridge
    doesn't use it, guests/guestenv.notify_owner does."""
    cfg = tg_load()
    notify_token = (notify_token or "").strip()
    if not notify_token:
        cfg.pop("notify_token", None)
        tg_save(cfg)
        return "Notifications go to your main bot again."
    if not (cfg.get("token") and cfg.get("owner")):
        return "Set up your main bot first: the notifications bot writes to the same Telegram id."
    if not TG_TOKEN_RE.match(notify_token):
        return "That doesn't look like a bot token (123456:ABC-DEF…, from @BotFather)."
    if notify_token == cfg.get("token"):
        return "That is your main bot. Make a second bot in @BotFather for notifications."
    ok, why = tg_send(notify_token, cfg["owner"], TG_NOTIFY_TEST)
    if not ok:
        return (f"Not saved: Telegram refused ({why}). If the bot is new, open it in Telegram, "
                "press Start, then save again.")
    cfg["notify_token"] = notify_token
    tg_save(cfg)
    return "Notifications bot saved: it has just written to you."


def tg_render(msg=""):
    # built lazily so _AUTH_HEAD (defined further down) is resolved at request time
    page = (_AUTH_HEAD.replace("min(92vw,340px)", "min(92vw,420px)") +
      '<form class="card" method="POST" action="/telegram">'
      '<h1>📲 Telegram bot</h1>'
      '<p class="sub">Drive your agents from Telegram. __STATUS__</p>'
      '<label for="t">Bot token <span style="color:#6e7681">(from @BotFather)</span></label>'
      '<input id="t" name="token" value="__TOKEN__" placeholder="123456:ABC-DEF...">'
      '<label for="o">Your Telegram user id <span style="color:#6e7681">(from @userinfobot)</span></label>'
      '<input id="o" name="owner" value="__OWNER__" placeholder="123456789" inputmode="numeric">'
      '<button type="submit" name="action" value="start">Save &amp; start</button>'
      '<button type="submit" name="action" value="stop" style="margin-top:8px;background:#30363d">Stop</button>'
      '<label for="n" style="margin-top:18px">Notifications bot <span style="color:#6e7681">(optional, a second bot)</span></label>'
      '<p class="sub" style="margin:0 0 6px">Guests\' spending, strange requests and published sites go there, '
      'so they don\'t get lost among your agents. Empty: they come to the bot above.</p>'
      '<input id="n" name="notify_token" form="tgn" value="__NOTIFY__" placeholder="123456:ABC-DEF...">'
      '<button type="submit" form="tgn" name="action" value="notify" style="margin-top:8px;background:#30363d">Save notifications bot</button>'
      '__MSG__'
      '<p style="text-align:center;margin-top:14px"><a href="/">&larr; back to dashboard</a></p>'
      '</form>'
      # a form of its own: Enter in the notifications field must not press "Save & start"
      '<form id="tgn" method="POST" action="/telegram"></form></body></html>')
    cfg = tg_load()
    if tg_systemd_active():
        # managed outside this page: no Save&start / Stop (a second bridge on the
        # same token would fight the live one for updates)
        page = page.replace(
            '<button type="submit" name="action" value="start">Save &amp; start</button>', ''
        ).replace(
            '<button type="submit" name="action" value="stop" style="margin-top:8px;background:#30363d">Stop</button>',
            f'<p class="sub">Managed by the systemd service <code>{TG_SYSTEMD_UNIT}</code> on this '
            'server — start/stop it there, not here.</p>')
        status = '<b style="color:#3fb950">running</b> (systemd)'
    else:
        status = ('<b style="color:#3fb950">running</b>' if tg_running()
                  else '<b style="color:#8b949e">stopped</b>')
    return (page.replace("__STATUS__", "Status: " + status)
            .replace("__TOKEN__", (cfg.get("token") or "").replace('"', ""))
            .replace("__OWNER__", str(cfg.get("owner") or ""))
            .replace("__NOTIFY__", (cfg.get("notify_token") or "").replace('"', ""))
            .replace("__MSG__", f'<p class="ok">{msg}</p>' if msg else ''))


# used only inside functions here, defined in a module the facade imports later
from backend.auth import _AUTH_HEAD  # noqa: E402
