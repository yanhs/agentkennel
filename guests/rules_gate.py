#!/usr/bin/env python3
"""A guest's rules in the live terminal too (owner, 17.11.2026): the sandbox becomes Claude-only.

The turnstile adds the owner's rules to every request the sandbox makes of Claude; that holds
whatever happens inside. What the rules could not reach were the ways to run something WITHOUT
Claude. Found by experiment in a throwaway container (claude 2.1.282; the design note
docs/superpowers/specs/2026-11-17-guest-rules-live-terminal.md):

  * `!command` in Claude's input runs it directly: no UserPromptSubmit, no PreToolUse, no hook.
    But it — like every Bash tool call, hook, status line and stdio MCP server — is started
    through CLAUDE_CODE_SHELL_PREFIX. This file is that prefix (`prefix`): a command runs only
    when it is a hook from the settings files or when the model asked for it a moment ago (the
    PreToolUse hook `ticket` leaves a one-time ticket with its text). `!` has no ticket.
  * tmux's prefix key (Ctrl+B c: a new window with a shell; Ctrl+B : run-shell): taken away.
  * Ctrl+G / /memory open $EDITOR: EDITOR and VISUAL point at a refusal.
  * plugins (hooks and MCP servers of their own): no marketplace may be added.
  * the dashboard's plain command line (cmd-shell): refused by the core while the flag is up.

Everything here runs as root, as the agent itself does, so the agent could undo it — but only
because someone asked it to, and that request reaches it with the owner's rules attached.
What this closes is the guest's way around the agent, not the agent.

    rules_gate.py install on|off   (as root in the sandbox; guestctl pipes this file in)
    rules_gate.py prefix '<command line>'      CLAUDE_CODE_SHELL_PREFIX, via shell-gate
    rules_gate.py ticket                       PreToolUse hook (any tool), JSON on stdin
    rules_gate.py report shell|editor          tell the owner what the core refused

Standard library only: it runs on any sandbox image.
"""
import glob
import hashlib
import json
import os
import re
import secrets
import shlex
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

RULES_DIR = "etc/agentkennel/rules"           # under the sandbox's / (root-owned)
MANAGED = "etc/claude-code/managed-settings.json"
TICKETS = "run/agentkennel-rules"
SHELL_GATE = "/etc/agentkennel/rules/shell-gate"
NO_EDITOR = "/etc/agentkennel/rules/no-editor"
TICKET_HOOK = "python3 /etc/agentkennel/rules/gate.py ticket"
TICKET_TTL = 600            # seconds a model's command may wait between PreToolUse and its run
REFUSED = 126              # never 2: a PreToolUse hook exiting 2 (through the prefix) blocks
REFUSAL = ("The server's owner has limited this sandbox to its work: commands are run only by "
           "Claude. Write to Claude what you need instead (no `!`).")
BROKEN = ("AgentKennel: the rules gate failed ({error}), so this command was not run. "
          "Tell the server's owner: the guest's rules lock needs fixing.")
REFUSED_LOG_MAX = 64 * 1024  # /run/agentkennel-rules/refused.log: the newest half is kept
REPORT_MAX = 200             # characters of what was tried that go to the owner
# what the core refuses, reported by `rules_gate.py report <what>` (open-session.sh, no-editor)
REPORTS = {"shell": "opened the plain command line (refused)",
           "editor": "opened an editor from Claude (refused)"}

# Ctrl+B is tmux's way to a shell (c: new window, ": run-shell"); with no prefix key the
# prefix table can't be reached. The right-click menus are already unbound by tmux.conf.
TMUX_LOCK = ("# a guest's rules are on (guests/rules_gate.py): no tmux commands from the keyboard\n"
             "set -g prefix None\n"
             "set -g prefix2 None\n")

