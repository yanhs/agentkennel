"""The dashboard's side of updates (backend/updates.py): GET /api/update for the panel, the
"Update" button refused while an agent works (or queued until all are idle), the update
started in a process of its own, and the opt-in night-time patch update."""
import json
import time

import pytest

from updater import state as S


@pytest.fixture
def U(tmp_path, monkeypatch):
    import status_server as ss
    from backend import updates
    d = tmp_path / "update"
    monkeypatch.setenv("AGENTKENNEL_UPDATE_DIR", str(d))
    monkeypatch.setattr(updates, "upd_installed", lambda: "2.7.2")
    monkeypatch.setattr(updates, "RUNNING_VERSION", "2.7.2")
    monkeypatch.setattr(updates, "upd_kind", lambda: "git")
    monkeypatch.setattr(updates, "upd_busy", lambda: [])
    launched = []
    monkeypatch.setattr(updates, "upd_spawn", lambda tag, auto=False: launched.append((tag, auto)) or "test")
    updates._launched = launched
    updates._dir = d
    return updates


def latest(U, version="2.8.0", changelog=None, **kw):
    log = changelog or ("## v2.8.0 — updates\n\n- One click.\n\n## v2.7.3 — fix\n\n- Fixed.\n\n"
                        "## v2.7.2 — old\n\n- Old.\n")
    S.write(U._dir / S.LATEST, {"tag": f"v{version}", "version": version, "changelog": log,
                                "checked_at": 100, "next_at": time.time() + 999, **kw})


def test_nothing_known_nothing_available(U):
    st = U.upd_state()
    assert st["kind"] == "git" and st["current"] == "2.7.2" and st["running"] == "2.7.2"
    assert st["available"] is False and st["latest"] is None


def test_a_newer_release_comes_with_what_changed(U):
    latest(U)
    st = U.upd_state()
    assert st["available"] and st["latest"] == "2.8.0" and st["tag"] == "v2.8.0"
    assert st["patch"] is False
    assert [n["version"] for n in st["notes"]] == ["2.8.0", "2.7.3"]
    assert st["auto"] is False                         # off until turned on


def test_off_shows_nothing_but_the_running_version(U, monkeypatch):
    monkeypatch.setattr(U, "upd_kind", lambda: "off")
    latest(U)
    assert U.upd_state() == {"kind": "off", "running": "2.7.2"}


def test_docker_shows_the_command(U, monkeypatch):
    monkeypatch.setattr(U, "upd_kind", lambda: "docker")
    latest(U)
    st = U.upd_state()
    assert st["available"] and "docker compose pull" in st["command"]


def test_start_launches_the_update(U):
    latest(U)
    st = U.upd_post("start", {"tag": "v2.8.0"})
    assert U._launched == [("v2.8.0", False)]
    assert st["job"]["state"] == "starting" and st["job"]["to"] == "2.8.0"


def test_start_is_refused_while_an_agent_works_and_says_who(U, monkeypatch):
    latest(U)
    monkeypatch.setattr(U, "upd_busy", lambda: ["blog fixes", "4f2a"])
    with pytest.raises(U.LibError) as e:
        U.upd_post("start", {"tag": "v2.8.0"})
    assert e.value.code == 409 and e.value.extra["busy"] == ["blog fixes", "4f2a"]
    assert U._launched == []


def test_when_idle_waits_and_the_scheduler_starts_it_once_all_are_idle(U, monkeypatch):
    latest(U)
    busy = ["blog fixes"]
    monkeypatch.setattr(U, "upd_busy", lambda: list(busy))
    st = U.upd_post("start", {"tag": "v2.8.0", "when_idle": True})
    assert st["pending"]["tag"] == "v2.8.0" and U._launched == []
    sch = U.UpdateScheduler(check=lambda: None)
    sch.tick()
    assert U._launched == []                           # still working
    busy.clear()
    sch.tick()
    assert U._launched == [("v2.8.0", False)]
    assert U.upd_state()["pending"] is None


def test_cancel_drops_the_waiting_update(U, monkeypatch):
    latest(U)
    monkeypatch.setattr(U, "upd_busy", lambda: ["x"])
    U.upd_post("start", {"tag": "v2.8.0", "when_idle": True})
    assert U.upd_post("cancel", {})["pending"] is None


def test_only_the_known_latest_release_and_only_newer(U):
    latest(U)
    for tag in ("v2.9.0", "v2.7.2", "../x", None):
        with pytest.raises(U.LibError):
            U.upd_post("start", {"tag": tag})
    assert U._launched == []


def test_one_update_at_a_time(U):
    latest(U)
    U.upd_post("start", {"tag": "v2.8.0"})
    with pytest.raises(U.LibError) as e:
        U.upd_post("start", {"tag": "v2.8.0"})
    assert e.value.code == 409


def test_not_on_docker_or_manual_installs(U, monkeypatch):
    latest(U)
    monkeypatch.setattr(U, "upd_kind", lambda: "docker")
    with pytest.raises(U.LibError):
        U.upd_post("start", {"tag": "v2.8.0"})


