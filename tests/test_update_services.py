"""Restarting the panel after an update: only the panel's own services, never one that holds
the terminals (the owner's rule: work in the terminals is never interrupted), and a
health check that the new version is the one answering."""
import json
import signal
import subprocess

import pytest

from updater import services as S

USER_CG = "0::/user.slice/user-1000.slice/user@1000.service/app.slice/agents-status-server.service\n"
SYS_CG = "0::/system.slice/agentkennel-status.service\n"


def test_the_panels_own_unit_is_read_from_its_cgroup():
    assert S.unit_of_cgroup(USER_CG) == ("user", "agents-status-server.service")
    assert S.unit_of_cgroup(SYS_CG) == ("system", "agentkennel-status.service")
    v1 = "12:pids:/x\n1:name=systemd:/system.slice/agentkennel-status.service\n"
    assert S.unit_of_cgroup(v1) == ("system", "agentkennel-status.service")
    assert S.unit_of_cgroup("0::/user.slice/user-1000.slice/session-5.scope\n") is None
    assert S.unit_of_cgroup("") is None


def test_which_units_are_the_panels():
    assert S.panel_units(("system", "agentkennel-status.service"), env={}) == [
        ("system", "agentkennel-status.service"), ("system", "agentkennel-tasks.service")]
    assert S.panel_units(("user", "agents-status-server.service"), env={}) == [
        ("user", "agents-status-server.service")]
    assert S.panel_units(None, env={}) == []
    env = {"AGENTKENNEL_UPDATE_RESTART": "user:a.service  system:b.service bad c.service"}
    assert S.panel_units(("user", "x.service"), env=env) == [("user", "a.service"),
                                                             ("system", "b.service")]


class Units:
    """A fake systemctl: properties per unit, and what each command did."""
    def __init__(self, props, restart_ok=True):
        self.props, self.restart_ok, self.calls = props, restart_ok, []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        unit = cmd[-1]
        if "show" in cmd:
            p = self.props[unit]
            return subprocess.CompletedProcess(cmd, 0, "".join(f"{k}={v}\n" for k, v in p.items()), "")
        if "restart" in cmd:
            if self.restart_ok:
                self.props[unit]["MainPID"] = str(int(self.props[unit]["MainPID"]) + 1)
                return subprocess.CompletedProcess(cmd, 0, "", "")
            return subprocess.CompletedProcess(cmd, 1, "", "sudo: a password is required")
        if "reset-failed" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if "is-active" in cmd:
            return subprocess.CompletedProcess(cmd, 0, self.props[unit]["ActiveState"] + "\n", "")
        raise AssertionError(cmd)


def props(kill="mixed", restart="always", pid=100, cg="/x.service"):
    return {"KillMode": kill, "Restart": restart, "MainPID": str(pid), "ControlGroup": cg,
            "ActiveState": "active"}


def test_the_telegram_bridge_is_never_restarted():
    u = Units({"claude-tg-bridge.service": props(kill="process")})
    why = S.refuse_reason("user", "claude-tg-bridge.service", run=u, procs=lambda cg: [])
    assert why and "terminals" in why


def test_a_unit_holding_the_tmux_server_is_refused_unless_only_its_main_process_stops():
    u = Units({"a.service": props(kill="mixed", cg="/a"), "b.service": props(kill="process", cg="/b")})
    procs = lambda cg: ["python3", "tmux: server", "claude"]           # noqa: E731
    assert "terminals" in S.refuse_reason("user", "a.service", run=u, procs=procs)
    assert S.refuse_reason("user", "b.service", run=u, procs=procs) is None
    assert S.refuse_reason("user", "a.service", run=u, procs=lambda cg: ["python3"]) is None


def test_the_updater_does_not_restart_the_unit_it_runs_in_unless_it_survives():
    u = Units({"a.service": props(kill="mixed", cg="/a"), "b.service": props(kill="process", cg="/b")})
    assert "itself" in S.refuse_reason("user", "a.service", run=u, procs=lambda cg: [],
                                       own_cgroup="/a")
    assert S.refuse_reason("user", "b.service", run=u, procs=lambda cg: [], own_cgroup="/b") is None


def test_restart_user_and_system_units():
    u = Units({"a.service": props(pid=10), "s.service": props(pid=20)})
    ok, how = S.restart("user", "a.service", run=u, sleep=lambda s: None)
    assert ok and ["systemctl", "--user", "restart", "a.service"] in u.calls
    # the start limit a crashing version may have hit is cleared first
    assert u.calls.index(["systemctl", "--user", "reset-failed", "a.service"]) < \
        u.calls.index(["systemctl", "--user", "restart", "a.service"])
    ok, how = S.restart("system", "s.service", run=u, sleep=lambda s: None)
    assert ok and ["sudo", "-n", "systemctl", "restart", "s.service"] in u.calls


def test_without_sudo_a_system_unit_that_restarts_itself_is_stopped_by_its_pid():
    u = Units({"s.service": props(pid=20)}, restart_ok=False)
    killed = []
    def kill(pid, sig):
        killed.append((pid, sig))
        u.props["s.service"]["MainPID"] = "21"           # systemd brings it back
    ok, how = S.restart("system", "s.service", run=u, kill=kill, sleep=lambda s: None)
    assert ok and killed == [(20, signal.SIGTERM)] and "pid" in how


def test_without_sudo_a_unit_that_would_stay_down_is_not_touched():
    u = Units({"s.service": props(pid=20, restart="no")}, restart_ok=False)
    killed = []
    ok, how = S.restart("system", "s.service", run=u, kill=lambda p, s: killed.append(p),
                        sleep=lambda s: None)
    assert not ok and killed == [] and "sudo" in how


def test_restart_by_a_command_of_your_own():
    ran = []
    def run(cmd, **kw):
        ran.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")
    ok, how = S.restart_cmd("my-supervisor restart panel", run=run)
    assert ok and ran == [["bash", "-c", "my-supervisor restart panel"]]


def test_healthy_waits_for_the_new_version_to_answer():
    answers = iter([OSError("refused"), {"running": "2.7.2"}, {"running": "2.8.0"}])
    def get(url):
        a = next(answers)
        if isinstance(a, Exception):
            raise a
        return a
    t = [0.0]
    ok, why = S.healthy("http://h/api/update", "2.8.0", get=get, timeout=60,
                        sleep=lambda s: t.__setitem__(0, t[0] + s), clock=lambda: t[0])
    assert ok, why


def test_unhealthy_says_what_answered():
    t = [0.0]
    ok, why = S.healthy("http://h/api/update", "2.8.0", get=lambda url: {"running": "2.7.2"},
                        timeout=10, sleep=lambda s: t.__setitem__(0, t[0] + s), clock=lambda: t[0])
    assert not ok and "2.7.2" in why
