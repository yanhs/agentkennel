"""The session library API (/api/library): listing, new/rename/archive/reorder/close, the
registry's tmux side (see library.py).

Part of status_server (the facade re-exports every name here)."""

import subprocess
import time
import os
import re
import unicodedata
import library
import envcompat
import convo_sync
import idle_reaper

from backend import _HERE
from backend.terminals import child_map, get_system_stats, sample_working


# ── Session library API: /api/library (see library.py) ──────────────────────
# Topics live in the registry; the loaded ones are tmux sessions cs-<id>. All tmux
# calls here go to AGENTKENNEL_TMUX_SOCKET when set (`tmux -L <name>` — tests use a
# private socket), and /close targets exactly "=cs-<id>" after the id is validated
# and found in the registry, so nothing else can ever be killed from here.
LIB_ROUTE = "/api/library"
LIB_NAME_MAX = 200
LIB_BODY_MAX = 64 * 1024
# /close unloads only a topic nobody is using: not attached and no screen output
# for this long — anything else needs {"force": true}
CLOSE_QUIET_SECONDS = 30 * 60
# CSRF: POSTs must be JSON (a cross-site <form> cannot send that without a CORS
# preflight, and we answer no preflight) and, when the browser says where the
# request comes from, come from the dashboard itself.
# The dashboard's origin: $AGENTKENNEL_ORIGIN when set (e.g. https://agents.example.com —
# needed behind a proxy that rewrites Host or doesn't pass X-Forwarded-Proto, like the
# nginx config in nginx/); otherwise the request's own scheme + Host, as the proxy
# forwards them (Caddy keeps Host and sets X-Forwarded-Proto). No Host -> refused.


def lib_allowed_origin(headers=None):
    """The one Origin library writes are accepted from ('' = none acceptable)."""
    configured = envcompat.get("ORIGIN")
    if configured:
        return configured
    headers = headers or {}
    host = (headers.get("Host") or "").strip()
    proto = (headers.get("X-Forwarded-Proto") or "http").strip().lower()
    if not host or proto not in ("http", "https") or not re.fullmatch(r"[A-Za-z0-9.:\[\]-]+", host):
        return ""
    return f"{proto}://{host}"


def lib_origin_ok(origin, headers):
    allowed = lib_allowed_origin(headers)
    return bool(allowed) and origin == allowed
_LIB_ROW_KEYS = ("id", "name", "cwd", "created", "last_used", "archived", "pos")
# only on a terminal whose conversation changed under it (convo_sync / library.switch):
# the numbers that now lead here, the number it had before and when it changed
_LIB_SWITCH_KEYS = ("aliases", "prev_id", "switched_at")


class LibError(Exception):
    def __init__(self, code, msg, **extra):
        super().__init__(msg)
        self.code = code
        self.extra = extra          # more fields of the error reply (e.g. reason)


def lib_path():
    return envcompat.get("LIBRARY") or library.LIB_FILE


def lib_workdir():
    """Where a new topic starts: $AGENTKENNEL_WORKDIR, else the folder above this
    repo — the same default the launch-claude*.sh scripts use."""
    return envcompat.get("WORKDIR") or os.path.dirname(_HERE)


def lib_tmux(*args):
    sock = envcompat.get("TMUX_SOCKET")
    return subprocess.run(["tmux", *(["-L", sock] if sock else []), *args],
                          capture_output=True, text=True)


def lib_sync(lock="try"):
    """One number per terminal: a terminal whose Claude now runs another conversation
    (consent relaunch, /clear, /resume) takes that conversation's number before
    anything reads or acts on it (convo_sync.sync). lock="try": the page's poll
    skips a round while an `ensure` runs; True: wait (before an unload). Never
    raises: a failure is logged and the caller goes on with what is there."""
    try:
        return convo_sync.sync(run=lib_tmux, lib_file=lib_path(), lock=lock)
    except Exception as e:  # noqa: BLE001 — must never break the listing or a POST
        convo_sync.log(f"sync failed: {str(e)[:200]}")
        return []


def lib_canonical(sid):
    """The current number of terminal `sid`: after syncing, an old number that
    became an alias (the consent relaunch) maps to the terminal that has it."""
    lib_sync(lock=True)
    e = library.find_or_alias(library.load(lib_path()), sid)
    return e["id"] if e is not None else sid


