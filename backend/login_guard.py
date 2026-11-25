"""Limit failed logins per visitor (owner 25.11.2026: the panel's password could be guessed
endlessly, and behind it are terminals with full power over the server).

MAX_FAILS wrong passwords within WINDOW seconds -> that address is refused for BAN seconds
without its password being checked. Kept in memory: a restart forgets it (bans are short
anyway). Other visitors are never affected, so a stranger can't lock the owner out from
another address."""
import ipaddress
import sys
import threading
import time

MAX_FAILS = 5
WINDOW = 15 * 60
BAN = 15 * 60
MAX_KEYS = 10_000          # memory bound against a flood of addresses


def _ip(text):
    try:
        return ipaddress.ip_address(text.strip())
    except ValueError:
        return None


def _internal(ip):
    return ip.is_private or ip.is_loopback or ip.is_link_local


def client_key(peer, forwarded_for):
    """The visitor's address. A public TCP peer IS the visitor (its own X-Forwarded-For could
    say anything). Behind our own proxy (loopback/private peer) the proxy appended the
    visitor to X-Forwarded-For: take it from the right, skipping internal hops (a sandbox's
    own Caddy behind the host's). Nothing usable -> the peer."""
    peer_ip = _ip(peer or "")
    if peer_ip is None or not _internal(peer_ip):
        return peer
    for part in reversed((forwarded_for or "").split(",")):
        ip = _ip(part)
        if ip is not None and not _internal(ip):
            return str(ip)
    return peer


class LoginGuard:
    def __init__(self, max_fails=MAX_FAILS, window=WINDOW, ban=BAN, clock=time.time,
                 max_keys=MAX_KEYS):
        self.max_fails, self.window, self.ban = max_fails, window, ban
        self.clock, self.max_keys = clock, max_keys
        self._fails = {}      # key -> [timestamps within the window]
        self._banned = {}     # key -> banned until
        self._lock = threading.Lock()

    def banned_for(self, key):
        """Seconds left of this visitor's ban, 0 if none."""
        with self._lock:
            until = self._banned.get(key, 0)
            left = until - self.clock()
            if left <= 0:
                self._banned.pop(key, None)
                return 0
            return int(left) + 1

    def failed(self, key):
        """Count a wrong password; True when this one starts a ban."""
        with self._lock:
            now = self.clock()
            recent = [t for t in self._fails.get(key, []) if now - t < self.window]
            recent.append(now)
            if len(recent) >= self.max_fails:
                self._fails.pop(key, None)
                self._banned[key] = now + self.ban
                self._trim()
                print(f"[login-guard] {key}: {len(recent)} failed logins, refused for "
                      f"{self.ban // 60} min", file=sys.stderr, flush=True)
                return True
            self._fails[key] = recent
            self._trim()
            return False

    def succeeded(self, key):
        with self._lock:
            self._fails.pop(key, None)

    def size(self):
        with self._lock:
            return len(self._fails) + len(self._banned)

    def _trim(self):
        # drop the oldest entries first (dicts keep insertion order); bans are kept longest
        while len(self._fails) + len(self._banned) > self.max_keys and self._fails:
            self._fails.pop(next(iter(self._fails)))
        while len(self._banned) > self.max_keys:
            self._banned.pop(next(iter(self._banned)))
