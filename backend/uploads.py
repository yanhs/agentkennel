"""Image paste and 📎 file upload (:3047): save the bytes, hand the terminal a path.

Part of status_server (the facade re-exports every name here)."""

from http.server import BaseHTTPRequestHandler
import json
import time
import os
import re
import unicodedata
from urllib.parse import parse_qs, urlparse
import envcompat

from backend import _HERE
from backend.auth import _verify_token, AUTH_COOKIE


# ── Clipboard image paste → save to tgimg, hand the terminal a file path ─────
# A web terminal (ttyd/tmux) can't accept a pasted image — it's text-only. So the
# dashboard intercepts an image paste, POSTs the bytes here, and types the saved
# file PATH into the terminal so the agent (Claude Code) can Read the image.

# Where pasted images are saved, and (optionally) the public base URL they're served at.
# Default: inside the repo (.sessions/paste — the persisted volume in Docker), no public
# URL → the response's "url" is the local path. The dashboard only uses "path" (typed into
# the terminal so the agent can Read it). A server that serves the folder publicly sets
# AGENTKENNEL_PASTE_DIR + AGENTKENNEL_PASTE_URL (TG_FILES_DIR_IMG is the legacy name for the dir).
PASTE_DIR = (envcompat.get("PASTE_DIR") or os.environ.get("TG_FILES_DIR_IMG")
             or os.path.join(_HERE, ".sessions", "paste"))
PASTE_URL = envcompat.get("PASTE_URL", "").rstrip("/")
_PASTE_EXT = {
    "image/png": ".png", "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/gif": ".gif",
    "image/webp": ".webp", "image/bmp": ".bmp", "image/svg+xml": ".svg",
}


def _paste_ext(ctype):
    return _PASTE_EXT.get((ctype or "").split(";")[0].strip().lower())


def save_paste_image(data: bytes, ctype: str, token: str, dest_dir: str | None = None) -> dict:
    """Save pasted image bytes to dest_dir as paste_<token><ext>; return {path,url}
    (url = PASTE_URL/<name>, or the local path when no public URL is configured)."""
    dest_dir = dest_dir or PASTE_DIR
    ext = _paste_ext(ctype)
    if not ext:
        raise ValueError(f"unsupported content-type: {ctype!r}")
    if not data:
        raise ValueError("empty body")
    name = f"paste_{token}{ext}"
    os.makedirs(dest_dir, exist_ok=True)
    with open(os.path.join(dest_dir, name), "wb") as f:
        f.write(data)
    path = os.path.join(dest_dir, name)
    return {"path": path, "url": f"{PASTE_URL}/{name}" if PASTE_URL else path}


# ── 📎 File: any file from the browser → a PRIVATE folder, the terminal gets its path ──
# A path, not a link: the agent runs on this machine and reads the file directly, and a
# public link would expose whatever was uploaded (client documents, keys). Files keep
# their (made safe) name under <UPLOAD_DIR>/<YYYY-MM-DD>/, folder 0700, files 0600.
UPLOAD_DIR = (envcompat.get("UPLOAD_DIR")
              or os.path.join(_HERE, ".sessions", "uploads"))
UPLOAD_MAX = int(float(envcompat.get("UPLOAD_MAX_MB", "100")) * 1024 * 1024)


def _upload_name(raw):
    """The file's own name, safe to type into a terminal: its last path part only, letters
    (any script), digits and . _ - + kept, everything else (spaces, ; $ ` ( ) …) → _,
    no leading dot, at most 120 characters with the extension kept."""
    name = os.path.basename(str(raw or "").replace("\\", "/"))
    name = unicodedata.normalize("NFC", name)
    name = re.sub(r"[^\w.+-]", "_", name)
    name = re.sub(r"_{2,}", "_", name).strip("._") or "file"
    if len(name) > 120:
        stem, ext = os.path.splitext(name)
        ext = ext[:16]
        name = stem[:120 - len(ext)] + ext
    return name