def lib_live():
    """{id: {"attached": bool, "pane_pid": int|None}} for every cs-<id> session.
    One tmux call; no running tmux server simply means nothing is loaded."""
    r = lib_tmux("list-panes", "-a", "-F",
                 "#{session_name}\t#{session_attached}\t#{pane_pid}")
    live = {}
    if r.returncode != 0:
        return live
    for line in r.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        sid = library.id_from_tmux(parts[0])
        if sid is None or sid in live:          # not a library session / 2nd pane
            continue
        live[sid] = {"attached": parts[1] not in ("", "0"),
                     "pane_pid": int(parts[2]) if parts[2].isdigit() else None}
    return live


_LEGACY_TMUX = re.compile(r"claude-terminal(?:-(\d+))?")
_RESUME_UUID = re.compile(rb"--(?:resume|session-id)\x00?\s*([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")


def _uuids_under(pid, depth=4, children=None):
    """uuids after --resume/--session-id in the cmdline of pid and its descendants."""
    children = child_map() if children is None else children
    found, stack = set(), [(pid, 0)]
    while stack:
        p, d = stack.pop()
        try:
            with open(f"/proc/{p}/cmdline", "rb") as f:
                found.update(m.decode() for m in _RESUME_UUID.findall(f.read()))
        except OSError:
            continue
        if d < depth:
            stack.extend((c, d + 1) for c in children.get(p, []))
    return found


def lib_legacy():
    """{uuid: "/terminalN/"} for Claude conversations still running in the old
    numbered terminals (migration leaves busy ones untouched until they unload)."""
    r = lib_tmux("list-panes", "-a", "-F", "#{session_name}\t#{pane_pid}")
    out, children = {}, None
    for line in r.stdout.splitlines() if r.returncode == 0 else []:
        name, _, pid = line.partition("\t")
        m = _LEGACY_TMUX.fullmatch(name)
        if not m or not pid.isdigit():
            continue
        path = f"/terminal{m.group(1) or ''}/"
        children = child_map() if children is None else children
        for u in _uuids_under(int(pid), children=children):
            out.setdefault(u, path)
    return out


def lib_last_output(name):
    """Latest #{window_activity} (moves on pane output) of session `name`, by
    exact name; None when unknown. list-windows + filtering, never
    display-message (segfaults tmux 3.2a)."""
    r = lib_tmux("list-windows", "-a", "-F", "#{session_name}\t#{window_activity}")
    last = None
    for line in r.stdout.splitlines() if r.returncode == 0 else []:
        n, _, act = line.rpartition("\t")
        if n == name and act.isdigit():
            last = max(last or 0, int(act))
    return last


def lib_status(active, working):
    """Same words as the terminal cards: off / idle / working."""
    return "off" if not active else ("working" if working else "idle")


def lib_shell_live():
    """The dashboard's command line (tmux cmd-shell, exact name):
    {"attached": bool, "pane_pid": int|None}, or None when it is not running."""
    r = lib_tmux("list-panes", "-a", "-F",
                 "#{session_name}\t#{session_attached}\t#{pane_pid}")
    for line in r.stdout.splitlines() if r.returncode == 0 else []:
        parts = line.split("\t")
        if len(parts) == 3 and parts[0] == library.SHELL_TMUX:
            return {"attached": parts[1] not in ("", "0"),
                    "pane_pid": int(parts[2]) if parts[2].isdigit() else None}
    return None


_SHELL_KEY = "\0shell"        # never a valid id: cannot collide with a topic's key


def lib_rows(entries, live=None, legacy_paths=None, working=None):
    """Registry entries -> API rows with tmux state (samples CPU once for all).
    live / legacy_paths / working: pass what the caller already has (one scan
    and one CPU sample per request)."""
    live = lib_live() if live is None else live
    if working is None:
        working = sample_working({e["id"]: live[e["id"]]["pane_pid"]
                                  for e in entries if e["id"] in live})
    legacy_paths = lib_legacy() if legacy_paths is None else legacy_paths
    rows = []
    for e in entries:
        row = {k: e.get(k) for k in _LIB_ROW_KEYS}
        row.update({k: e[k] for k in _LIB_SWITCH_KEYS if e.get(k) is not None})
        if library.is_trashed(e):
            row["trashed"] = e["trashed"]           # only in the trash listing (?trash=1)
        row["archived"] = bool(e.get("archived"))
        info = live.get(e["id"])
        legacy = legacy_paths.get(e.get("uuid")) if info is None else None
        row["legacy_path"] = legacy
        row["active"] = info is not None or legacy is not None
        row["attached"] = bool(info and info["attached"])
        row["status"] = lib_status(info is not None, working.get(e["id"], False))
        rows.append(row)
    return rows


