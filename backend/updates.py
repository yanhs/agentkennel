"""Updates in the dashboard: GET/POST /api/update, the daily check for a new release, the
"update when all agents are idle" queue and the opt-in night-time patch update.

The update itself runs in a process of its own (updater/apply.py), so it outlives the
restart of this one: under a systemd user unit it gets a transient unit of its own
(systemd-run --user); otherwise it is a detached child (install.sh's agentkennel-status
has KillMode=process, so a restart stops only this process).

Part of status_server (the facade re-exports every name here)."""

import json
import os
import subprocess
import sys
import threading
import time

import envcompat
from updater import install as upd_install
from updater import release as upd_release
from updater import services as upd_services
from updater import state as upd_files
from updater import versions as upd_versions

from backend import _HERE
from backend.library_api import LibError, lib_busy_reason, lib_live, lib_path, lib_tmux, _LEGACY_TMUX
from backend.quota import Quota
from backend.terminals import sample_working
import library

UPD_ROUTE = "/api/update"
UPD_DOCKER_CMD = "docker compose pull && docker compose up -d"
NIGHT_HOURS = (2, 5)          # the night-time patch update: from 02:00 to 05:00, server time
LOST_AFTER = 120              # s: a job whose process is gone this long is reported lost


def upd_installed():
    """The version of the code on disk (web/version.json)."""
    try:
        with open(os.path.join(_HERE, "web", "version.json")) as f:
            return json.load(f)["version"]
    except (OSError, ValueError, KeyError, TypeError):
        return "0.0.0"


RUNNING_VERSION = upd_installed()      # what this process loaded: the health check asks it


def upd_dir():
    return upd_files.update_dir(_HERE)


def upd_path(name):
    return upd_dir() / name


def upd_kind():
    """git | docker | manual | off. A guest's sandbox (behind a turnstile) is off: its
    updates are the owner's."""
    if Quota.source() is not None:
        return "off"
    return upd_install.kind(_HERE)


def upd_repo():
    return envcompat.get("UPDATE_REPO") or upd_release.DEFAULT_REPO


def upd_check(by_hand=False):
    cache = upd_files.read(upd_path(upd_files.LATEST), {})
    new = upd_release.check(cache, upd_repo(), _HERE, by_hand=by_hand)
    if new != cache:
        upd_files.write(upd_path(upd_files.LATEST), new)
    return new


def _alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def upd_job():
    """The update running now, None when there is none (a finished one is in last.json)."""
    job = upd_files.read(upd_path(upd_files.JOB), None)
    if not isinstance(job, dict) or job.get("state") not in ("starting", "running"):
        return None
    if time.time() - (job.get("started") or 0) > LOST_AFTER and not _alive(job.get("pid")):
        job = {**job, "state": "lost",
               "error": "the update process stopped without a result; see .sessions/update/update.log"}
    return job


def upd_settings():
    s = upd_files.read(upd_path(upd_files.SETTINGS), {})
    return s if isinstance(s, dict) else {}


def upd_state():
    kind = upd_kind()
    if kind == "off":
        return {"kind": "off", "running": RUNNING_VERSION}
    cur = upd_installed()
    cache = upd_files.read(upd_path(upd_files.LATEST), {}) or {}
    latest = cache.get("version")
    available = bool(latest) and upd_versions.newer(latest, cur)
    last = upd_files.read(upd_path(upd_files.LAST), None)
    d = {"kind": kind, "current": cur, "running": RUNNING_VERSION,
         "latest": latest, "tag": cache.get("tag"), "available": available,
         "patch": available and upd_versions.is_patch(cur, latest),
         "notes": upd_versions.excerpt(cache.get("changelog"), cur, latest) if available else [],
         "checked_at": cache.get("checked_at"), "error": cache.get("error") or "",
         "auto": bool(upd_settings().get("auto_patch")),
         "pending": upd_files.read(upd_path(upd_files.PENDING), None),
         "job": upd_job(),
         "last": last if isinstance(last, dict) and not last.get("seen") else None}
    if kind == "docker":
        d["command"] = UPD_DOCKER_CMD
    elif kind == "manual":
        d["command"] = (f"download {cache.get('tag') or 'the new release'} from "
                        "https://github.com/yanhs/agentkennel and run install.sh")
    return d


def upd_busy():
    """Names of the terminals at work: loaded library terminals with a reason not to be
    interrupted (printing, a hold, a background task, CPU), and numbered ones on CPU."""
    live = lib_live()
    pids = {sid: i["pane_pid"] for sid, i in live.items()}
    r = lib_tmux("list-panes", "-a", "-F", "#{session_name}\t#{pane_pid}")
    for line in r.stdout.splitlines() if r.returncode == 0 else []:
        name, _, pid = line.partition("\t")
        if _LEGACY_TMUX.fullmatch(name) and pid.isdigit():
            pids.setdefault("\0" + name, int(pid))
    working = sample_working(pids)
    try:
        names = {e["id"]: e.get("name") or e["id"] for e in library.load(lib_path())["sessions"]}
    except Exception:
        names = {}
    busy = [names.get(sid, sid) for sid, info in live.items()
            if lib_busy_reason(sid, info, working.get(sid, False))]
    busy += [k[1:] for k, w in working.items() if k.startswith("\0") and w]
    return busy


def _user_unit():
    """This process runs in a systemd user unit (then the update gets a unit of its own)."""
    own = upd_services.unit_of_cgroup(open("/proc/self/cgroup").read()) \
        if os.path.exists("/proc/self/cgroup") else None
    return bool(own and own[0] == "user")


# what the update reads from the environment (the rest of the panel's, secrets included, stays here)
_PASS_ENV = tuple(p + k for p in ("AGENTKENNEL_", "AGENTDECK_")
                  for k in ("UPDATE", "CONF", "TG_CONF", "GUARDS"))


