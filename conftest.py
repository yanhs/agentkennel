"""`pytest -n N` (pytest-xdist): every worker starts through bin/pytest-worker, in a
sandbox of its own — its own PID namespace and /proc, the live tmux directory covered,
its own network (loopback only). Workers then never see each other's fake claudes, nor
the live terminals, nor any live port (tests/test_parallel_sandbox.py). Without -n
nothing here applies.

If the sandbox cannot start (no unprivileged user namespaces), a parallel run stops at
once and says so; it never falls back to unsandboxed workers."""
import os
import shlex
import subprocess
import sys

import pytest

WORKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin", "pytest-worker")


def wrap_tx(specs, worker=WORKER, python=sys.executable):
    """xdist gateway specs with every plain local `popen` started through the sandbox."""
    py = f"python={shlex.quote(worker)} {shlex.quote(python)}"
    out = []
    for s in specs:
        count, star, rest = s.rpartition("*") if "*" in s else ("", "", s)
        if rest.split("//")[0] == "popen" and "python=" not in rest:
            rest = f"{rest}//{py}"
        out.append(f"{count}{star}{rest}")
    return out


def sandbox_works(worker=WORKER, python=sys.executable):
    try:
        r = subprocess.run([worker, python, "-c",
                            "import os; print(os.readlink('/proc/self/ns/pid'))"],
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0 and r.stdout.strip() not in ("", os.readlink("/proc/self/ns/pid"))


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    tx = getattr(config.option, "tx", None)
    if not tx or hasattr(config, "workerinput"):     # not -n, or already a worker
        return
    if not sandbox_works():
        raise pytest.UsageError(
            "pytest -n: bin/pytest-worker could not start a sandboxed worker (it needs "
            "unprivileged user namespaces: `unshare --user --pid --mount`). Parallel "
            "workers without it could see each other's processes, so the run stops; "
            "run the tests without -n.")
    config.option.tx = wrap_tx(tx)