def save_upload(src, length: int, raw_name: str, dest_dir: str | None = None) -> dict:
    """Stream `length` bytes from src into <dest_dir>/<today>/<safe name> (name_2, name_3 …
    when taken); return {path, name, size}. A short body leaves no file behind."""
    if length <= 0:
        raise ValueError("empty body")
    root = dest_dir or UPLOAD_DIR
    day = os.path.join(root, time.strftime("%Y-%m-%d"))
    os.makedirs(day, mode=0o700, exist_ok=True)
    for d in (root, day):
        os.chmod(d, 0o700)
    name = _upload_name(raw_name)
    stem, ext = os.path.splitext(name)
    n = 1
    while True:
        path = os.path.join(day, name if n == 1 else f"{stem}_{n}{ext}")
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            break
        except FileExistsError:
            n += 1
    got = 0
    try:
        with os.fdopen(fd, "wb") as f:
            while got < length:
                chunk = src.read(min(1 << 20, length - got))
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
        if got < length:
            raise ValueError(f"upload cut short: {got} of {length} bytes")
    except BaseException:
        try:
            os.unlink(path)
        except OSError:
            pass
        raise
    return {"path": path, "name": os.path.basename(path), "size": got}


class PasteHandler(BaseHTTPRequestHandler):
    """POST raw image bytes (Content-Type: image/*) → save → {path, url} JSON.
    POST /api/upload?name=<file name> with any bytes → save_upload → {path, name, size}."""

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _upload(self):
        # the proxy gates this too; checked here as well so a route slip can't open it
        cookies = dict(p.strip().split("=", 1) for p in (self.headers.get("Cookie", "") or "").split(";")
                       if "=" in p)
        if not _verify_token(cookies.get(AUTH_COOKIE, "")):
            self._json(401, {"error": "not signed in"}); return
        try:
            length = int(self.headers.get("Content-Length", "") or "-1")
        except ValueError:
            length = -1
        if length < 0:
            self._json(411, {"error": "Content-Length required"}); return
        if length > UPLOAD_MAX:
            self.close_connection = True
            self._json(413, {"error": "file too large", "max_mb": UPLOAD_MAX // (1024 * 1024)}); return
        name = parse_qs(urlparse(self.path).query).get("name", [""])[0]
        try:
            res = save_upload(self.rfile, length, name)
        except ValueError as e:
            self._json(400, {"error": str(e)[:200]}); return
        except OSError as e:
            self._json(500, {"error": str(e)[:200]}); return
        print("UPLOAD-SAVED", time.strftime("%H:%M:%S"), res["path"], res["size"], flush=True)
        self._json(200, res)

    def do_POST(self):
        if urlparse(self.path).path.rstrip("/") == "/api/upload":
            return self._upload()
        try:
            ctype = (self.headers.get("Content-Type", "") or "").split(";")[0].strip().lower()
            length = int(self.headers.get("Content-Length", "0") or "0")
            # diagnostic client log (so we can SEE what happens in the user's browser)
            if ctype == "application/json":
                body = self.rfile.read(length).decode("utf-8", "replace") if 0 < length <= 65536 else ""
                print("PASTE-CLIENTLOG", time.strftime("%H:%M:%S"), body[:600], flush=True)
                self._json(200, {"ok": True}); return
            if length <= 0 or length > 30 * 1024 * 1024:
                self._json(413, {"error": "empty or too large"}); return
            if not _paste_ext(ctype):
                self._json(415, {"error": "not an image"}); return
            data = self.rfile.read(length)
            res = save_paste_image(data, ctype, os.urandom(6).hex())
            print("PASTE-SAVED", time.strftime("%H:%M:%S"), res["path"], flush=True)
            self._json(200, res)
        except Exception as e:
            self._json(500, {"error": str(e)[:200]})

    def log_message(self, *args):
        pass