def lib_listing(include_archived=False, trash=False):
    """The list (never a trashed terminal); trash=True: the trash itself instead."""
    lib_sync()                                      # the page polls this every 4 s
    first_run = not os.path.exists(lib_path())      # a new install: see library.welcome
    lib = library.load(lib_path())
    live = lib_live()
    legacy = lib_legacy()
    active = set(live) | {e["id"] for e in lib["sessions"] if e.get("uuid") in legacy}
    entries = library.display_order(lib, active, include_archived=include_archived,
                                    trashed=trash)
    shell = lib_shell_live()
    pids = {e["id"]: live[e["id"]]["pane_pid"] for e in entries if e["id"] in live}
    if shell is not None:
        pids[_SHELL_KEY] = shell["pane_pid"]
    working = sample_working(pids)                  # one CPU sample for all rows
    return {"max_active": library.MAX_ACTIVE,
            "first_run": first_run,
            "sessions": lib_rows(entries, live, legacy, working),
            # the "cmd" button's shell: not a topic, so not in sessions[]
            "shell": {"active": shell is not None,
                      "attached": bool(shell and shell["attached"]),
                      "status": lib_status(shell is not None, working.get(_SHELL_KEY, False)),
                      # a guest's rules: no command line here (the page hides cmd)
                      **({"locked": True} if library.shell_locked() else {})},
            "_system": get_system_stats()}      # CPU/RAM for the top bar, same as GET /


def lib_clean_name(v, required=False):
    """Display name: whitespace runs (newlines too) -> one space, control chars
    dropped (a name ends up in tmux/Telegram/logs), at most LIB_NAME_MAX chars."""
    if v is None:
        v = ""
    if not isinstance(v, str):
        raise LibError(400, "name must be a string")
    v = "".join(ch for ch in " ".join(v.split()) if unicodedata.category(ch) != "Cc")
    if len(v) > LIB_NAME_MAX:
        raise LibError(400, f"name longer than {LIB_NAME_MAX} characters")
    if required and not v:
        raise LibError(400, "name is empty")
    return v


def lib_id(body):
    sid = body.get("id")
    if not library.valid_id(sid):
        raise LibError(400, "invalid id")
    return sid


def lib_edit(sid, fn):
    """Locked read-modify-write of one entry; unknown id -> 404, nothing saved."""
    try:
        with library.update(lib_path()) as lib:
            e = fn(lib)
    except KeyError:
        raise LibError(404, "unknown id")
    return lib_rows([e])[0]


def lib_post(route, body):
    if route == "new":
        name = lib_clean_name(body.get("name"))
        with library.update(lib_path()) as lib:
            e = library.create(lib, name, cwd=lib_workdir(), now=int(time.time()))
        return lib_rows([e], live={})[0]          # not started: nothing to ask tmux
    if route == "welcome":
        # first visit of a new install: one "Getting started" terminal, once
        e = library.welcome(lib_path(), cwd=lib_workdir(), now=int(time.time()))
        return lib_rows([e], live={})[0] if e else {"id": None}
    if route == "reorder":
        return lib_reorder(body.get("ids"))
    if route == "shell-close":
        # the "cmd" command line: exact name, configured socket; not running = ok
        killed = lib_tmux("kill-session", "-t", "=" + library.SHELL_TMUX).returncode == 0
        return {"ok": True, "killed": killed}

    if route == "empty-trash":
        return lib_empty_trash(body.get("ids"))

    sid = lib_id(body)
    if route in ("rename", "archive", "close", "restart", "restart-check",
                 "trash", "trash-check", "untrash"):
        sid = lib_canonical(sid)                  # act on the terminal's live number
    if route == "rename":
        name = lib_clean_name(body.get("name"), required=True)
        return lib_edit(sid, lambda lib: library.rename(lib, sid, name))
    if route == "archive":
        archived = body.get("archived", True)
        if not isinstance(archived, bool):
            raise LibError(400, "archived must be true or false")
        return lib_archive(sid, archived)
    if route == "close":
        force = body.get("force", False)
        if not isinstance(force, bool):
            raise LibError(400, "force must be true or false")
        e = library.find(library.load(lib_path()), sid)
        if e is None:
            raise LibError(404, "unknown id")
        name = library.tmux_name(sid)
        info = lib_live().get(sid)
        if info is not None and not force:
            if info["attached"]:
                raise LibError(409, "the topic is open in a browser/terminal right now; "
                                    "send force=true to unload it anyway")
            last = lib_last_output(name)
            if last is None or time.time() - last < CLOSE_QUIET_SECONDS:
                raise LibError(409, f"the topic printed output in the last "
                                    f"{CLOSE_QUIET_SECONDS // 60} min (it may be working); "
                                    "send force=true to unload it anyway")
        killed = lib_tmux("kill-session", "-t", "=" + name).returncode == 0
        row = lib_rows([e])[0]
        row["killed"] = killed
        return row
    if route == "restart":
        force = body.get("force", False)
        if not isinstance(force, bool):
            raise LibError(400, "force must be true or false")
        return lib_restart(sid, force)
    if route == "restart-check":
        # its own route, not a flag on "restart": a server older than the page
        # answers 404 here instead of taking the question for a restart
        return lib_restart(sid, False, check=True)
    if route in ("trash", "trash-check"):
        force = body.get("force", False)
        if not isinstance(force, bool):
            raise LibError(400, "force must be true or false")
        return lib_trash(sid, force, check=route == "trash-check")
    if route == "untrash":
        return lib_edit(sid, lambda lib: library.untrash(lib, sid))
    raise LibError(404, "not found")


