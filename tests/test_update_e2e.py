"""The update end to end, for real: an install of this very code at a made-up v2.7.2, cloned
from a local bare repo with release tags; its real panel (status_server's Handler) running
as a process; `python3 -m updater.apply` moves it to v2.7.3, restarts that panel and asks it
which version it runs. A broken release (the panel can't start) is rolled back and the old
panel answers again. Nothing here touches this checkout, the live panel or systemd: the
restart is a command of its own (AGENTKENNEL_UPDATE_RESTART_CMD)."""
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from tests.update_fixtures import commit_release, git, install_at, upstream

ROOT = Path(__file__).resolve().parents[1]

SERVE = """import os, sys, threading, time
sys.path.insert(0, os.getcwd())
import status_server
from http.server import HTTPServer
srv = HTTPServer(("127.0.0.1", int(sys.argv[1])), status_server.Handler)
stop = sys.argv[2] + ".stop"
open(sys.argv[2], "w").write(str(os.getpid()))
threading.Thread(target=srv.serve_forever, daemon=True).start()
while not os.path.exists(stop):
    time.sleep(0.1)
srv.shutdown()
srv.server_close()
os.remove(stop)
"""

# a stop file, not a signal: the test sandbox's processes ignore SIGTERM
RESTART = """#!/bin/bash
cd "$E2E_INSTALL" || exit 1
if [ -s "$E2E_PIDFILE" ]; then
  touch "$E2E_PIDFILE.stop"
  for i in $(seq 50); do [ -e "$E2E_PIDFILE.stop" ] || break; sleep 0.1; done
  rm -f "$E2E_PIDFILE.stop" "$E2E_PIDFILE"
fi
setsid "$E2E_PY" "$E2E_SERVE" "$E2E_PORT" "$E2E_PIDFILE" >>"$E2E_LOG" 2>&1 </dev/null &
"""


def code_files():
    out = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "-z"], cwd=ROOT,
                         capture_output=True, check=True).stdout.decode().split("\0")
    skip = (".png", ".gif", ".mp4", ".jpg", ".webp")
    return [f for f in out if f and not f.startswith(("docs/", "tests/artifacts/"))
            and not f.endswith(skip) and (ROOT / f).is_file()]


def free_port():
    with socket.socket() as s:            # the panel is another process: it binds it later
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def get(url):
    with urllib.request.urlopen(url, timeout=3) as r:
        return json.loads(r.read())


def wait_running(url, want, timeout=30):
    end, last = time.time() + timeout, None
    while time.time() < end:
        try:
            last = get(url).get("running")
            if last == want:
                return last
        except OSError:
            pass
        time.sleep(0.3)
    return last


@pytest.fixture
def e2e(tmp_path):
    base = tmp_path / "base"
    for f in code_files():
        (base / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / f, base / f)
    bare, src = upstream(tmp_path, releases=("2.7.2", "2.7.3"), base=base)
    d = install_at(tmp_path, bare, "v2.7.2")
    (tmp_path / "serve.py").write_text(SERVE)
    (tmp_path / "restart.sh").write_text(RESTART)
    (tmp_path / "restart.sh").chmod(0o755)
    (tmp_path / "home").mkdir()
    port = free_port()
    env = {**os.environ,
           "HOME": str(tmp_path / "home"),
           "AGENTKENNEL_CONF": str(tmp_path / "no-record.env"),
           "AGENTKENNEL_TG_CONF": str(tmp_path / "no-telegram.json"),
           "AGENTKENNEL_UPDATE_REPO": str(bare),
           "AGENTKENNEL_UPDATE_RESTART_CMD": str(tmp_path / "restart.sh"),
           "AGENTKENNEL_UPDATE_HEALTH_URL": f"http://127.0.0.1:{port}/api/update",
           "AGENTKENNEL_UPDATE_HEALTH_TIMEOUT": "20",
           "AGENTKENNEL_LIBRARY": str(tmp_path / "library.json"),
           "E2E_INSTALL": str(d), "E2E_PIDFILE": str(tmp_path / "panel.pid"),
           "E2E_PY": sys.executable, "E2E_SERVE": str(tmp_path / "serve.py"),
           "E2E_PORT": str(port), "E2E_LOG": str(tmp_path / "panel.log")}
    for k in ("AGENTKENNEL_UPDATE_DIR", "TG_BRIDGE_TOKEN", "TG_BRIDGE_OWNER"):
        env.pop(k, None)                  # a dashboard terminal's own bot never gets a test's message
    subprocess.run([str(tmp_path / "restart.sh")], env=env, check=True)     # the panel, v2.7.2
    url = env["AGENTKENNEL_UPDATE_HEALTH_URL"]
    assert wait_running(url, "2.7.2") == "2.7.2", (tmp_path / "panel.log").read_text()[-2000:]
    yield {"dir": d, "env": env, "url": url, "src": src, "bare": bare, "tmp": tmp_path}
    (tmp_path / "panel.pid.stop").touch()                 # the panel we started stops


def log_of(e):
    return (e["tmp"] / "panel.log").read_text()[-3000:]


def apply(e, tag):
    return subprocess.run([sys.executable, "-m", "updater.apply", "--install", str(e["dir"]),
                           "--to", tag], cwd=e["dir"], env=e["env"], capture_output=True,
                          text=True, timeout=180)


def version_of(d):
    return json.loads((d / "web" / "version.json").read_text())["version"]


def test_update_to_a_new_release_for_real(e2e):
    r = apply(e2e, "v2.7.3")
    assert r.returncode == 0, r.stdout + r.stderr + log_of(e2e)
    assert version_of(e2e["dir"]) == "2.7.3"
    assert get(e2e["url"])["running"] == "2.7.3"            # the restarted panel is the new one
    last = json.loads((e2e["dir"] / ".sessions" / "update" / "last.json").read_text())
    assert last["state"] == "done" and last["telegram"] == "not set up"
    assert [s["step"] for s in last["steps"]] == ["checks", "backup", "code", "restart", "health check"]
    assert Path(last["backup"], "code.tar.gz").is_file()
    assert git(e2e["dir"], "symbolic-ref", "--short", "HEAD") == "master"


def test_a_release_whose_panel_does_not_start_is_rolled_back(e2e):
    src = e2e["src"]
    commit_release(src, "2.7.4", ["2.7.2", "2.7.3", "2.7.4"],
                   extra={"backend/__init__.py": "raise SystemExit('a broken release')\n"})
    git(src, "push", "-q", str(e2e["bare"]), "master", "v2.7.4")
    r = apply(e2e, "v2.7.4")
    assert r.returncode == 1, r.stdout + r.stderr
    last = json.loads((e2e["dir"] / ".sessions" / "update" / "last.json").read_text())
    assert last["state"] == "rolled-back", last
    assert last["step"] == "health check"
    assert version_of(e2e["dir"]) == "2.7.2"
    assert wait_running(e2e["url"], "2.7.2") == "2.7.2"     # the old panel is back
