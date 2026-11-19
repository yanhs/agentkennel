"""Restart (⟳) and Trash (🗑, restore, Empty trash) for library topics.

Part of status_server (the facade re-exports every name here)."""

import signal
import subprocess
import sys
import time
import os
import library
import envcompat
import convo_sync
import library_cli

from backend import _HERE
from backend.terminals import sample_working
from backend.library_api import (
    lib_busy_reason, lib_legacy, lib_live, lib_path, LIB_REORDER_MAX, lib_rows, lib_tmux, LibError
)


# ── Restart (⟳ on a loaded row): Claude starts again in the same conversation ──
# Why (owner 2026-10-23): launch settings (e.g. CLAUDE_CODE_SCROLL_SPEED in bin/pane)
# take effect only on a fresh start, and a hung Claude needs reviving. The launch
# itself is not changed: the terminal is unloaded and loaded again by `library_cli
# ensure`, exactly what opening it does (bare `claude --resume <uuid>`).
#
# One claude per conversation: the check, the kill and the wait for the old claude
# run under library_cli's ensure lock, so nothing renames or starts a terminal
# meanwhile. An open tab races us — its attach returns when the session dies and
# ttyd runs open-session.sh (ensure) again; that ensure waits for the lock, and
# after it the two ensures take turns and the second finds the session running.
# Either way: one new claude.
# The check under the lock: lib_canonical's sync came before the busy sample, and
# a /clear, /resume or consent relaunch landing in between (with or without another
# syncer renaming the session) would make us restart the conversation it left —
# refused instead (409 "changed"), as is a kill-session that found no session.
# The old claude is waited for by pid + start time, not by "still a claude on u":
# Claude's shutdown removes its pid file first and then flushes for a while, and
# after /clear that file is all that ties it to u. A hung Claude may ignore the
# hangup tmux sends: then SIGTERM, then SIGKILL — only to those exact processes.
RESTART_EXIT_SECONDS = 5       # the old claude's time to exit after the hangup …
RESTART_TERM_SECONDS = 3       # … and after SIGTERM, and after SIGKILL
RESTART_ENSURE_TIMEOUT = 60
LIBRARY_CLI = os.path.join(_HERE, "library_cli.py")
# lib_busy_reason's reasons in plain words (the page says them in its own words)
BUSY_WORDS = {"printing": "it is printing right now", "hold": "a timer is pending",
              "background-task": "a background task is running", "working": "it is working"}
RESTART_CHANGED = "it switched conversations just now; press ⟳ again"


def _claude_pids(u):
    """pids of every claude running conversation u (library_cli's own test)."""
    return {p for p, _ in library_cli.claude_processes(u)}


def _proc_start(pid):
    """Start time of a running pid (/proc/<pid>/stat field 22); None when it is gone
    or a zombie (it has exited, only not reaped yet)."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            st = f.read().rsplit(")", 1)[1].split()
    except (OSError, IndexError):
        return None
    return None if len(st) < 20 or st[0] in ("Z", "X") else st[19]


def _still_running(procs):
    """The ones of {pid: start time} still running (a reused pid is another process)."""
    return {p: t for p, t in procs.items() if _proc_start(p) == t}


def _wait_gone(procs, seconds):
    """Wait until none of {pid: start time} runs any more; -> the ones that still do."""
    end = time.time() + seconds
    while True:
        left = _still_running(procs)
        if not left or time.time() >= end:
            return left
        time.sleep(0.1)


def _elsewhere_error(other):
    pids = ", ".join(str(p) for p, _ in other)
    return LibError(409, f"this conversation also runs in another Claude (pid {pids}); "
                         "close that one first", reason="elsewhere")


def _unload_locked(name, u, changed, stuck):
    """Unload terminal tmux `name` running conversation u; the caller holds the
    ensure lock. Refused (409) when the terminal is gone or runs another
    conversation now (`changed`: the words for that) or when u also runs in
    another claude. Kills the exact session, then waits for its claude by pid +
    start time; one that ignores the hangup gets SIGTERM, then SIGKILL — only those
    exact processes. One that still runs: 500, "<…> did not exit; it is unloaded,
    <stuck>"."""
    if (lib_tmux("has-session", "-t", "=" + name).returncode != 0
            or convo_sync.pane_conversations(lib_tmux).get(name, u) != u):
        raise LibError(409, changed, reason="changed")
    other = library_cli.claude_elsewhere(u, name)
    if other:
        raise _elsewhere_error(other)
    old = {p: t for p in _claude_pids(u)              # all in this terminal (checked above)
           if (t := _proc_start(p)) is not None}
    if lib_tmux("kill-session", "-t", "=" + name).returncode != 0:
        raise LibError(409, changed, reason="changed")
    left = _wait_gone(old, RESTART_EXIT_SECONDS)
    for sig in (signal.SIGTERM, signal.SIGKILL):
        if not left:
            break
        for p in _still_running(left):            # the very processes, checked again
            try:
                os.kill(p, sig)
            except OSError:                       # gone meanwhile / not ours
                pass
        left = _wait_gone(left, RESTART_TERM_SECONDS)
    if left:
        raise LibError(500, f"the old Claude (pid {', '.join(map(str, sorted(left)))}) did not "
                            f"exit; it is unloaded, {stuck}")


