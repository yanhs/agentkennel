"""The setup steps of THIS release, run by an update after the code moved to it:

    python3 -m updater.setup_steps --install <dir>     (prints a JSON list of the steps)

Only steps that are safe to run again and need no sudo. A release that needs more (a new
apt package, a changed systemd unit) says so in CHANGELOG.md's "Upgrading" lines, and the
update tells you to run install.sh once more when install.sh changed.

Today:
- guard hooks: hooks/install_guards.py with install.sh's own arguments, on an install
  that install.sh made (its record, ~/.config/agentkennel/install.env, names this folder)
  and unless AGENTKENNEL_GUARDS=0. The Docker image runs it on every start anyway."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys


def _record_path(env):
    own = env.get("AGENTKENNEL_CONF") or env.get("AGENTDECK_CONF")
    if own:
        return own
    base = env.get("XDG_CONFIG_HOME") or os.path.join(env.get("HOME") or os.path.expanduser("~"), ".config")
    return os.path.join(base, "agentkennel", "install.env")


def install_record(env=None):
    """install.sh's record: {"DIR": ..., "WORKDIR": ..., ...}; {} when there is none."""
    env = os.environ if env is None else env
    out = {}
    try:
        lines = open(_record_path(env), encoding="utf-8").read().splitlines()
    except OSError:
        return out
    for line in lines:
        k, sep, v = line.partition("=")
        if sep and k.startswith("AGENTKENNEL_"):
            out[k[len("AGENTKENNEL_"):]] = v
    return out


def run_steps(install_dir, home, env=None, run=None):
    env = os.environ if env is None else env
    run = run or subprocess.run
    rec = install_record(env)
    out = []
    guards = (env.get("AGENTKENNEL_GUARDS") or env.get("AGENTDECK_GUARDS") or "1") != "0"
    if guards and rec.get("DIR") and os.path.realpath(rec["DIR"]) == os.path.realpath(install_dir):
        workdir = rec.get("WORKDIR") or os.path.join(home, "projects")
        d = install_dir.rstrip("/")
        cmd = [sys.executable, f"{d}/hooks/install_guards.py",
               "--settings", f"{home}/.claude/settings.json", "--hooks-dir", f"{d}/hooks",
               "--tracker-state", f"{d}/.sessions/tasks-state.json",
               "--claude-md", f"{home}/.claude/CLAUDE.md",
               "--tracker", f"{d}/tasks-dashboard/tracker.py",
               "--services-dir", f"{workdir.rstrip('/')}/.services"]
        r = run(cmd, capture_output=True, text=True, timeout=120)
        out.append({"step": "guard hooks", "ok": r.returncode == 0,
                    "detail": ((r.stdout if r.returncode == 0 else r.stderr or r.stdout)
                               or "").strip()[-300:]})
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--install", required=True)
    a = p.parse_args(argv)
    home = os.environ.get("HOME") or os.path.expanduser("~")
    steps = run_steps(os.path.abspath(a.install), home)
    print(json.dumps(steps))
    return 0 if all(s["ok"] for s in steps) else 1


if __name__ == "__main__":
    sys.exit(main())
