"""The update from start to end on a made-up upstream, with the restart, the health check and
Telegram replaced: what changes, what is refused before anything changes, and the rollback."""
import json
import os

import pytest

from tests.update_fixtures import git, install_at, upstream
from updater import apply as A
from updater import install as I


@pytest.fixture
def env(tmp_path, monkeypatch):
    bare, src = upstream(tmp_path)
    d = install_at(tmp_path, bare, "v2.7.2")
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text('{"v": "old"}')
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("AGENTKENNEL_CONF", str(tmp_path / "no-record.env"))
    monkeypatch.setenv("AGENTKENNEL_UPDATE_REPO", str(bare))
    monkeypatch.delenv("AGENTKENNEL_UPDATE_DIR", raising=False)
    return bare, d, home


class Fake:
    def __init__(self, healthy=True, restart=True, plan_ok=True, setup_ok=True, tell=True):
        self.calls = []
        self._healthy, self._restart, self._plan, self._setup = healthy, restart, plan_ok, setup_ok
        self._tell = tell

    def deps(self):
        return dict(
            check_plan=lambda plan: None if self._plan else "agents-x.service holds the terminals",
            restart=lambda plan: (self.calls.append("restart") or (self._restart, "restarted")),
            healthy=self.healthy,
            setup=lambda d: [{"step": "guard hooks", "ok": self._setup, "detail": "d"}],
            target=lambda d: ("1:t", "42") if self._tell else None,
            send=lambda tok, chat, text: (self.calls.append(("tg", text)) or (True, "")))

    def healthy(self, url, version, timeout=90):
        self.calls.append(("health", version))
        ok = self._healthy(version) if callable(self._healthy) else self._healthy
        return ok, "fine" if ok else f"v{version} does not answer"


def version_of(d):
    return json.loads((d / "web" / "version.json").read_text())["version"]


def test_a_good_update(env):
    bare, d, home = env
    f = Fake()
    r = A.update(str(d), "v2.8.0", deps=f.deps())
    assert r["state"] == "done", r
    assert version_of(d) == "2.8.0" and I.branch(str(d)) == "master"
    assert [s["step"] for s in r["steps"]] == ["checks", "backup", "code", "setup: guard hooks",
                                               "restart", "health check"]
    assert ("health", "2.8.0") in f.calls
    assert [x["version"] for x in r["notes"]] == ["2.8.0", "2.7.3"]
    assert os.path.isdir(r["backup"])
    last = json.loads((d / ".sessions" / "update" / "last.json").read_text())
    assert last["state"] == "done" and last["seen"] is False
    tg = [c for c in f.calls if c[0] == "tg"]
    assert len(tg) == 1 and "v2.7.2 → v2.8.0" in tg[0][1] and r["telegram"] == "sent"


def test_a_failed_health_check_rolls_everything_back(env):
    bare, d, home = env
    old = I.head(str(d))
    f = Fake(healthy=lambda v: v == "2.7.2")          # the new version never answers
    def setup(dirname):                               # the setup changes a setting
        (home / ".claude" / "settings.json").write_text('{"v": "new"}')
        return [{"step": "guard hooks", "ok": True, "detail": ""}]
    r = A.update(str(d), "v2.8.0", deps={**f.deps(), "setup": setup})
    assert r["state"] == "rolled-back", r
    assert r["step"] == "health check"
    assert I.head(str(d)) == old and version_of(d) == "2.7.2" and I.branch(str(d)) == "master"
    assert (home / ".claude" / "settings.json").read_text() == '{"v": "old"}'
    assert f.calls.count("restart") == 2                     # forward, and back
    assert ("health", "2.7.2") in f.calls
    tg = [c[1] for c in f.calls if isinstance(c, tuple) and c[0] == "tg"]
    assert len(tg) == 1 and "failed" in tg[0] and "Rolled back" in tg[0]


def test_a_failed_setup_step_rolls_back_without_restarting(env):
    bare, d, home = env
    f = Fake(setup_ok=False)
    r = A.update(str(d), "v2.8.0", deps=f.deps())
    assert r["state"] == "rolled-back" and version_of(d) == "2.7.2"
    assert "restart" not in f.calls


def test_a_rollback_that_fails_says_so(env):
    bare, d, home = env
    f = Fake(healthy=False)                               # neither version answers
    r = A.update(str(d), "v2.8.0", deps=f.deps())
    assert r["state"] == "rollback-failed" and r["rollback_error"]
    assert version_of(d) == "2.7.2"                        # the code is back all the same


def test_changed_files_refuse_before_anything_changes(env):
    bare, d, home = env
    (d / "CHANGELOG.md").write_text("mine")
    f = Fake()
    r = A.update(str(d), "v2.8.0", deps=f.deps())
    assert r["state"] == "refused" and "CHANGELOG.md" in r["error"]
    assert not r.get("backup") and "restart" not in f.calls
    assert (d / "CHANGELOG.md").read_text() == "mine"
    assert any(c[0] == "tg" for c in f.calls if isinstance(c, tuple))   # told, even so


def test_a_panel_that_cannot_be_restarted_safely_is_refused(env):
    bare, d, home = env
    f = Fake(plan_ok=False)
    r = A.update(str(d), "v2.8.0", deps=f.deps())
    assert r["state"] == "refused" and "holds the terminals" in r["error"]
    assert version_of(d) == "2.7.2" and "restart" not in f.calls


def test_not_newer_is_refused(env):
    bare, d, home = env
    r = A.update(str(d), "v2.7.2", deps=Fake().deps())
    assert r["state"] == "refused" and "not newer" in r["error"]


def test_a_missing_tag_is_refused(env):
    bare, d, home = env
    r = A.update(str(d), "v9.9.9", deps=Fake().deps())
    assert r["state"] == "refused" and "download" in r["error"]


def test_one_update_at_a_time(env):
    import fcntl
    bare, d, home = env
    lockdir = d / ".sessions" / "update"
    lockdir.mkdir(parents=True)
    with open(lockdir / ".lock", "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        r = A.update(str(d), "v2.8.0", deps=Fake().deps())
    assert r["state"] == "busy" and version_of(d) == "2.7.2"


def test_no_telegram_no_message(env):
    bare, d, home = env
    r = A.update(str(d), "v2.7.3", deps=Fake(tell=False).deps())
    assert r["state"] == "done" and r["telegram"] == "not set up"


def test_the_restart_plan_comes_from_the_panels_unit_or_a_command(monkeypatch):
    monkeypatch.setenv("AGENTKENNEL_UPDATE_RESTART_CMD", "restart-it")
    assert A.plan_for(None) == {"cmd": "restart-it"}
    monkeypatch.delenv("AGENTKENNEL_UPDATE_RESTART_CMD")
    monkeypatch.setenv("AGENTKENNEL_UPDATE_RESTART", "user:a.service")
    assert A.plan_for(None) == {"units": [("user", "a.service")]}
    monkeypatch.delenv("AGENTKENNEL_UPDATE_RESTART")
    assert A.plan_for(None) == {"units": []}
    assert "systemd service" in A.check_plan({"units": []})