LIB_REORDER_MAX = 10000
DELETE_QUIET_SECONDS = 120     # 2 min of quiet: an archived terminal's (idle_reaper's window)
ARCHIVE_BUSY_SECONDS = 15      # output this recent = working: Archive leaves it loaded


def lib_reorder(ids):
    """Manual order from the page: the full visible order of ids -> pos 0..n-1."""
    if not isinstance(ids, list) or not ids or len(ids) > LIB_REORDER_MAX:
        raise LibError(400, "ids must be a non-empty list")
    if not all(library.valid_id(i) for i in ids):
        raise LibError(400, "invalid id")
    if len(set(ids)) != len(ids):
        raise LibError(400, "duplicate id")
    try:
        with library.update(lib_path()) as lib:
            library.reorder(lib, ids)
    except KeyError as e:
        raise LibError(400, f"unknown id {e.args[0]}")
    return {"ok": True}


def lib_busy_reason(sid, info, working):
    """Why a loaded terminal must not be unloaded right now, or None when it
    may go. The owner's top rule is "never interrupt work": printing in the
    last ARCHIVE_BUSY_SECONDS (unknown output time counts as printing), an
    unexpired hold marker (a timer is pending), a live background task, or the
    cards' CPU status 'working'. An open tab is NOT a reason."""
    now = time.time()
    last = lib_last_output(library.tmux_name(sid))
    if last is None or now - last < ARCHIVE_BUSY_SECONDS:
        return "printing"
    if library.held(sid, now, lib_file=lib_path()):
        return "hold"
    pid = info.get("pane_pid")
    if pid and idle_reaper.tree_has_task_output(pid):
        return "background-task"
    if working:
        return "working"
    return None


def lib_archive(sid, archived):
    """Archive/restore. Archive unloads the terminal right away (owner: "When I
    press Archive, the terminal should unload") — even with a tab open, since
    the owner pressed Archive on purpose — unless it is working
    (lib_busy_reason): then it stays loaded, unload_pending, and idle_reaper
    unloads it once quiet (ARCHIVED_IDLE_SECONDS). The reply says which:
    unloaded true|false + reason (idle | printing | hold | background-task |
    working | not-loaded). A trashed one is refused (409 'trashed', checked under
    the registry lock): a stale row must not change where Restore puts it back."""
    try:
        with library.update(lib_path()) as lib:
            e = dict(library.archive(lib, sid, archived))
    except KeyError:
        raise LibError(404, "unknown id")
    except library.Trashed:
        raise LibError(409, "it is in the trash; restore it first", reason="trashed")
    if not archived:
        return lib_rows([e])[0]
    live = lib_live()
    info = live.get(sid)
    working = {}
    if info is None:
        unloaded, reason = False, "not-loaded"
    else:
        working = sample_working({sid: info["pane_pid"]})
        reason = lib_busy_reason(sid, info, working.get(sid, False))
        unloaded = reason is None
        if unloaded:
            lib_tmux("kill-session", "-t", "=" + library.tmux_name(sid))
            reason = "idle"
            live, working = None, {}
    row = lib_rows([e], live=live, working=working)[0]
    row["unloaded"] = unloaded
    row["reason"] = reason
    row["unload_pending"] = info is not None and not unloaded
    return row


# used only inside functions here, defined in a module the facade imports later
from backend.restart_trash import lib_empty_trash, lib_restart, lib_trash  # noqa: E402
