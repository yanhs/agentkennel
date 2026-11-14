"""Keys for the turnstile: find one in a pasted text, keep it on the host, check it with
Anthropic. A secret lives only in <root>/keys/<name>.json (0600, dir 0700); it is never
returned by anything here except get()/owner(), and check() never echoes it back."""
import http.client
import json
import os
import re
import tempfile
import time
from pathlib import Path

KEY_RE = re.compile(r"sk-ant-(api|oat)\d{2}-[A-Za-z0-9_\-]{20,}")
KINDS = ("api", "oauth")
_BODY_CAP = 64 * 1024


def classify(text):
    """(kind, secret) for the first Anthropic key or token in the text, else None."""
    m = KEY_RE.search(text or "")
    if not m:
        return None
    return ("api" if m.group(1) == "api" else "oauth", m.group(0))


def last4(secret):
    """The last four characters; a secret shorter than 12 characters is fully masked."""
    return secret[-4:] if len(secret or "") >= 12 else "…"


def _check_name(name):
    if (not isinstance(name, str) or not name or "/" in name or "\\" in name
            or ".." in name or name.startswith("_") or "\0" in name):
        raise ValueError(f"bad key name: {name!r}")


class KeyStore:
    def __init__(self, root):
        self.dir = Path(root) / "keys"

    def _read(self, path):
        try:
            d = json.loads(path.read_text())
        except (OSError, ValueError):
            return None
        s = d.get("secret") if isinstance(d, dict) else None
        return d if isinstance(s, str) and s else None

    def _write(self, path, doc):
        self.dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.dir, 0o700)
        fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=".tmp-")
        try:
            with os.fdopen(fd, "w") as f:
                os.fchmod(f.fileno(), 0o600)
                json.dump(doc, f)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def get(self, name):
        _check_name(name)
        d = self._read(self.dir / f"{name}.json")
        if not d or d.get("kind") not in KINDS or not d.get("secret"):
            return None
        return d["kind"], d["secret"]

    def put(self, name, kind, secret):
        _check_name(name)
        if kind not in KINDS:
            raise ValueError(f"bad key kind: {kind!r}")
        self._write(self.dir / f"{name}.json",
                    {"kind": kind, "secret": secret, "saved": int(time.time())})

    def drop(self, name):
        _check_name(name)
        (self.dir / f"{name}.json").unlink(missing_ok=True)

    def owner(self):
        d = self._read(self.dir / "_owner.json")
        return (d or {}).get("secret") or None

    def set_owner(self, key):
        self._write(self.dir / "_owner.json",
                    {"kind": "api", "secret": key, "saved": int(time.time())})

    def drop_owner(self):
        (self.dir / "_owner.json").unlink(missing_ok=True)


def _scrub(text):
    return re.sub(r"sk-ant-\S*", "…", text)


def check(kind, secret, upstream=("https", "api.anthropic.com", 443), timeout=20):
    """(True, "") when Anthropic accepts the key, else (False, why). Never contains the secret."""
    if kind not in KINDS:
        raise ValueError(f"bad key kind: {kind!r}")
    if not secret:
        raise ValueError("empty secret")
    scheme, host, port = upstream
    if kind == "api":
        method, path, body = "GET", "/v1/models?limit=1", None
        headers = {"x-api-key": secret, "anthropic-version": "2023-06-01"}
    else:
        method, path = "POST", "/v1/messages"
        body = json.dumps({"model": "claude-haiku-4-5", "max_tokens": 1,
                           "messages": [{"role": "user", "content": "hi"}]}).encode()
        headers = {"Authorization": f"Bearer {secret}", "anthropic-version": "2023-06-01",
                   "anthropic-beta": "oauth-2025-04-20", "content-type": "application/json"}
    cls = http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection
    conn = None
    try:
        conn = cls(host, port, timeout=timeout)
        conn.request(method, path, body=body, headers=headers)
        resp = conn.getresponse()
        raw = resp.read(_BODY_CAP)
        status = resp.status
    except ValueError:  # e.g. CR/LF in the secret: http.client refuses the header
        return False, "the key has characters that cannot be sent"
    except Exception as e:  # network, TLS, timeout
        return False, "can't reach Anthropic: " + _scrub(str(e)).replace(secret, "…")
    finally:
        if conn:
            conn.close()
    if 200 <= status < 300:
        return True, ""
    msg = ""
    try:
        m = json.loads(raw).get("error", {}).get("message")
        msg = m if isinstance(m, str) else ""
    except (ValueError, AttributeError):
        pass
    msg = _scrub(msg.replace(secret, "…")).strip()
    return False, msg[:300] or f"HTTP {status}"
