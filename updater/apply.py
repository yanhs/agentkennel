"""The update itself, in a process of its own (the panel it restarts started it):

    python3 -m updater.apply --install <dir> --to v2.8.0 [--auto] [--panel-pid <pid>]

1. checks: a git clone with no changed tracked files and no commits outside the release;
   the panel's services can be restarted without touching the terminals (services.py)
2. backup: the code and the settings an update may change (backup.py)
3. code: fetch the release tag, move the clone to it (install.py)
4. setup: the NEW release's own setup steps (python3 -m updater.setup_steps)
5. restart: only the panel's services
6. health: the panel answers that it runs the new version
Anything failing after 2 rolls back: git to the old commit, the settings from the backup,
the services restarted again, and the old version checked to answer.

Its steps go to <update dir>/job.json as it works (the panel shows them), the result to
last.json (the panel's notice) and to Telegram when the install has a bot. Exit code:
0 updated, 1 failed (rolled back or not), 2 refused (nothing changed), 3 another update
is running.

Settings (environment): AGENTKENNEL_UPDATE_REPO (default the GitHub repo),
AGENTKENNEL_UPDATE_HEALTH_URL (default http://127.0.0.1:3011/api/update),
AGENTKENNEL_UPDATE_HEALTH_TIMEOUT (s, default 90), AGENTKENNEL_UPDATE_RESTART /
AGENTKENNEL_UPDATE_RESTART_CMD (services.py)."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
import time
from pathlib import Path

if __package__ in (None, ""):                       # python3 updater/apply.py
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from updater import backup, install, notify, release, services, setup_steps, state, versions  # noqa: E402

HEALTH_URL = "http://127.0.0.1:3011/api/update"


def _env(name, default=""):
    return os.environ.get("AGENTKENNEL_" + name) or os.environ.get("AGENTDECK_" + name) or default


def installed_version(install_dir):
    try:
        return json.loads((Path(install_dir) / "web" / "version.json").read_text())["version"]
    except (OSError, ValueError, KeyError, TypeError):
        return "0.0.0"


class Job:
    """job.json as the update goes: every step with its outcome."""
    def __init__(self, udir, **fields):
        self.path = Path(udir) / state.JOB
        self.d = {"state": "running", "step": "", "steps": [], "started": time.time(),
                  "pid": os.getpid(), **fields}
        self.save()

    def save(self):
        state.write(self.path, self.d)

    def step(self, name):
        self.d["step"] = name
        self.save()

    def done(self, name, ok=True, detail=""):
        self.d["steps"].append({"step": name, "ok": ok, "detail": str(detail)[:400], "at": time.time()})
        self.save()


class Failed(Exception):
    pass


def plan_for(panel_pid, env=None):
    env = os.environ if env is None else env
    cmd = env.get("AGENTKENNEL_UPDATE_RESTART_CMD") or env.get("AGENTDECK_UPDATE_RESTART_CMD")
    if cmd:
        return {"cmd": cmd}
    own = None
    if panel_pid:
        try:
            own = services.unit_of_cgroup(open(f"/proc/{panel_pid}/cgroup").read())
        except OSError:
            own = None
    return {"units": services.panel_units(own, env)}


def check_plan(plan, refuse=None):
    if plan.get("cmd"):
        return None
    if not plan.get("units"):
        return ("the panel does not run as a systemd service here, so an update cannot restart "
                "it: set AGENTKENNEL_UPDATE_RESTART (or AGENTKENNEL_UPDATE_RESTART_CMD), or "
                "update by hand")
    refuse = refuse or services.refuse_reason
    mine = services.own_cgroup()
    for scope, unit in plan["units"]:
        why = refuse(scope, unit, own_cgroup=mine)
        if why:
            return why
    return None


def do_restart(plan):
    if plan.get("cmd"):
        return services.restart_cmd(plan["cmd"])
    done = []
    for scope, unit in plan["units"]:
        ok, how = services.restart(scope, unit)
        if not ok:
            return False, how
        done.append(how)
    return True, "; ".join(done)


def run_setup(install_dir):
    r = subprocess.run([sys.executable, "-m", "updater.setup_steps", "--install", install_dir],
                       cwd=install_dir, capture_output=True, text=True, timeout=300)
    try:
        steps = json.loads(r.stdout or "[]")
    except ValueError:
        steps = [{"step": "setup", "ok": False, "detail": (r.stderr or r.stdout)[-300:]}]
    if r.returncode != 0 and all(s.get("ok") for s in steps):
        steps.append({"step": "setup", "ok": False, "detail": (r.stderr or "")[-300:]})
    return steps


DEFAULTS = dict(check_plan=check_plan, restart=do_restart, healthy=services.healthy,
                setup=run_setup, send=notify.send, target=notify.target)


def update(install_dir, tag, auto=False, panel_pid=None, repo=None, home=None, deps=None):
    """The whole update; the result (also in last.json)."""
    dp = {**DEFAULTS, **(deps or {})}
    install_dir = os.path.abspath(install_dir)
    home = home or os.environ.get("HOME") or os.path.expanduser("~")
    repo = repo or _env("UPDATE_REPO", release.DEFAULT_REPO)
    udir = state.update_dir(install_dir)
    udir.mkdir(parents=True, exist_ok=True)
    lock = open(udir / ".lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return {"state": "busy", "error": "another update is running"}
    frm, to = installed_version(install_dir), versions.plain(tag)
    job = Job(udir, id=f"{int(time.time())}", to=to, tag=tag, auto=auto, **{"from": frm})
    ctx = {"moved": False, "restarted": False, "backup": None, "old": None, "branch": None}
    plan = plan_for(panel_pid)
    try:
        _forward(job, ctx, dp, install_dir, tag, repo, home, plan, frm, to)
        job.d["state"] = "done"
    except Failed as e:
        job.d["error"] = str(e)
        if not ctx["backup"]:
            job.d["state"] = "refused"
        else:
            _rollback(job, ctx, dp, install_dir, home, plan, frm)
    except Exception as e:                               # a bug must not leave a half update
        job.d["error"] = f"{type(e).__name__}: {e}"
        if ctx["backup"]:
            _rollback(job, ctx, dp, install_dir, home, plan, frm)
        else:
            job.d["state"] = "refused"
    job.d["finished"] = time.time()
    job.d["telegram"] = _tell(job.d, install_dir, dp)
    job.save()
    state.write(udir / state.LAST, {**job.d, "seen": False})
    lock.close()
    return job.d


def _forward(job, ctx, dp, install_dir, tag, repo, home, plan, frm, to):
    job.step("checks")
    if install.kind(install_dir) != "git":
        raise Failed("this install is not a git clone: update it as its README says")
    if not versions.newer(to, frm):
        raise Failed(f"v{to} is not newer than the running v{frm}")
    try:
        target = install.fetch_tag(install_dir, repo, tag)
        ctx["old"], ctx["branch"] = install.head(install_dir), install.branch(install_dir)
    except install.GitError as e:
        raise Failed(f"could not download {tag}: {e}")
    problems = install.preflight(install_dir, target)
    why = dp["check_plan"](plan)
    if why:
        problems.append(why)
    if problems:
        raise Failed("; ".join(problems))
    job.done("checks", True, f"{tag} = {target[:12]}")

    job.step("backup")
    ctx["backup"] = str(backup.make(install_dir, home, state.update_dir(install_dir) / state.BACKUPS,
                                    frm, ctx["old"], ctx["branch"]))
    job.d["backup"] = ctx["backup"]
    job.done("backup", True, ctx["backup"])

    job.step("code")
    ctx["moved"] = True
    try:
        install.move_to(install_dir, target)
    except install.GitError as e:
        raise Failed(str(e))
    job.done("code", True, f"{ctx['old'][:12]} -> {target[:12]}")
    job.d["notes"] = versions.excerpt(_read(Path(install_dir) / "CHANGELOG.md"), frm, to, 3000)
    rec_dir = setup_steps.install_record().get("DIR")
    if (rec_dir and os.path.realpath(rec_dir) == os.path.realpath(install_dir)
            and install.changed(install_dir, ctx["old"], target, "install.sh")):
        job.d["todo"] = [f"install.sh changed in this release: run {install_dir}/install.sh once "
                         "more to finish (it may ask for your sudo password)"]

    job.step("setup")
    for s in dp["setup"](install_dir):
        job.done(f"setup: {s.get('step')}", bool(s.get("ok")), s.get("detail", ""))
        if not s.get("ok"):
            raise Failed(f"setup step {s.get('step')!r} failed: {s.get('detail', '')[:200]}")

    job.step("restart")
    ctx["restarted"] = True
    ok, how = dp["restart"](plan)
    job.done("restart", ok, how)
    if not ok:
        raise Failed(how)

    job.step("health check")
    ok, why = dp["healthy"](_env("UPDATE_HEALTH_URL", HEALTH_URL), to,
                            timeout=int(_env("UPDATE_HEALTH_TIMEOUT", "90")))
    job.done("health check", ok, why)
    if not ok:
        raise Failed(why)


def _rollback(job, ctx, dp, install_dir, home, plan, frm):
    failed_at = job.d.get("step")
    job.step("rollback")
    errors = []
    try:
        if ctx["moved"]:
            install.move_back(install_dir, ctx["old"], ctx["branch"])
        backup.restore_config(ctx["backup"], install_dir, home)
    except Exception as e:
        errors.append(f"{type(e).__name__}: {e}")
    if not errors and ctx["restarted"]:
        ok, how = dp["restart"](plan)
        if ok:
            ok, how = dp["healthy"](_env("UPDATE_HEALTH_URL", HEALTH_URL), frm,
                                    timeout=int(_env("UPDATE_HEALTH_TIMEOUT", "90")))
        if not ok:
            errors.append(how)
    job.done("rollback", not errors, "; ".join(errors) or f"back at v{frm}")
    job.d["step"] = failed_at
    job.d["state"] = "rollback-failed" if errors else "rolled-back"
    if errors:
        job.d["rollback_error"] = "; ".join(errors)[:400]


def _read(p):
    try:
        return Path(p).read_text()
    except OSError:
        return ""


def _tell(result, install_dir, dp):
    t = dp["target"](install_dir)
    if not t:
        return "not set up"
    ok, why = dp["send"](t[0], t[1], notify.message(result))
    return "sent" if ok else f"failed: {why}"


def main(argv=None):
    p = argparse.ArgumentParser(description="Update this AgentKennel install to a release.")
    p.add_argument("--install", default=str(Path(__file__).resolve().parent.parent))
    p.add_argument("--to", required=True, help="the release tag, e.g. v2.8.0")
    p.add_argument("--auto", action="store_true", help="started by the night-time patch update")
    p.add_argument("--panel-pid", type=int, default=None)
    a = p.parse_args(argv)
    r = update(a.install, a.to, auto=a.auto, panel_pid=a.panel_pid)
    print(json.dumps({k: r.get(k) for k in ("state", "from", "to", "error", "step")}))
    return {"done": 0, "refused": 2, "busy": 3}.get(r.get("state"), 1)


if __name__ == "__main__":
    sys.exit(main())
