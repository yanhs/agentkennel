"""The files guests hand out by link — sbx.<domain>/f/<guest>-<key>/<file> — when Caddy
carries the guests' addresses (guests/webchoice.py).

Caddy's own file_server follows symbolic links: a guest who puts `ln -s /home/<owner>/.cf_token
x` into /work/public would get the owner's file served (Caddy runs as the owner). So Caddy
proxies /f/ here instead, to 127.0.0.1 only, and this keeps the nginx template's promises:

  disable_symlinks on   every path component is opened with O_NOFOLLOW from the folder
                        before it (openat), so no link is followed, not even a relative one
  autoindex off         a folder is never listed; <folder>/ serves its index.html if any,
                        <folder> redirects to <folder>/ (as nginx does)
  alias guests/public/  nothing above that folder can be named ("..", "." and empty parts
                        are refused before anything is opened)
  add_header ... always the same four headers on every answer
  (static file)         GET / HEAD, Last-Modified + If-Modified-Since, single byte ranges;
                        only regular files (a pipe or a device a guest makes is not opened)

Started by sitegate.py next to the site gate (PUBLIC_FILES_PORT, default 8795).
"""

from __future__ import annotations

import email.utils
import mimetypes
import os
import re
import stat
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Optional, Tuple
from urllib.parse import unquote, urlsplit

PREFIX = "/f/"
HEADERS = (("Content-Security-Policy", "sandbox"), ("X-Content-Type-Options", "nosniff"),
           ("X-Robots-Tag", "noindex, nofollow"), ("Cache-Control", "no-cache"))
RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")
CHUNK = 256 * 1024


class NotFound(Exception):
    pass


class Redirect(Exception):
    pass


def open_file(root, rel: str) -> Tuple[int, os.stat_result]:
    """(fd, stat) of the regular file `rel` under `root`, never through a symlink.
    `rel` is the decoded path after /f/; a trailing "/" means the folder's index.html.
    Raises NotFound, or Redirect for a folder named without its trailing "/"."""
    if "\x00" in rel:
        raise NotFound
    parts = rel.split("/")
    folder_index = parts[-1] == ""
    if folder_index:
        parts[-1] = "index.html"
    if any(p in ("", ".", "..") for p in parts) or len(parts) < 2:
        raise NotFound                       # /f/ itself, /f/<key> alone, //, ., ..
    fds = []
    try:
        fds.append(os.open(str(root), os.O_RDONLY | os.O_DIRECTORY))
        for part in parts[:-1]:
            try:
                fds.append(os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                   dir_fd=fds[-1]))
            except OSError:
                raise NotFound
        name = parts[-1]
        try:
            st = os.stat(name, dir_fd=fds[-1], follow_symlinks=False)
        except OSError:
            raise NotFound
        if stat.S_ISDIR(st.st_mode) and not folder_index:
            raise Redirect
        if not stat.S_ISREG(st.st_mode):
            raise NotFound
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_NOCTTY,
                         dir_fd=fds[-1])
        except OSError:
            raise NotFound
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):     # swapped between stat and open
            os.close(fd)
            raise NotFound
        return fd, st
    finally:
        for f in fds:
            os.close(f)


def byte_range(header: Optional[str], size: int):
    """(start, end) inclusive for one "bytes=" range; None = the whole file;
    "bad" = unsatisfiable (416)."""
    m = RANGE_RE.match((header or "").strip())
    if not m or (not m.group(1) and not m.group(2)):
        return None
    first, last = m.group(1), m.group(2)
    if not first:                                   # the last N bytes
        n = int(last)
        if n == 0 or size == 0:
            return "bad"
        return max(0, size - n), size - 1
    start = int(first)
    end = min(int(last), size - 1) if last else size - 1
    if start >= size or end < start:
        return "bad"
    return start, end