def test_a_lost_update_process_is_reported(U):
    S.write(U._dir / S.JOB, {"state": "running", "to": "2.8.0", "pid": 999999999,
                              "started": time.time() - 3600, "step": "code"})
    st = U.upd_state()
    assert st["job"]["state"] == "lost"


def test_the_result_is_shown_until_seen(U):
    S.write(U._dir / S.LAST, {"state": "done", "from": "2.7.2", "to": "2.8.0", "seen": False})
    assert U.upd_state()["last"]["state"] == "done"
    U.upd_post("seen", {})
    assert U.upd_state()["last"] is None


def test_auto_setting(U):
    assert U.upd_post("auto", {"enabled": True})["auto"] is True
    assert json.loads((U._dir / S.SETTINGS).read_text())["auto_patch"] is True
    assert U.upd_post("auto", {"enabled": False})["auto"] is False
    with pytest.raises(U.LibError):
        U.upd_post("auto", {"enabled": "yes"})


def test_check_now_asks_for_the_latest_release(U, monkeypatch):
    asked = []
    monkeypatch.setattr(U, "upd_check", lambda by_hand=False: asked.append(by_hand))
    U.upd_post("check", {})
    assert asked == [True]


# ── the night window ─────────────────────────────────────────────────────────────────
def at(hour, day=20):
    return time.mktime((2026, 11, day, hour, 30, 0, 0, 0, -1))


def night(U, monkeypatch, hour, version="2.7.3", auto=True, busy=()):
    latest(U, version=version)
    if auto:
        U.upd_post("auto", {"enabled": True})
    monkeypatch.setattr(U, "upd_busy", lambda: list(busy))
    sch = U.UpdateScheduler(check=lambda: None, clock=lambda: at(hour))
    sch.tick()
    return sch


def test_a_patch_release_is_installed_at_night_when_turned_on(U, monkeypatch):
    sch = night(U, monkeypatch, 3)
    assert U._launched == [("v2.7.3", True)]
    sch.tick()                                         # once a night
    assert U._launched == [("v2.7.3", True)]


def test_not_by_day(U, monkeypatch):
    night(U, monkeypatch, 14)
    assert U._launched == []


def test_not_when_turned_off(U, monkeypatch):
    night(U, monkeypatch, 3, auto=False)
    assert U._launched == []


def test_never_a_minor_or_major_release(U, monkeypatch):
    night(U, monkeypatch, 3, version="2.8.0")
    assert U._launched == []


def test_not_while_an_agent_works(U, monkeypatch):
    night(U, monkeypatch, 3, busy=["x"])
    assert U._launched == []


def test_a_release_that_failed_at_night_is_not_tried_again(U, monkeypatch):
    S.write(U._dir / S.LAST, {"state": "rolled-back", "auto": True, "to": "2.7.3", "seen": True})
    night(U, monkeypatch, 3)
    assert U._launched == []


# ── how the update process is started ──────────────────────────────────────────────────
@pytest.fixture
def spawn(tmp_path, monkeypatch):
    import status_server  # noqa: F401  (loads backend/ afresh)
    from backend import updates
    monkeypatch.setenv("AGENTKENNEL_UPDATE_DIR", str(tmp_path))
    monkeypatch.setenv("AGENTKENNEL_UPDATE_REPO", "/srv/mirror.git")
    monkeypatch.setenv("AGENTKENNEL_PASSWORD", "not-for-the-update")
    calls = {"run": [], "popen": []}

    class P:
        def __init__(self, cmd, **kw):
            calls["popen"].append((cmd, kw))
    monkeypatch.setattr(updates.subprocess, "Popen", P)
    return updates, calls


def test_under_a_user_unit_the_update_gets_a_unit_of_its_own(spawn, monkeypatch):
    U, calls = spawn
    monkeypatch.setattr(U, "_user_unit", lambda: True)
    def run(cmd, **kw):
        calls["run"].append(cmd)
        import subprocess
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(U.subprocess, "run", run)
    how = U.upd_spawn("v2.8.0")
    cmd = calls["run"][0]
    assert cmd[:4] == ["systemd-run", "--user", "--collect", "--quiet"] and how.startswith("agentkennel-update-")
    assert "--setenv=AGENTKENNEL_UPDATE_REPO=/srv/mirror.git" in cmd
    assert not any("AGENTKENNEL_PASSWORD" in a for a in cmd)          # secrets stay in the panel
    i = cmd.index("-m")
    assert cmd[i:i + 6] == ["-m", "updater.apply", "--install", U._HERE, "--to", "v2.8.0"]
    assert calls["popen"] == []


def test_otherwise_a_detached_child_in_a_session_of_its_own(spawn, monkeypatch):
    U, calls = spawn
    monkeypatch.setattr(U, "_user_unit", lambda: False)
    assert U.upd_spawn("v2.8.0", auto=True) == "detached"
    cmd, kw = calls["popen"][0]
    assert kw["start_new_session"] is True and cmd[-1] == "--auto"
