"""Which services are the panel's, restarting them, and the health check after an update.

The panel's units: AGENTKENNEL_UPDATE_RESTART ("user:a.service system:b.service"), else the
unit the dashboard's backend runs in (from its cgroup) — and with install.sh's
agentkennel-status.service, its task board agentkennel-tasks.service too. Or a command of
your own (AGENTKENNEL_UPDATE_RESTART_CMD) for a setup without systemd.

Never restarted: the Telegram bridge (claude-tg-bridge.service, which on some servers
holds the tmux server of every terminal), any unit whose cgroup holds tmux or claude unless
its KillMode=process (then only its main process stops), and the unit the updater itself
runs in unless the updater survives that (KillMode=process)."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import time
import urllib.request

DENY = {"claude-tg-bridge.service"}
TERMINAL_PROCS = ("tmux", "claude")


def unit_of_cgroup(text):
    """("user"|"system", "<name>.service") from /proc/<pid>/cgroup, or None."""
    for line in (text or "").splitlines():
        parts = line.split(":", 2)
        if len(parts) != 3 or parts[1] not in ("", "name=systemd"):
            continue
        segs = parts[2].strip("/").split("/")
        if not segs or not segs[-1].endswith(".service"):
            continue
        scope = "user" if any(s.startswith("user@") for s in segs) else "system"
        return scope, segs[-1]
    return None


def own_cgroup(pid="self"):
    try:
        text = open(f"/proc/{pid}/cgroup").read()
    except OSError:
        return None
    for line in text.splitlines():
        parts = line.split(":", 2)
        if len(parts) == 3 and parts[1] in ("", "name=systemd"):
            return parts[2].strip()
    return None


def panel_units(own, env=None):
    env = os.environ if env is None else env
    spec = env.get("AGENTKENNEL_UPDATE_RESTART") or env.get("AGENTDECK_UPDATE_RESTART")
    if spec:
        out = []
        for item in spec.split():
            scope, _, name = item.partition(":")
            if scope in ("user", "system") and name.endswith(".service"):
                out.append((scope, name))
        return out
    if not own:
        return []
    units = [own]
    if own == ("system", "agentkennel-status.service"):
        units.append(("system", "agentkennel-tasks.service"))
    return units


def _systemctl(scope):
    return ["systemctl", "--user"] if scope == "user" else ["systemctl"]


def show(scope, unit, run=None):
    run = run or subprocess.run
    r = run([*_systemctl(scope), "show", "-p", "KillMode", "-p", "Restart", "-p", "MainPID",
             "-p", "ControlGroup", "-p", "ActiveState", unit],
            capture_output=True, text=True, timeout=30)
    return dict(line.split("=", 1) for line in r.stdout.splitlines() if "=" in line)


def cgroup_procs(cg):
    """The program names (comm) of every process in cgroup `cg`."""
    names = []
    for base in ("/sys/fs/cgroup", "/sys/fs/cgroup/systemd"):
        try:
            pids = open(f"{base}{cg}/cgroup.procs").read().split()
        except OSError:
            continue
        for pid in pids:
            names.append(_proc_name(pid))
        break
    return [n for n in names if n]


def _proc_name(pid):
    """comm, or "claude" for Claude Code's native binary (its comm is its version number:
    ~/.local/share/claude/versions/2.1.326)."""
    try:
        comm = open(f"/proc/{pid}/comm").read().strip()
    except OSError:
        return ""
    try:
        exe = os.readlink(f"/proc/{pid}/exe")
    except OSError:
        exe = ""
    if "/claude/versions/" in exe or os.path.basename(exe) == "claude":
        return "claude"
    return comm


def refuse_reason(scope, unit, run=None, procs=None, own_cgroup=None):
    """Why this unit must not be restarted by an update; None when it may."""
    if unit in DENY:
        return f"{unit} is never restarted by an update: it may hold the terminals"
    info = show(scope, unit, run)
    whole = info.get("KillMode", "control-group") != "process"
    cg = info.get("ControlGroup", "")
    if whole and own_cgroup and cg == own_cgroup:
        return (f"the updater runs inside {unit}: restarting it would stop the update itself "
                f"(KillMode={info.get('KillMode')})")
    if whole and cg:
        held = [p for p in (procs or cgroup_procs)(cg)
                if any(p == t or p.startswith(t + ":") for t in TERMINAL_PROCS)]
        if held:
            return (f"{unit} holds the terminals ({', '.join(sorted(set(held)))} run in it): "
                    "restarting it would stop them")
    return None


def _wait_new_pid(scope, unit, old, run, sleep, tries=30):
    for _ in range(tries):
        sleep(1)
        info = show(scope, unit, run)
        if info.get("MainPID", "0") not in ("0", str(old)) and info.get("ActiveState") == "active":
            return True
    return False


def restart(scope, unit, run=None, kill=None, sleep=None):
    """(ok, how). System units through `sudo -n` (never asks for a password); without it,
    a unit with Restart=always is stopped by its main PID and systemd starts it again."""
    run, kill, sleep = run or subprocess.run, kill or os.kill, sleep or time.sleep
    sudo = ["sudo", "-n"] if scope == "system" else []
    # a version that crashed on start may have hit systemd's start limit: clear it first,
    # or the restart (and the rollback's) is refused as "start request repeated too quickly"
    run([*sudo, *_systemctl(scope), "reset-failed", unit], capture_output=True, text=True, timeout=30)
    cmd = [*sudo, *_systemctl(scope), "restart", unit]
    r = run(cmd, capture_output=True, text=True, timeout=120)
    if r.returncode == 0:
        return True, " ".join(cmd)
    info = show(scope, unit, run)
    pid = int(info.get("MainPID", "0") or 0)
    if info.get("Restart") not in ("always", "on-success", "on-abnormal") or pid <= 0:
        return False, (f"could not restart {unit}: {(r.stderr or '').strip()[:200]} "
                       "(no sudo without a password, and it does not restart by itself)")
    kill(pid, signal.SIGTERM)
    if _wait_new_pid(scope, unit, pid, run, sleep):
        return True, f"stopped pid {pid}; systemd started {unit} again"
    return False, f"{unit} did not come back after its pid {pid} was stopped"


def restart_cmd(command, run=None):
    run = run or subprocess.run
    r = run(["bash", "-c", command], capture_output=True, text=True, timeout=300)
    if r.returncode == 0:
        return True, command
    return False, f"{command!r} failed (exit {r.returncode}): {(r.stderr or '').strip()[:200]}"


def get_json(url):
    with urllib.request.urlopen(url, timeout=5) as r:
        return json.loads(r.read(65536))


def healthy(url, version, get=None, timeout=90, sleep=None, clock=None):
    """(ok, why): the panel at `url` answers that it runs `version`."""
    get, sleep, clock = get or get_json, sleep or time.sleep, clock or time.monotonic
    end, last = clock() + timeout, "no answer"
    while True:
        try:
            running = (get(url) or {}).get("running")
            if running == version:
                return True, f"v{version} answers"
            last = f"v{running} answers instead of v{version}"
        except (OSError, ValueError) as e:
            last = f"no answer ({type(e).__name__}: {str(e)[:120]})"
        if clock() >= end:
            return False, f"the panel is not healthy after {timeout} s: {last}"
        sleep(2)