def make_handler(root) -> type:
    class Handler(BaseHTTPRequestHandler):
        server_version = "agentkennel-publicfiles"
        sys_version = ""

        def _head(self, status: int, extra=()) -> None:
            self.send_response(status)
            for k, v in HEADERS:
                self.send_header(k, v)
            for k, v in extra:
                self.send_header(k, v)
            self.end_headers()

        def _plain(self, status: int, text: str = "", extra=()) -> None:
            body = text.encode()
            self._head(status, (("Content-Type", "text/plain; charset=utf-8"),
                                ("Content-Length", str(len(body))), *extra))
            if self.command != "HEAD":
                self.wfile.write(body)

        def _serve(self) -> None:
            raw = urlsplit(self.path).path
            if not raw.startswith(PREFIX):
                return self._plain(404, "404 not found\n")
            try:
                fd, st = open_file(root, unquote(raw[len(PREFIX):], errors="strict"))
            except Redirect:
                return self._plain(301, "", (("Location", raw + "/"),))
            except (NotFound, UnicodeDecodeError):
                return self._plain(404, "404 not found\n")
            try:
                self._send_file(fd, st, raw)
            finally:
                os.close(fd)

        def _send_file(self, fd: int, st: os.stat_result, raw: str) -> None:
            size, mtime = st.st_size, int(st.st_mtime)
            last_mod = email.utils.formatdate(mtime, usegmt=True)
            ims = self.headers.get("If-Modified-Since")
            if ims:
                try:
                    if mtime <= int(email.utils.parsedate_to_datetime(ims).timestamp()):
                        return self._head(304, (("Last-Modified", last_mod),))
                except (TypeError, ValueError, OverflowError):
                    pass
            ctype = mimetypes.guess_type(raw.rstrip("/") or "x")[0] if not raw.endswith("/") \
                else "text/html"
            common = (("Content-Type", ctype or "application/octet-stream"),
                      ("Last-Modified", last_mod), ("Accept-Ranges", "bytes"))
            rng = byte_range(self.headers.get("Range"), size)
            if rng == "bad":
                return self._plain(416, "", (("Content-Range", f"bytes */{size}"),))
            if rng is None:
                start, end, status, extra = 0, size - 1, 200, ()
            else:
                start, end = rng
                status, extra = 206, (("Content-Range", f"bytes {start}-{end}/{size}"),)
            length = max(0, end - start + 1)
            self._head(status, (*common, ("Content-Length", str(length)), *extra))
            if self.command == "HEAD":
                return
            os.lseek(fd, start, os.SEEK_SET)
            left = length
            while left > 0:
                chunk = os.read(fd, min(CHUNK, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)

        def do_GET(self):
            self._serve()

        def do_HEAD(self):
            self._serve()

        def _refuse(self):
            self._plain(405, "405 not allowed\n", (("Allow", "GET, HEAD"),))

        do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _refuse

        def log_message(self, *args) -> None:
            pass

    return Handler


def make_server(bind: str, port: int, root) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((bind, port), make_handler(Path(root)))
    server.daemon_threads = True
    return server


def start_in_background(bind: str, port: int, root,
                        log: Callable[[str], None] = lambda m: print(m, file=sys.stderr, flush=True),
                        retry: float = 30) -> Optional[ThreadingHTTPServer]:
    """The server in a thread. A busy port isn't fatal (the site gate goes on): it is
    tried again every `retry` seconds (0: once) in the background, and None is returned."""
    def bind_now() -> Optional[ThreadingHTTPServer]:
        try:
            server = make_server(bind, port, root)
        except OSError:
            return None
        threading.Thread(target=server.serve_forever, name="publicfiles", daemon=True).start()
        log(f"publicfiles on {bind}:{port} ({root})")
        return server

    server = bind_now()
    if server is not None:
        return server
    log(f"publicfiles: {bind}:{port} not available; sbx.<domain>/f/ on Caddy won't answer "
        f"until it is" + (f" (trying again every {retry:g} s)" if retry else ""))
    if retry:
        def again() -> None:
            while bind_now() is None:
                time.sleep(retry)
        threading.Thread(target=again, name="publicfiles-bind", daemon=True).start()
    return None
