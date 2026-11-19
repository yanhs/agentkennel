"""Terminals: the system CPU/RAM line, reading tmux panes (project, task, junk and notice
filtering), agents.json overrides and the process tree (is a terminal working?).

Part of status_server (the facade re-exports every name here)."""

import json
import subprocess
import time
import os
import re
import server_status

from backend import _HERE


SESSIONS = [
    {"id": "1",  "session": "claude-terminal",    "path": "terminal"},
    {"id": "2",  "session": "claude-terminal-2",   "path": "terminal2"},
    {"id": "3",  "session": "claude-terminal-3",   "path": "terminal3"},
    {"id": "4",  "session": "claude-terminal-4",   "path": "terminal4"},
    {"id": "5",  "session": "claude-terminal-5",   "path": "terminal5"},
    {"id": "6",  "session": "claude-terminal-6",   "path": "terminal6"},
    {"id": "7",  "session": "claude-terminal-7",   "path": "terminal7"},
    {"id": "8",  "session": "claude-terminal-8",   "path": "terminal8"},
    {"id": "9",  "session": "claude-terminal-9",   "path": "terminal9"},
    {"id": "10", "session": "claude-terminal-10",  "path": "terminal10"},
    {"id": "11", "session": "claude-terminal-11",  "path": "terminal11"},
    {"id": "12", "session": "claude-terminal-12",  "path": "terminal12"},
]

AGENTS_FILE = os.path.join(_HERE, "agents.json")
SAMPLE_INTERVAL = 0.15
_prev_cpu = None
# a guest's sandbox: the CPU/RAM line reads its cgroup limits (server_status.read_cgroup)
CGROUP_ROOT = "/sys/fs/cgroup"
_prev_cg = None
_stats_clock = time.monotonic
CPU_TICK_THRESHOLD = 2

# Optional: pretty display names for auto-detected project folders. Any folder
# not listed here simply shows its directory name, so this map is just for looks.
# Map your own "<folder>": "<Display Name>" entries here.
PROJECT_MAP = {
    "my-app": "My App",
    "docs-site": "Docs Site",
    "terminal": "Terminal",
    "api-server": "API Server",
}

ANSI_RE = re.compile(r'\x1b[\[\(][0-9;]*[a-zA-Z]|\x1b\].*?\x07|\x1b.|\x0f|\x0e')
SEP_PROJECT_RE = re.compile(r'[─━═]{3,}\s+(.+?)\s+[─━═]{3,}')
PATH_PROJECT_RE = re.compile(r'(?:/home/ubuntu/pr|~/pr|/var/www)/([a-zA-Z0-9_-]+)')
SKIP_DIRS = frozenset({
    "tgimg", "tgfiles", "static", "node_modules",
    ".git", ".cache", ".claude", "pr",
})


