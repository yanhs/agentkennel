"""`pytest -n 4`: every xdist worker runs in a sandbox of its own (bin/pytest-worker,
started by the root conftest.py).

Why: tests start fake `claude --resume <uuid>` processes, and the code under test finds
a conversation's claude by scanning /proc (library_cli.claude_processes) — and restarts it
by signalling what it found. Several test files share the same made-up uuids, so two
workers side by side would see, refuse ("already running elsewhere") or even kill each
other's fakes. In its own PID namespace a worker sees and can signal only what it started
itself: not the other workers, and not the live terminals either. The live tmux
directory (/tmp/tmux-<uid>, and <checkout>/.sessions/tmux of an installed copy) is
covered by an empty tmpfs inside, so no tmux in a test can reach the live server.
When the worker ends, everything it left running ends with it."""
import importlib.util
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
WORKER = REPO / "bin" / "pytest-worker"
PY = sys.executable


def _root_conftest():
    spec = importlib.util.spec_from_file_location("ak_root_conftest", REPO / "conftest.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def run_in_worker(code, worker=WORKER, **kw):
    return subprocess.run([str(worker), PY, "-c", code], capture_output=True, text=True,
                          timeout=60, **kw)


def test_a_worker_has_its_own_pid_namespace_and_proc():
    r = run_in_worker("import os; print(os.getpid(), os.readlink('/proc/self/ns/pid'), "
                      "sum(p.isdigit() for p in os.listdir('/proc')))")
    assert r.returncode == 0, r.stderr
    pid, ns, nprocs = r.stdout.split()
    assert int(pid) < 10                              # 1 is the reaper, then the worker
    assert ns != os.readlink("/proc/self/ns/pid")
    assert int(nprocs) < 10, r.stdout                 # none of the server's processes


def test_a_worker_keeps_the_uid_stdin_stdout_and_exit_code():
    """execnet talks to the worker over its stdin/stdout."""
    r = subprocess.run([str(WORKER), PY, "-c",
                        "import os, sys; print(os.getuid(), os.getgid(), sys.stdin.readline().strip()); "
                        "sys.exit(7)"],
                       input="hello\n", capture_output=True, text=True, timeout=60)
    assert r.returncode == 7, r.stderr
    assert r.stdout.split() == [str(os.getuid()), str(os.getgid()), "hello"]


def test_a_worker_cannot_see_the_live_tmux_dir():
    r = run_in_worker("import os; d = f'/tmp/tmux-{os.getuid()}'; "
                      "print(sorted(os.listdir(d)) if os.path.isdir(d) else 'absent')")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() in ("[]", "absent"), r.stdout


def test_a_worker_cannot_see_an_installed_checkouts_tmux_dir(tmp_path):
    """install.sh keeps the live tmux server under <checkout>/.sessions/tmux."""
    (tmp_path / "bin").mkdir()
    worker = tmp_path / "bin" / "pytest-worker"
    worker.write_bytes(WORKER.read_bytes())
    worker.chmod(0o755)
    live = tmp_path / ".sessions" / "tmux" / f"tmux-{os.getuid()}"
    live.mkdir(parents=True)
    (live / "default").write_text("")
    r = run_in_worker(f"import os; print(os.listdir({str(live.parent)!r}))", worker=worker)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "[]"
    assert (live / "default").exists()                # outside: untouched


def test_orphans_are_reaped_and_leftovers_end_with_the_worker():
    mark = f"pytest-worker-leftover-{uuid.uuid4().hex[:12]}"
    code = f"""
import os, subprocess, time
subprocess.run(['sh', '-c', '(sleep 0.2 &)'])          # an orphan that exits: pid 1 reaps it
subprocess.Popen(['bash', '-c', 'exec -a {mark} sleep 600'], start_new_session=True)
time.sleep(1)
z = []
for p in os.listdir('/proc'):
    try:
        st = open(f'/proc/{{p}}/stat').read()
    except OSError:
        continue
    if st.rsplit(')', 1)[1].split()[0] == 'Z':
        z.append(p)
print('zombies', len(z))
"""
    r = run_in_worker(code)
    assert r.returncode == 0, r.stderr
    assert r.stdout.split() == ["zombies", "0"], r.stdout
    # read-only lookup of a unique, made-up name (nothing is signalled)
    left = subprocess.run(["pgrep", "-fx", f"{mark} 600"], capture_output=True, text=True)
    assert left.stdout.strip() == "", f"left running after the worker ended: {left.stdout}"


def test_xdist_popen_workers_are_wrapped_in_the_sandbox():
    m = _root_conftest()
    w = m.wrap_tx(["popen", "popen", "2*popen", "ssh=host", "popen//python=/opt/py"],
                  worker="/r/bin/pytest-worker", python="/usr/bin/python3")
    want = "popen//python=/r/bin/pytest-worker /usr/bin/python3"
    assert w == [want, want, "2*" + want, "ssh=host", "popen//python=/opt/py"]


def test_a_sandbox_that_does_not_start_stops_the_run(tmp_path):
    m = _root_conftest()
    assert m.sandbox_works(str(WORKER), PY) is True
    broken = tmp_path / "pytest-worker"
    broken.write_text("#!/bin/sh\necho 'unshare: operation not permitted' >&2\nexit 1\n")
    broken.chmod(0o755)
    assert m.sandbox_works(str(broken), PY) is False


@pytest.mark.skipif(not os.environ.get("AK_SANDBOX_PROBE"), reason="run by the test below")
@pytest.mark.parametrize("n", range(4))
def test_sandbox_probe(n):
    assert os.environ.get("PYTEST_XDIST_WORKER")
    assert os.readlink("/proc/self/ns/pid") != os.environ["AK_SANDBOX_PROBE"]
    d = f"/tmp/tmux-{os.getuid()}"
    assert not os.path.isdir(d) or os.listdir(d) == []


def test_pytest_n_runs_every_worker_in_the_sandbox():
    env = {**os.environ, "AK_SANDBOX_PROBE": os.readlink("/proc/self/ns/pid")}
    env.pop("PYTEST_XDIST_WORKER", None)
    r = subprocess.run([PY, "-m", "pytest", "-n", "2", "-q", "-p", "no:cacheprovider",
                        "tests/test_parallel_sandbox.py", "-k", "sandbox_probe"],
                       cwd=REPO, env=env, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]
    assert "4 passed" in r.stdout, r.stdout[-2000:]
