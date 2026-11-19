#!/usr/bin/env python3
"""
Agent status server.

GET  /  — live status + auto-detected project/task
POST /  — update manual overrides  { "id": "1", "project": "...", "task": "..." }
"""
from http.server import HTTPServer, ThreadingHTTPServer, BaseHTTPRequestHandler
import http.client
import json
import signal
import subprocess
import sys
import threading
import time
import os
import re
import hmac
import hashlib
import secrets
import unicodedata
import glob
import urllib.error
import urllib.parse
import urllib.request
from urllib.parse import urlparse, parse_qs
from datetime import datetime
from pathlib import Path

import library   # session registry (library.py next to this file)
import envcompat   # settings: AGENTKENNEL_*, then the old AGENTDECK_*
import convo_sync   # a terminal takes the number of the conversation live in it
import idle_reaper   # background-task detection (tree_has_task_output), pure /proc walk
import library_cli   # claude_processes: which claudes run a conversation (Restart)
import server_status   # the Server page's collector (GET /api/server)
import services_keeper   # the Server page's Services block (GET/POST /api/services)

# ── The code lives in backend/, one module per concern; this file is the entry point and
# the one namespace everything else uses (tests, tg_bridge.py: `import status_server`).
#   backend/terminals.py      system line, tmux panes, agents.json, process tree
#   backend/telegram.py       the Telegram bridge and its setup page
#   backend/library_api.py    /api/library: listing, new, rename, archive, order, close
#   backend/restart_trash.py  Restart and Trash
#   backend/server_tab.py     /api/server, /api/services
#   backend/guests.py         /api/guests
#   backend/quota.py          /api/quota*
#   backend/http_api.py       Handler (:3011), BufferHandler (:3045), LiveHandler (:3014)
#   backend/auth.py           the login gate (:3046)
#   backend/uploads.py        image paste and file upload (:3047)
# Every module's names are re-exported here. Setting one on this module (monkeypatch, or
# `status_server.X = ...`) sets it in every backend module that holds it too, so the code
# sees it as it did when all of this was one file. Importing this module afresh (tests pop
# it from sys.modules) loads fresh backend modules too: settings are read from the
# environment again.
import types as _types

for _k in [k for k in sys.modules if k == "backend" or k.startswith("backend.")]:
    del sys.modules[_k]

from backend import (terminals, telegram, library_api, restart_trash, server_tab, guests, quota, http_api, auth, uploads)  # noqa: E402

_PARTS = (terminals, telegram, library_api, restart_trash, server_tab, guests, quota, http_api, auth, uploads)
for _m in _PARTS:
    for _k, _v in vars(_m).items():
        if not _k.startswith("__"):
            globals()[_k] = _v
del _m, _k, _v
_MISSING = object()


class _Facade(_types.ModuleType):
    def __setattr__(self, name, value):
        if not name.startswith("__"):
            old = self.__dict__.get(name, _MISSING)
            for m in _PARTS:
                if name in m.__dict__ and (old is _MISSING or m.__dict__[name] is old):
                    setattr(m, name, value)
        super().__setattr__(name, value)

    def __delattr__(self, name):
        old = self.__dict__.get(name, _MISSING)
        for m in _PARTS:
            if old is not _MISSING and m.__dict__.get(name, _MISSING) is old:
                delattr(m, name)
        super().__delattr__(name)


sys.modules[__name__].__class__ = _Facade

# what the command line below uses, by name (all of it is re-exported above as well)
from backend.telegram import tg_autostart  # noqa: E402
from backend.server_tab import SERVER_COLLECTOR  # noqa: E402
from backend.http_api import BufferHandler, Handler, LiveHandler  # noqa: E402
from backend.auth import AuthHandler, PASSFILE, reset_password, setup_link_path, setup_url  # noqa: E402
from backend.uploads import PasteHandler  # noqa: E402


if __name__ == "__main__":
    if "--setup-link" in sys.argv[1:]:   # the first-run link's path ('' + exit 1: none)
        link = setup_link_path()
        print(link)
        sys.exit(0 if link else 1)
    if "--setup-url" in sys.argv[1:]:    # the same, with the server's address in front
        link = setup_url()
        print(link)
        sys.exit(0 if link else 1)
    if "--reset-password" in sys.argv[1:]:
        if not PASSFILE:
            print("--reset-password works with the dashboard's own password file "
                  "(AGENTKENNEL_PASSFILE), which this setup doesn't use", file=sys.stderr)
            sys.exit(2)
        link = reset_password()
        print(link)
        sys.exit(0 if link else 1)
    from threading import Thread
    SERVER_COLLECTOR.start()  # /api/server: sample /proc every 5 s
    tg_autostart()  # bring the Telegram bridge back up if it was configured + enabled
    Thread(target=lambda: HTTPServer(("127.0.0.1", 3014), LiveHandler).serve_forever(), daemon=True).start()
    Thread(target=lambda: HTTPServer(("127.0.0.1", 3045), BufferHandler).serve_forever(), daemon=True).start()
    Thread(target=lambda: HTTPServer(("127.0.0.1", 3046), AuthHandler).serve_forever(), daemon=True).start()
    # threaded: a 100 MB upload must not hold up an image paste
    Thread(target=lambda: ThreadingHTTPServer(("127.0.0.1", 3047), PasteHandler).serve_forever(), daemon=True).start()
    HTTPServer(("127.0.0.1", 3011), Handler).serve_forever()