def get_system_stats():
    global _prev_cpu
    # CPU
    try:
        with open("/proc/stat") as f:
            parts = f.readline().split()[1:]
        vals = [int(v) for v in parts]
        idle = vals[3] + vals[4]  # idle + iowait
        total = sum(vals)
        cpu_pct = 0
        if _prev_cpu:
            d_total = total - _prev_cpu[0]
            d_idle = idle - _prev_cpu[1]
            cpu_pct = round((1 - d_idle / d_total) * 100) if d_total else 0
        _prev_cpu = (total, idle)
    except Exception:
        cpu_pct = 0
    # RAM
    try:
        mem = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":")
                mem[k.strip()] = int(v.split()[0])
        total_mb = mem["MemTotal"] // 1024
        avail_mb = mem.get("MemAvailable", mem["MemFree"]) // 1024
        used_mb = total_mb - avail_mb
        ram_pct = round(used_mb / total_mb * 100) if total_mb else 0
    except Exception:
        total_mb = used_mb = ram_pct = 0
    # a guest's sandbox: its own memory and CPU quota, like the Server page (30.10.2026)
    global _prev_cg
    cg = server_status.read_cgroup(CGROUP_ROOT)
    if "mem_max" in cg and total_mb and cg["mem_max"] / 1048576 < total_mb:
        total_mb = cg["mem_max"] // 1048576
        used_mb = min(total_mb, max(0, cg["mem_current"] - cg["mem_inactive"]) // 1048576)
        ram_pct = round(used_mb / total_mb * 100) if total_mb else 0
    if "cpu_quota" in cg and cg["cpu_quota"] < (os.cpu_count() or 1) and "cpu_usage_usec" in cg:
        now, prev = _stats_clock(), _prev_cg
        _prev_cg = (now, cg["cpu_usage_usec"])
        cpu_pct = 0
        if prev and now > prev[0]:
            used = (cg["cpu_usage_usec"] - prev[1]) / 1e6 / (now - prev[0])
            cpu_pct = round(max(0.0, min(100.0, used / cg["cpu_quota"] * 100)))
    return {"cpu_pct": cpu_pct, "ram_used_mb": used_mb, "ram_total_mb": total_mb, "ram_pct": ram_pct}


def strip_ansi(s):
    return ANSI_RE.sub("", s)


def is_junk(line):
    """Return True if line is UI chrome, not meaningful content."""
    if not line:
        return True
    # Pure box-drawing / whitespace
    if re.match(r'^[\s─━═╭╮╰╯│┃┌┐└┘├┤┬┴┼╱╲░▒▓▘▝▖▗▀▄█▌▐]+$', line):
        return True
    # Claude Code prompt / status bar
    junk_markers = [
        "bypass permissions", "shift+tab", "esc to interrupt",
        "ctrl+t", "ctrl+c", "ctrl+r", "\u276f",
    ]
    low = line.lower()
    for m in junk_markers:
        if m in low:
            return True
    # Just a prompt char
    if line in (">", "$", "%", "\u276f", "❯"):
        return True
    # Rating prompt
    if re.match(r'^\d+:\s*\w+\s+\d+:', line):
        return True
    # Path-only line like ~/pr
    if re.match(r'^~[/\w]*$', line.strip()):
        return True
    return False


# Claude Code's own notices for the person at the keyboard — never the agent's
# reply. A hook's output is a block: a head line ("● Ran 1 stop hook",
# "⎿  Stop hook error: …") and its wrapped/indented text, up to the next
# bullet, unindented line, spinner/timing line or tool summary.
_HOOK_HEAD_RE = re.compile(
    r"^(?:●\s*)?Ran \d+ [\w:-]+ hooks?$"
    r"|^(?:⎿\s*)?[\w:-]+ hook (?:error|feedback|blocking error|output)\b",
    re.IGNORECASE)
_UPDATE_RE = re.compile(
    r"^[✔✓✗✘]?\s*(?:Update installed\b|Auto-update failed\b)"
    r"|^Updated to latest\. Got \d+"
    r"|^code\.claude\.com/docs/\S*changelog for details$",
    re.IGNORECASE)
_BLOCK_END_RE = re.compile(
    r"^(?:Ran|Read|Wrote|Edited|Updated|Searched|Listed|Did|Fetched|Called|Created|"
    r"Running|Reading|Searching|Writing|Editing)\s+\d")
_SPINNER_CHARS = "✻✶✳✽✺✷✸✹✼❋✢❂⊹∗*·"


def drop_program_notices(lines):
    """`lines` (ANSI-stripped, indentation kept) without hook output blocks and
    the program's update notices. A dropped block leaves one blank line."""
    out, in_hook = [], False
    for line in lines:
        s = line.strip()
        if _HOOK_HEAD_RE.search(s):
            in_hook = True
            continue
        if in_hook:
            if not s or (line[:1] in (" ", "\t") and s[0] not in "●" + _SPINNER_CHARS
                         and not _BLOCK_END_RE.match(s)):
                continue
            in_hook = False
            if out and out[-1].strip():
                out.append("")
        if _UPDATE_RE.search(s):
            continue
        out.append(line)
    return out


def load_agents():
    try:
        with open(AGENTS_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_agents(data):
    with open(AGENTS_FILE, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def reset_agent(agent_id, session_name):
    """Unload an agent from RAM by killing its tmux session.

    Both the Claude conversation JSONL and the agent's overrides entry
    (project / task / locked) are intentionally left untouched, so that
    when the user re-adds the agent via "+ Claude":
      - launch-claude-*.sh resumes the same Claude session id
      - the agent card immediately shows the same name (instead of blank)

    Idempotent: missing tmux session is fine.
    """
    out = {"tmux_killed": False}

    # `=name`: exactly this session. A bare name is a prefix to tmux when no session
    # has it exactly — and slot #1's `claude-terminal` is the prefix of every slot.
    r = subprocess.run(
        ["tmux", "kill-session", "-t", "=" + session_name],
        capture_output=True, text=True,
    )
    out["tmux_killed"] = r.returncode == 0

    return out


def get_pane_pid(session):
    r = subprocess.run(
        ["tmux", "list-panes", "-t", f"={session}:", "-F", "#{pane_pid}"],   # exact
        capture_output=True, text=True,
    )
    if r.returncode != 0 or not r.stdout.strip():
        return None
    return int(r.stdout.strip().split("\n")[0])


def get_child_pids(pid):
    r = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True)
    return [int(p) for p in r.stdout.strip().split("\n") if p.strip()]


def read_cpu_ticks(pid):
    try:
        with open(f"/proc/{pid}/stat") as f:
            parts = f.read().split(")")
            fields = parts[-1].strip().split()
            return int(fields[11]) + int(fields[12])
    except (FileNotFoundError, PermissionError, IndexError, ValueError):
        return 0


def child_map():
    """{ppid: [pid, ...]} for every process, from one pass over /proc/*/stat —
    instead of spawning `pgrep -P` once per process."""
    kids = {}
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/stat") as f:
                ppid = int(f.read().rsplit(")", 1)[1].split()[1])
        except (OSError, IndexError, ValueError):
            continue
        kids.setdefault(ppid, []).append(int(name))
    return kids


def tree_cpu_ticks(pane_pid, children=None):
    """CPU ticks of a pane's process, its children and grandchildren — the
    quantity whose growth over SAMPLE_INTERVAL makes a terminal "working".
    `children`: a child_map() to use instead of pgrep per process."""
    kids = (lambda p: children.get(p, [])) if children is not None else get_child_pids
    ticks = read_cpu_ticks(pane_pid)
    for cpid in kids(pane_pid):
        ticks += read_cpu_ticks(cpid)
        for gpid in kids(cpid):
            ticks += read_cpu_ticks(gpid)
    return ticks


def is_working(ticks1, ticks2):
    return ticks2 - ticks1 > CPU_TICK_THRESHOLD


def sample_working(pane_pids):
    """{key: pane_pid} -> {key: working}, one shared SAMPLE_INTERVAL sleep —
    the same measure the terminal cards use (GET /)."""
    pids = {k: p for k, p in pane_pids.items() if p}
    if not pids:
        return {}
    kids = child_map()
    first = {k: tree_cpu_ticks(p, kids) for k, p in pids.items()}
    time.sleep(SAMPLE_INTERVAL)
    kids = child_map()
    return {k: is_working(t1, tree_cpu_ticks(pids[k], kids)) for k, t1 in first.items()}


def get_cwd(session):
    # list-panes, not display-message: in tmux 3.2a `display-message -p` on a
    # target that has just vanished segfaults the whole server (2026-09-24).
    r = subprocess.run(
        ["tmux", "list-panes", "-a", "-F", "#{session_name}\t#{pane_current_path}"],
        capture_output=True, text=True,
    )
    for line in r.stdout.splitlines() if r.returncode == 0 else []:
        name, _, path = line.partition("\t")
        if name == session:
            return path
    return ""


def detect_project_from_cwd(cwd):
    if not cwd:
        return ""
    parts = cwd.rstrip("/").split("/")
    for p in reversed(parts):
        if p.lower() in PROJECT_MAP:
            return PROJECT_MAP[p.lower()]
    for p in reversed(parts):
        if p and p not in ("pr", "home", "ubuntu", ""):
            return p
    return ""


def detect_project_from_pane(text):
    """Find the project by weighting recent mentions more heavily."""
    matches = [m for m in PATH_PROJECT_RE.findall(text) if m not in SKIP_DIRS]
    if not matches:
        return ""
    # Weight recent mentions: last 1/4 of text counts 4x
    total = len(text)
    cutoff = total * 3 // 4
    recent_text = text[cutoff:]
    recent_matches = [m for m in PATH_PROJECT_RE.findall(recent_text) if m not in SKIP_DIRS]
    from collections import Counter
    counts = Counter(matches)
    # Boost recent mentions
    for m in recent_matches:
        counts[m] += 3
    top = counts.most_common(1)[0][0]
    return PROJECT_MAP.get(top.lower(), top)


def parse_pane(session):
    """Capture pane content, extract project name and last meaningful activity."""
    # Deep capture for project detection, shallow for task
    r_deep = subprocess.run(
        ["tmux", "capture-pane", "-t", f"={session}:", "-p", "-S", "-300"],   # exact
        capture_output=True, text=True,
    )
    if r_deep.returncode != 0:
        return "", ""

    full_text = strip_ansi(r_deep.stdout)
    lines = full_text.split("\n")

    # 1) Project: most-mentioned path in scrollback
    project = detect_project_from_pane(full_text)
    task = ""

    # 2) Fallback: separator line  ──── name ────
    if not project:
        for line in lines:
            clean = line.strip()
            if not clean:
                continue
            dash_count = sum(1 for c in clean if c in "─━═")
            if dash_count < len(clean) * 0.6:
                continue
            m = SEP_PROJECT_RE.search(clean)
            if m:
                name = m.group(1).strip()
                low = name.lower().replace(" ", "-")
                project = PROJECT_MAP.get(low, name)

    # Find last user prompt (line starting with ❯)
    for line in reversed(lines):
        clean = strip_ansi(line).strip()
        if not clean:
            continue
        # Match prompt line: ❯ <user text>
        if clean.startswith("\u276f") or clean.startswith("❯"):
            prompt_text = clean.lstrip("❯\u276f ").strip()
            if prompt_text and len(prompt_text) > 1:
                if len(prompt_text) > 100:
                    prompt_text = prompt_text[:97] + "..."
                task = prompt_text
                break

    return project, task
