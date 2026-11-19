"""The guests tests start real tmux servers (test_rules_gate.py): like the main suite
(tests/conftest.py), a run gets its own TMUX_TMPDIR, so their sockets — and a bare `tmux`
anywhere — never land in the directory of the live terminals' server (/tmp/tmux-<uid>).
Before 19.11.2026 they did: dead rules-core-*/rules-gate-test-* sockets piled up there."""
import os
import subprocess
import tempfile


def test_tmux_is_pointed_at_a_private_dir():
    d = os.environ.get("TMUX_TMPDIR", "")
    assert d and d.startswith(tempfile.gettempdir()) and "pytest-tmux" in d, d
    assert os.path.realpath(d) != os.path.realpath(tempfile.gettempdir()), d


def test_a_named_tmux_server_lives_in_the_private_dir(tmp_path):
    sock = f"guests-isolation-{os.getpid()}"
    r = subprocess.run(["tmux", "-L", sock, "-f", "/dev/null", "new-session", "-d", "-s", "t",
                        "cat"], capture_output=True, text=True, timeout=10)
    assert r.returncode == 0, r.stderr
    try:
        where = os.path.join(os.environ["TMUX_TMPDIR"], f"tmux-{os.getuid()}", sock)
        assert os.path.exists(where), where
        assert not os.path.exists(f"/tmp/tmux-{os.getuid()}/{sock}")
    finally:
        subprocess.run(["tmux", "-L", sock, "kill-server"], capture_output=True, timeout=10)