def lib_restart(sid, force, check=False):
    """Unload terminal `sid` and load it again: claude starts anew on the same
    conversation and the terminal comes back loaded even with no tab open.
    Refused (409 + reason) when there is nothing to restart (archived,
    not-loaded), when the conversation also runs in another claude (elsewhere:
    ensure would then refuse to load it back), when it switched conversations
    while we checked (changed), and — unless force — when it is busy
    (lib_busy_reason). An open tab is not a reason: the owner pressed ⟳.
    check=True (POST restart-check) touches nothing and answers {"busy": reason
    | None}, so the page asks ONE question — plain, or red naming the reason
    (owner 2026-10-23)."""
    e = library.find(library.load(lib_path()), sid)
    if e is None:
        raise LibError(404, "unknown id")
    if library.is_trashed(e):
        raise LibError(409, "it is in the trash; restore it first", reason="trashed")
    if e.get("archived"):
        raise LibError(409, "it is archived; restore it first", reason="archived")
    info = lib_live().get(sid)
    if info is None:
        raise LibError(409, "it is not loaded: opening it starts Claude", reason="not-loaded")
    name = library.tmux_name(sid)
    if check:
        try:
            _, u = library_cli.checked_entry(e)
        except ValueError:
            raise LibError(500, "the registry entry is corrupt (uuid)")
        if library_cli.claude_elsewhere(u, name):
            raise LibError(409, "this conversation also runs in another Claude; close that one first",
                           reason="elsewhere")
    if check or not force:
        reason = _busy_now(sid, info)
        if check:
            return {"busy": reason}
        if reason is not None:
            raise LibError(409, f"{BUSY_WORDS[reason]}; send force=true to restart it anyway",
                           reason=reason)
    try:
        _, u = library_cli.checked_entry(e)
    except ValueError:
        raise LibError(500, "the registry entry is corrupt (uuid)")
    with convo_sync._ensure_lock(lib_path(), True):          # library_cli's ensure lock
        _unload_locked(name, u, RESTART_CHANGED, "nothing was started")
    env = dict(os.environ, **envcompat.both("LIBRARY", lib_path()))
    try:
        r = subprocess.run([sys.executable, LIBRARY_CLI, "ensure", sid], capture_output=True,
                           text=True, env=env, timeout=RESTART_ENSURE_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise LibError(500, "unloaded, but loading it again took too long; open it to retry")
    if r.returncode != 0:
        msg = " ".join((r.stderr or "").split())[:200] or f"library_cli exit {r.returncode}"
        raise LibError(500, f"unloaded, but couldn't load it again: {msg}")
    e = library.find(library.load(lib_path()), sid) or e
    row = lib_rows([e])[0]
    row["restarted"] = True
    return row


def _busy_now(sid, info):
    """lib_busy_reason of a loaded terminal, with one CPU sample of its own."""
    working = sample_working({sid: info["pane_pid"]})
    return lib_busy_reason(sid, info, working.get(sid, False))


# ── Trash (🗑 on every topic row; owner 2026-10-23) ──────────────────────────
# Archive = needed, just not now. Trash = not needed, kept just in case: a trashed
# terminal is in no list (library.display_order), never loaded (library_cli ensure
# refuses it), and nothing erases it by itself. Move to Trash unloads a loaded one
# the way ⟳ does (_unload_locked, under the ensure lock); a busy one only with
# force=true, after the page's red question. Empty trash erases for good, only on
# the owner's word: the entries the page showed that are trashed AND run nowhere.
TRASH_CHANGED = "it switched conversations just now; press 🗑 again"


def lib_trash(sid, force, check=False):
    """Move terminal `sid` to the trash: unload it if loaded, then set `trashed`.
    Refused (409 + reason) while its conversation runs in another claude or in an
    old numbered terminal (elsewhere), when it switched conversations while we
    checked (changed), and — unless force — when it is busy (lib_busy_reason; an
    open tab is not a reason: the owner pressed 🗑). check=True (POST trash-check)
    touches nothing and answers {"busy": reason | None}. Again on a trashed one:
    nothing changes. -> the row, + unloaded: whether it was loaded and now is not."""
    e = library.find(library.load(lib_path()), sid)
    if e is None:
        raise LibError(404, "unknown id")
    if library.is_trashed(e):
        return {"busy": None} if check else dict(lib_rows([e])[0], unloaded=False)
    try:
        _, u = library_cli.checked_entry(e)
    except ValueError:
        raise LibError(500, "the registry entry is corrupt (uuid)")
    name = library.tmux_name(sid)
    legacy = lib_legacy().get(u)
    if legacy:
        raise LibError(409, f"its conversation is running in the old terminal {legacy}; "
                            "stop it there first", reason="elsewhere")
    info = lib_live().get(sid)
    if check:
        other = library_cli.claude_elsewhere(u, name)
        if other:
            raise _elsewhere_error(other)
    if info is not None and (check or not force):
        reason = _busy_now(sid, info)
        if check:
            return {"busy": reason}
        if reason is not None:
            raise LibError(409, f"{BUSY_WORDS[reason]}; send force=true to move it to the "
                                "trash anyway", reason=reason)
    if check:
        return {"busy": None}
    with convo_sync._ensure_lock(lib_path(), True):          # nothing loads it meanwhile
        unloaded = lib_tmux("has-session", "-t", "=" + name).returncode == 0
        if unloaded:
            if info is None:                      # loaded since we looked: not what was asked
                raise LibError(409, TRASH_CHANGED, reason="changed")
            _unload_locked(name, u, TRASH_CHANGED, "it was not moved to the trash")
        else:
            other = library_cli.claude_elsewhere(u, name)
            if other:
                raise _elsewhere_error(other)
        try:
            with library.update(lib_path()) as lib:
                e = dict(library.trash(lib, sid, now=int(time.time())))
        except KeyError:
            raise LibError(404, "unknown id")
    row = lib_rows([e])[0]
    row["unloaded"] = unloaded
    return row


EMPTY_TRASH_REASONS = ("unknown", "not-trashed", "running", "corrupt", "erase-failed",
                       "left-on-disk")


def lib_empty_trash(ids):
    """Erase for good the trashed entries among `ids` (the ones the page showed):
    each one's transcript and its own folder (library.erase_conversation), then its
    registry entry. Skipped, with a reason (EMPTY_TRASH_REASONS): an id not in the
    registry or not in the trash, and one whose conversation runs anywhere — loaded,
    in another claude, in an old numbered terminal. Erased where claude ran it: the
    entry's folder and the one the pane falls back to when that folder is gone
    (library_cli.effective_cwd). Never reported erased while a copy of the
    transcript is still on disk in some other project dir: that one is kept in the
    trash, nothing removed ('left-on-disk'). Under the ensure lock, then the
    registry lock (the order library_cli.ensure and convo_sync take them).
    -> {"erased": [ids], "skipped": [{id, reason}]}"""
    if not isinstance(ids, list) or len(ids) > LIB_REORDER_MAX:
        raise LibError(400, "ids must be a list")
    if not all(library.valid_id(i) for i in ids):
        raise LibError(400, "invalid id")
    erased, skipped = [], []
    if not ids:
        return {"erased": erased, "skipped": skipped}
    legacy = lib_legacy()
    with convo_sync._ensure_lock(lib_path(), True):
        live = lib_live()
        with library.update(lib_path()) as lib:
            for sid in dict.fromkeys(ids):        # each once, in the page's order
                e = library.find(lib, sid)
                if e is None or not library.is_trashed(e):
                    skipped.append({"id": sid, "reason": "unknown" if e is None else "not-trashed"})
                    continue
                try:
                    _, u = library_cli.checked_entry(e)
                except ValueError:
                    skipped.append({"id": sid, "reason": "corrupt"})
                    continue
                if sid in live or u in legacy or library_cli.claude_processes(u):
                    skipped.append({"id": sid, "reason": "running"})
                    continue
                cwds = [e.get("cwd"), library_cli.effective_cwd(e)]
                try:
                    root = os.path.abspath(library.claude_projects_root())
                    own = {os.path.dirname(library.transcript_file(root, c, u))
                           for c in cwds if isinstance(c, str) and c}
                    if any(os.path.dirname(p) not in own for p in library.transcripts_anywhere(u)):
                        convo_sync.log(f"empty trash: {sid} kept: its transcript is in another project dir")
                        skipped.append({"id": sid, "reason": "left-on-disk"})
                        continue
                    library.erase_conversation(e, cwds=cwds)
                    left = library.transcripts_anywhere(u)
                except (OSError, ValueError) as ex:
                    convo_sync.log(f"empty trash: {sid} not erased: {str(ex)[:200]}")
                    skipped.append({"id": sid, "reason": "erase-failed"})
                    continue
                if left:                                  # never "erased" with a copy on disk
                    convo_sync.log(f"empty trash: {sid} kept: {len(left)} transcript(s) left")
                    skipped.append({"id": sid, "reason": "left-on-disk"})
                    continue
                library.delete(lib, sid)
                erased.append(sid)
    return {"erased": erased, "skipped": skipped}