# RULES_GATE_ROOT: tests only (unset in a sandbox: the paths are /etc/...). Not an
# AGENTKENNEL_* setting: the gate runs standalone, without envcompat
SHELL_GATE_SH = """#!/bin/sh
# CLAUDE_CODE_SHELL_PREFIX (guests/rules_gate.py). Never exit 2: hooks run through here too.
G="${RULES_GATE_ROOT:-}/etc/agentkennel/rules/gate.py"
if [ ! -r "$G" ]; then
  echo "AgentKennel: the rules gate ($G) is missing, so no command can run. Tell the server's owner." >&2
  exit 126
fi
exec python3 "$G" prefix "$1"
"""
NO_EDITOR_SH = """#!/bin/sh
# EDITOR/VISUAL (guests/rules_gate.py): refused while a guest's rules are on, a real editor otherwise
R="${RULES_GATE_ROOT:-}/etc/agentkennel/rules"
if [ ! -e "$R/on" ]; then
  for e in editor nano vim vi; do
    command -v "$e" >/dev/null 2>&1 && exec "$e" "$@"
  done
  echo "No editor is installed." >&2
  exit 1
fi
[ -r "$R/gate.py" ] && python3 "$R/gate.py" report editor >/dev/null 2>&1
echo 'No editor here: ask Claude to change the file.' >&2
exit 1
"""


def root_dir() -> Path:
    return Path(os.environ.get("RULES_GATE_ROOT") or "/")


def tickets_dir(root: Path) -> Path:
    return root / TICKETS


def is_on(root: Path) -> bool:
    return (root / RULES_DIR / "on").exists()


def managed_settings() -> dict:
    return {
        # DISABLE_AUTOUPDATER: claude runs as root with internet; a newer claude must not
        # arrive by itself under a lock measured on a known version
        "env": {"CLAUDE_CODE_SHELL_PREFIX": SHELL_GATE, "EDITOR": NO_EDITOR, "VISUAL": NO_EDITOR,
                "DISABLE_AUTOUPDATER": "1"},
        "hooks": {"PreToolUse": [{"matcher": "*", "hooks": [
            {"type": "command", "command": TICKET_HOOK}]}]},
        "strictKnownMarketplaces": [],
        "disableCommandPluginSources": True,
        # nothing about the bypass-permissions consent: the lock neither gives nor takes it
        # (the sandbox's own, from guestctl's seed, stays as it is)
    }


def sh_quote(s: str) -> str:
    """POSIX single quotes, the way claude quotes a command into its `eval '…'`."""
    return "'" + s.replace("'", "'\"'\"'") + "'"


def _forms(cmd: str) -> list:
    """The command as claude puts it into its wrapper: `eval '<cmd>' ` — the whole command,
    quoted, so a longer command typed with `!` never matches a shorter pending one."""
    quoted = {sh_quote(cmd), "'" + cmd.replace("'", "'\\''") + "'", shlex.quote(cmd)}
    return ["eval " + q + " " for q in quoted]     # shlex.quote: a plain word stays bare


