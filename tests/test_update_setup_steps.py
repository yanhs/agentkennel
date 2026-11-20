"""After the code moves to the new release, the update runs that release's own setup steps
(updater/setup_steps.py of the NEW code): only steps safe to run again, without sudo.
Today: the guard hooks, the way install.sh installs them — only on an install that
install.sh made (its record names this folder) and with the guards on."""
import subprocess
import sys
from pathlib import Path

from updater import setup_steps as S

ROOT = Path(__file__).resolve().parents[1]


def record(tmp_path, install, guards="1", workdir="/w"):
    conf = tmp_path / "install.env"
    conf.write_text(f"AGENTKENNEL_DIR={install}\nAGENTKENNEL_WORKDIR={workdir}\nAGENTKENNEL_GUESTS=0\n")
    return {"AGENTKENNEL_CONF": str(conf), "AGENTKENNEL_GUARDS": guards}


def test_the_record_of_install_sh_is_read(tmp_path):
    env = record(tmp_path, "/srv/ak")
    assert S.install_record(env)["DIR"] == "/srv/ak"
    assert S.install_record({"AGENTKENNEL_CONF": str(tmp_path / "none")}) == {}


def test_guard_hooks_run_with_install_shs_arguments(tmp_path):
    ran = []
    def run(cmd, **kw):
        ran.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "ok", "")
    env = record(tmp_path, "/srv/ak")
    out = S.run_steps("/srv/ak", "/home/u", env=env, run=run)
    assert out == [{"step": "guard hooks", "ok": True, "detail": "ok"}]
    assert ran == [[sys.executable, "/srv/ak/hooks/install_guards.py",
                    "--settings", "/home/u/.claude/settings.json", "--hooks-dir", "/srv/ak/hooks",
                    "--tracker-state", "/srv/ak/.sessions/tasks-state.json",
                    "--claude-md", "/home/u/.claude/CLAUDE.md",
                    "--tracker", "/srv/ak/tasks-dashboard/tracker.py",
                    "--services-dir", "/w/.services"]]


def test_no_guard_hooks_on_an_install_install_sh_did_not_make_or_with_them_off(tmp_path):
    never = lambda cmd, **kw: (_ for _ in ()).throw(AssertionError(cmd))   # noqa: E731
    assert S.run_steps("/mine", "/home/u", env=record(tmp_path, "/srv/ak"), run=never) == []
    assert S.run_steps("/srv/ak", "/home/u", env=record(tmp_path, "/srv/ak", guards="0"),
                       run=never) == []


def test_a_failing_step_says_so(tmp_path):
    run = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 2, "", "boom")   # noqa: E731
    out = S.run_steps("/srv/ak", "/home/u", env=record(tmp_path, "/srv/ak"), run=run)
    assert out[0]["ok"] is False and "boom" in out[0]["detail"]


def test_it_runs_as_a_script_and_prints_json(tmp_path):
    env = {"AGENTKENNEL_CONF": str(tmp_path / "none"), "PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}
    r = subprocess.run([sys.executable, "-m", "updater.setup_steps", "--install", str(tmp_path)],
                       cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "[]"