def upd_spawn(tag, auto=False):
    """Start updater/apply.py so that it outlives this process; how it was started."""
    cmd = [sys.executable, "-m", "updater.apply", "--install", _HERE, "--to", tag,
           "--panel-pid", str(os.getpid())] + (["--auto"] if auto else [])
    log = str(upd_path("update.log"))
    if _user_unit():
        unit = f"agentkennel-update-{int(time.time())}"
        env = [f"--setenv={k}={v}" for k, v in os.environ.items()
               if k.startswith(_PASS_ENV) or k in ("PATH", "HOME", "LANG")]
        r = subprocess.run(["systemd-run", "--user", "--collect", "--quiet", f"--unit={unit}",
                            f"--working-directory={_HERE}", f"--property=StandardOutput=append:{log}",
                            f"--property=StandardError=append:{log}", *env, *cmd],
                           capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            return unit
    with open(log, "a") as out:
        subprocess.Popen(cmd, cwd=_HERE, stdin=subprocess.DEVNULL, stdout=out, stderr=out,
                         start_new_session=True)
    return "detached"


_LOCK = threading.Lock()


def upd_launch(tag, auto=False):
    """Start an update to `tag` (the caller checked it); a placeholder job until it runs."""
    upd_dir().mkdir(parents=True, exist_ok=True)
    upd_files.write(upd_path(upd_files.JOB), {
        "state": "starting", "to": upd_versions.plain(tag), "from": upd_installed(), "tag": tag,
        "auto": auto, "started": time.time(), "step": "starting", "steps": []})
    upd_files.remove(upd_path(upd_files.PENDING))
    try:
        how = upd_spawn(tag, auto)
    except Exception as e:
        upd_files.write(upd_path(upd_files.JOB), {"state": "failed", "to": upd_versions.plain(tag),
                                                  "error": f"could not start the update: {e}"})
        raise LibError(500, f"could not start the update: {e}")
    with open(upd_path("update.log"), "a") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} update to {tag} started ({how})\n")


def _wanted_tag(body):
    cache = upd_files.read(upd_path(upd_files.LATEST), {}) or {}
    tag = body.get("tag")
    if not isinstance(tag, str) or tag != cache.get("tag") or not upd_versions.parse(tag):
        raise LibError(400, "that is not the latest release; check again")
    if not upd_versions.newer(tag, upd_installed()):
        raise LibError(400, "that release is not newer than this install")
    return tag


def upd_post(what, body):
    if what == "check":
        upd_check(by_hand=True)
    elif what == "start":
        if upd_kind() != "git":
            raise LibError(400, "this install is not updated from the panel")
        tag = _wanted_tag(body)
        with _LOCK:
            if upd_job():
                raise LibError(409, "an update is running already")
            if body.get("when_idle") is True:
                upd_files.write(upd_path(upd_files.PENDING),
                                {"tag": tag, "requested": time.time(), "why": "idle"})
            else:
                busy = upd_busy()
                if busy:
                    raise LibError(409, "agents are working", busy=busy)
                upd_launch(tag)
    elif what == "cancel":
        upd_files.remove(upd_path(upd_files.PENDING))
    elif what == "auto":
        on = body.get("enabled")
        if not isinstance(on, bool):
            raise LibError(400, "enabled must be true or false")
        upd_files.write(upd_path(upd_files.SETTINGS), {**upd_settings(), "auto_patch": on})
    elif what == "seen":
        last = upd_files.read(upd_path(upd_files.LAST), None)
        if isinstance(last, dict):
            upd_files.write(upd_path(upd_files.LAST), {**last, "seen": True})
    else:
        raise LibError(404, "not found")
    return upd_state()


class UpdateScheduler:
    """Once a minute: the daily release check, a queued "when all are idle" update, and
    the night-time patch update (off unless turned on)."""
    def __init__(self, check=None, clock=None, every=60):
        self.check = check or (lambda: upd_check())
        self.clock = clock or time.time
        self.every = every

    def tick(self):
        kind = upd_kind()
        if kind == "off":
            return
        self.check()                                   # at most once a day (release.py)
        if kind != "git":
            return
        with _LOCK:
            if upd_job():
                return
            pending = upd_files.read(upd_path(upd_files.PENDING), None)
            if isinstance(pending, dict) and pending.get("tag"):
                if upd_versions.newer(pending["tag"], upd_installed()) and not upd_busy():
                    upd_launch(pending["tag"])
                return
            self._night()

    def _night(self):
        now = self.clock()
        t = time.localtime(now)
        if not (NIGHT_HOURS[0] <= t.tm_hour < NIGHT_HOURS[1]):
            return
        s = upd_settings()
        night = time.strftime("%Y-%m-%d", t)
        if not s.get("auto_patch") or s.get("last_night") == night:
            return
        cache = upd_files.read(upd_path(upd_files.LATEST), {}) or {}
        cur, latest = upd_installed(), cache.get("version")
        if not (latest and upd_versions.is_patch(cur, latest)):
            return
        last = upd_files.read(upd_path(upd_files.LAST), {}) or {}
        if last.get("auto") and last.get("to") == latest and last.get("state") != "done":
            return                                     # failed once: by hand from now on
        if upd_busy():
            return                                     # next minute, while the night lasts
        upd_files.write(upd_path(upd_files.SETTINGS), {**s, "last_night": night})
        upd_launch(cache["tag"], auto=True)

    def run(self):
        time.sleep(30)
        while True:
            try:
                self.tick()
            except Exception as e:                     # never kills the dashboard
                print(f"updates: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
            time.sleep(self.every)

    def start(self):
        threading.Thread(target=self.run, daemon=True, name="updates").start()