def hook_commands(paths) -> set:
    """Every hook and status line command in these settings files (missing or broken: none)."""
    out = set()

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "command" and isinstance(node.get("command"), str):
                out.add(node["command"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    for p in paths:
        try:
            data = json.loads(Path(p).read_text())
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            walk(data.get("hooks"))
            walk(data.get("statusLine"))
    return out


def settings_files(root: Path, cwd: str) -> list:
    """Where hook commands may come from: managed, the user's, and the project's — the
    session's project ($CLAUDE_PROJECT_DIR) and every folder from cwd up (after a `cd`)."""
    home = Path(os.environ.get("HOME") or "/root")
    out = [root / MANAGED, home / ".claude" / "settings.json"]
    dirs = [Path(cwd), *Path(cwd).parents]
    if os.environ.get("CLAUDE_PROJECT_DIR"):
        dirs.insert(0, Path(os.environ["CLAUDE_PROJECT_DIR"]))
    for d in dirs:
        out += [d / ".claude" / "settings.json", d / ".claude" / "settings.local.json"]
    return out


def _cwd() -> str:
    try:
        return os.getcwd()
    except OSError:                    # the folder was deleted under the session
        return os.environ.get("PWD") or "/"


def what_was_tried(cmdline: str) -> str:
    """The command itself out of claude's wrapper (`… && eval '<cmd>' < /dev/null && …`)."""
    at = cmdline.find("&& eval ")     # the first one is claude's: the rest is inside quotes
    if at >= 0:
        try:
            lex = shlex.shlex(cmdline[at + 8:], posix=True)
            lex.whitespace_split = True
            return next(iter(lex))
        except (ValueError, StopIteration):
            pass
    return cmdline


_SECRETS = [re.compile(r"sk-ant-[A-Za-z0-9_-]+"),
            re.compile(r"(?i)\b(bearer|token|password|passwd|secret|api[_-]?key)(\s*[:=]\s*|\s+)\S+"),
            re.compile(r"[A-Za-z0-9+/_-]{32,}={0,2}")]


def sanitized(text: str, cap: int = REPORT_MAX) -> str:
    """One short line for the owner: no keys, tokens or passwords in it."""
    text = " ".join(str(text or "").split())
    for rx in _SECRETS:
        text = rx.sub(lambda m: (m.group(1) + " …") if m.re.groups else "…", text)
    return text[:cap]


def tell_owner(kind: str, text: str) -> None:
    """POST /flag to the turnstile with this sandbox's own pass; the turnstile names the
    guest, applies its hourly limit and tells the owner. Never raises, never waits long."""
    base = (os.environ.get("ANTHROPIC_BASE_URL") or "").rstrip("/")
    token = os.environ.get("ANTHROPIC_AUTH_TOKEN") or ""
    if not base:
        return
    req = urllib.request.Request(
        base + "/flag", data=json.dumps({"kind": kind, "text": sanitized(text)}).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST")
    try:
        urllib.request.urlopen(req, timeout=3).close()
    except Exception:
        pass


def log_refusal(path: Path, cmdline: str) -> None:
    """One line per refusal; past REFUSED_LOG_MAX the newest half is kept."""
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > REFUSED_LOG_MAX - 1024:
            keep = path.read_bytes()[-(REFUSED_LOG_MAX // 2):]
            path.write_bytes(keep[keep.find(b"\n") + 1:])
        digest = hashlib.sha256(cmdline.encode()).hexdigest()[:12]
        with open(path, "a") as f:
            f.write(f"{int(time.time())} {digest} {sanitized(what_was_tried(cmdline), 300)!r}\n")
    except OSError:
        pass


def write_ticket(folder: Path, hook_input, now=None) -> None:
    """PreToolUse: the model is about to run this command — let it through once."""
    try:
        cmd = hook_input["tool_input"]["command"]
    except (KeyError, TypeError):
        return
    if not isinstance(cmd, str) or not cmd.strip():
        return
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    t = time.time() if now is None else now
    name = f"{int(t)}-{secrets.token_hex(8)}"
    tmp = folder / (name + ".tmp")
    tmp.write_text(json.dumps({"command": cmd, "t": t}))
    os.replace(tmp, folder / name)


def decide(cmdline: str, hooks: set, folder: Path, now=None) -> bool:
    if cmdline in hooks:
        return True
    t = time.time() if now is None else now
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return False
    for name in names:
        if name.endswith(".tmp"):
            continue
        path = folder / name
        try:
            d = json.loads(path.read_text())
            cmd, made = d["command"], float(d["t"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if t - made > TICKET_TTL:
            _remove(path)
            continue
        if isinstance(cmd, str) and cmd.strip() and any(f in cmdline + " " for f in _forms(cmd)):
            if _remove(path):             # spent: two runs can't share one ticket
                return True
    return False


def _remove(path: Path) -> bool:
    try:
        os.unlink(path)
        return True
    except OSError:
        return False


def _write(path: Path, text: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".new")
    tmp.write_text(text)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def _ours(path: Path) -> bool:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and (data.get("env") or {}).get(
        "CLAUDE_CODE_SHELL_PREFIX") == SHELL_GATE


def _tmux_sockets(root: Path) -> list:
    return sorted(glob.glob(str(root / "tmp" / "tmux-*" / "*")))


def tmux_unlock_argv(sock: str, home: Path) -> list:
    """Back to tmux's own default, then the user's ~/.tmux.conf on top (their prefix wins)."""
    argv = [["tmux", "-S", sock, "set", "-g", "prefix", "C-b"],
            ["tmux", "-S", sock, "set", "-g", "prefix2", "None"]]
    if (Path(home) / ".tmux.conf").is_file():
        argv.append(["tmux", "-S", sock, "source-file", str(Path(home) / ".tmux.conf")])
    return argv


def _claude_pids(root: Path) -> list:
    pids = []
    for cmdline in glob.glob(str(root / "proc" / "[0-9]*" / "cmdline")):
        try:
            argv0 = Path(cmdline).read_bytes().split(b"\0", 1)[0]
        except OSError:
            continue
        if os.path.basename(argv0.decode("utf-8", "replace")) in ("claude", "claude.exe"):
            pids.append(int(Path(cmdline).parent.name))
    return sorted(pids)


def install(root: Path, on: bool, source: str, run=subprocess.run) -> dict:
    """{"stale_claudes": n}: claudes that were running when the settings folder did not exist
    yet — measured: they never pick up managed settings until restarted."""
    rules = root / RULES_DIR
    managed = root / MANAGED
    info = {"stale_claudes": 0}
    if not on and not rules.exists():      # never locked: nothing to lift, nothing written
        return info
    if on and not managed.parent.exists():
        info["stale_claudes"] = len(_claude_pids(root))
    if on and managed.exists() and not _ours(managed):
        raise SystemExit(f"{managed} is someone else's: not overwritten")
    # the gate stays in place when off: a claude started while on still calls it
    _write(rules / "gate.py", source, 0o755)
    _write(rules / "shell-gate", SHELL_GATE_SH, 0o755)
    _write(rules / "no-editor", NO_EDITOR_SH, 0o755)
    managed.parent.mkdir(parents=True, exist_ok=True)   # kept: the next lock applies live
    for d in (root / "etc/agentkennel", rules, managed.parent):
        os.chmod(d, 0o755)
    kw = dict(capture_output=True, text=True, timeout=10)
    if on:
        _write(rules / "tmux.conf", TMUX_LOCK, 0o644)
        _write(managed, json.dumps(managed_settings(), indent=2) + "\n", 0o644)
        _write(rules / "on", "on\n", 0o644)
        for sock in _tmux_sockets(root):
            run(["tmux", "-S", sock, "source-file", str(rules / "tmux.conf")], **kw)
            run(["tmux", "-S", sock, "kill-session", "-t", "=cmd-shell"], **kw)
    else:
        _remove(rules / "on")
        _remove(rules / "tmux.conf")
        if _ours(managed):
            _remove(managed)
        home = Path(os.environ.get("HOME") or "/root")
        for sock in _tmux_sockets(root):
            for argv in tmux_unlock_argv(sock, home):
                run(argv, **kw)
    return info


# guestctl runs this file in the sandbox from stdin (any image has python3, none needs a copy):
#   docker exec -i sbx-<name> python3 -c LOADER install on   < guests/rules_gate.py
LOADER = ("import sys; s = sys.stdin.read(); g = {'__name__': 'rules_gate'}; exec(s, g); "
          "sys.exit(g['main'](sys.argv[1:], source=s))")


def main(argv=None, source=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    root = root_dir()
    if argv[:1] == ["prefix"] and len(argv) == 2:
        cmdline = argv[1]
        try:
            allowed = not is_on(root) or decide(
                cmdline, hook_commands(settings_files(root, _cwd())), tickets_dir(root))
        except Exception as exc:     # fail closed, but say so: the agent relays it
            print(BROKEN.format(error=f"{type(exc).__name__}: {exc}"[:200]), file=sys.stderr)
            return REFUSED
        if not allowed:
            print(REFUSAL, file=sys.stderr)
            log_refusal(tickets_dir(root) / "refused.log", cmdline)
            tell_owner("bypass", "typed a command past Claude (refused): " + what_was_tried(cmdline))
            return REFUSED
        os.execvp("bash", ["bash", "-c", cmdline])
    if argv[:1] == ["report"] and len(argv) == 2:
        if argv[1] not in REPORTS:
            return 2
        if is_on(root):
            tell_owner("bypass", REPORTS[argv[1]])
        return 0
    if argv == ["ticket"]:
        try:
            write_ticket(tickets_dir(root), json.loads(sys.stdin.read() or "{}"))
        except Exception:       # a hook that fails must never stop the agent's own work
            pass
        return 0
    if argv[:1] == ["install"] and argv[1:] in (["on"], ["off"]):
        info = install(root, argv[1] == "on",
                       source=source if source is not None else Path(__file__).read_text())
        print(f"rules lock: {argv[1]}")
        if info.get("stale_claudes"):
            print(f"stale-claudes: {info['stale_claudes']}")
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
