"""The tmux paste buffer (:3045) and the page version for live reload (:3014).

Part of status_server (the facade re-exports every name here)."""

from http.server import BaseHTTPRequestHandler
import json
import os
import re
import subprocess
from urllib.parse import urlparse

from backend import _HERE


class BufferHandler(BaseHTTPRequestHandler):
    """GET /  — return tmux paste buffer for a session."""
    def do_GET(self):
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(self.path).query)
        session = qs.get("session", [""])[0]
        if not session or not re.match(r'^[\w-]+$', session):
            self._resp(400, {"error": "bad session"})
            return
        r = subprocess.run(
            ["tmux", "show-buffer", "-t", session],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            # try without -t (global buffer)
            r = subprocess.run(["tmux", "show-buffer"], capture_output=True, text=True)
        self._resp(200, {"text": r.stdout if r.returncode == 0 else ""})

    def _resp(self, code, data):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

    def log_message(self, *args):
        pass


# the actually-served dashboard file (the old ../agents/index.html was archived,
# so page-version was stuck at "0" and live-reload never fired)
WEB_DIR = os.path.join(_HERE, "web")
WATCH_FILE = os.path.join(WEB_DIR, "index.html")   # kept for callers of the old name
# pages that poll /api/page-version, by the file name the browser shows in Referer;
# anything else (/, unknown names) watches index.html
WATCHED_PAGES = ("index.html", "index-lib.html")


def watch_file_for(referer):
    """The page file whose mtime is this page's version: the library page is
    served from web/index-lib.html, so edits to it must reload it too."""
    page = os.path.basename(urlparse(referer or "").path)
    return os.path.join(WEB_DIR, page if page in WATCHED_PAGES else "index.html")


class LiveHandler(BaseHTTPRequestHandler):
    """Returns mtime of the page that asks (Referer) for live-reload."""
    def do_GET(self):
        try:
            mtime = os.path.getmtime(watch_file_for(self.headers.get("Referer")))
        except OSError:
            mtime = 0
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(str(mtime).encode())

    def log_message(self, *args):
        pass
