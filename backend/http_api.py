"""The main API on :3011 (Handler). The tmux buffer (:3045) and the page version (:3014)
are in backend/live.py.

Part of status_server (the facade re-exports every name here)."""

from http.server import BaseHTTPRequestHandler
import json
import subprocess
import time
from urllib.parse import parse_qs, urlparse

from backend.terminals import (
    detect_project_from_cwd, get_cwd, get_pane_pid, get_system_stats, is_working, load_agents,
    parse_pane, SAMPLE_INTERVAL, save_agents, SESSIONS, tree_cpu_ticks
)
from backend.telegram import tg_action, tg_notify_action, tg_render
from backend.slots_api import slot_post
from backend.updates import upd_post, UPD_ROUTE, upd_state
from backend.library_api import (
    LIB_BODY_MAX, lib_listing, lib_origin_ok, lib_post, LIB_ROUTE, LibError
)
from backend.server_tab import (
    SERVER_COLLECTOR, SERVER_ROUTE, svc_listing, svc_log, svc_post, SVC_ROUTE
)
from backend.guests import GUEST_ENTRY_ROUTE, GUEST_ENTRY_SPENT, GUESTS, GUESTS_ROUTE
from backend.quota import (
    CLAUDE_JOBS, QUOTA, Quota, quota_claude_body, QUOTA_CLAUDE_BODY_MAX, QUOTA_CLAUDE_ROUTE,
    QUOTA_ROUTE, quota_terminals, QUOTA_TERMS_ROUTE
)


class Handler(BaseHTTPRequestHandler):
    def _server_get(self):
        try:
            snap = SERVER_COLLECTOR.snapshot() or SERVER_COLLECTOR.sample()
            code, data = 200, snap
        except Exception as e:
            code, data = 500, {"error": str(e)[:200]}
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _route(self):
        return urlparse(getattr(self, "path", "/")).path.rstrip("/") or "/"

    def _lib_json(self, code, data):
        # same-origin only: no Access-Control-Allow-Origin on library responses
        self._json_response(code, data, cors=False)

    def _library_get(self):
        if self._route() != LIB_ROUTE:
            return self._lib_json(404, {"error": "not found"})
        qs = parse_qs(urlparse(self.path).query)
        yes = lambda k: qs.get(k, [""])[0].lower() in ("1", "true", "yes")   # noqa: E731
        try:
            self._lib_json(200, lib_listing(include_archived=yes("archived"), trash=yes("trash")))
        except Exception as e:
            self._lib_json(500, {"error": str(e)[:200]})

    def _write_body(self):
        """The JSON object of a same-origin write (the library's CSRF rule), or LibError."""
        ctype = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if ctype != "application/json":
            raise LibError(415, "Content-Type must be application/json")
        origin = self.headers.get("Origin")
        if origin is not None and not lib_origin_ok(origin, self.headers):
            raise LibError(403, "cross-origin request refused")
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length > LIB_BODY_MAX:
            raise LibError(413, "body too large")
        raw = self.rfile.read(length) if length > 0 else b""
        try:
            body = json.loads(raw) if raw.strip() else {}
        except ValueError:
            raise LibError(400, "body is not JSON")
        if not isinstance(body, dict):
            raise LibError(400, "body must be a JSON object")
        return body

    def _is_services(self):
        r = self._route()
        return r == SVC_ROUTE or r.startswith(SVC_ROUTE + "/")

    def _services(self, method):
        route = self._route()
        try:
            if method == "GET" and route == SVC_ROUTE:
                data = svc_listing()
            elif method == "GET" and route.startswith(SVC_ROUTE + "/log/"):
                data = svc_log(route[len(SVC_ROUTE) + 5:])
            elif method == "POST" and route.startswith(SVC_ROUTE + "/"):
                self._write_body()
                data = svc_post(route[len(SVC_ROUTE) + 1:])
            else:
                raise LibError(404, "not found")
            self._lib_json(200, data)
        except LibError as e:
            self._lib_json(e.code, {"error": str(e)})
        except Exception as e:
            self._lib_json(500, {"error": str(e)[:200]})

    def _is_updates(self):
        r = self._route()
        return r == UPD_ROUTE or r.startswith(UPD_ROUTE + "/")

    def _updates(self, method):
        """GET /api/update; POST /api/update/<check|start|cancel|auto|seen> (same-origin)."""
        route = self._route()
        try:
            if method == "GET" and route == UPD_ROUTE:
                data = upd_state()
            elif method == "POST" and route.startswith(UPD_ROUTE + "/"):
                data = upd_post(route[len(UPD_ROUTE) + 1:], self._write_body())
            else:
                raise LibError(404, "not found")
            self._lib_json(200, data)
        except LibError as e:
            self._lib_json(e.code, {"error": str(e), **e.extra})
        except Exception as e:
            self._lib_json(500, {"error": str(e)[:200]})

    def _is_guests(self):
        r = self._route()
        return r == GUESTS_ROUTE or r.startswith(GUESTS_ROUTE + "/")

    def _guests(self, method):
        route = self._route()
        if method == "GET" and route == GUEST_ENTRY_ROUTE:
            return self._guest_enter()
        try:
            if method == "GET" and route == GUESTS_ROUTE:
                data = GUESTS.state()
            elif method == "POST" and route.startswith(GUESTS_ROUTE + "/"):
                body = self._write_body()
                what = route[len(GUESTS_ROUTE) + 1:]
                if what == "check":
                    GUESTS.check()
                    data = {"ok": True}
                elif what == "setup":
                    data = GUESTS.setup(body)
                elif what == "open":
                    data = GUESTS.open(body)
                elif what == "rules":
                    data = GUESTS.rules(body)
                elif what == "folders":
                    data = GUESTS.folders(body)
                elif what == "claude":
                    data = GUESTS.claude(body)
                elif what == "settings":
                    data = GUESTS.settings_set(body)
                elif what == "owner-key":
                    data = GUESTS.owner_key_set(body)
                else:
                    data = GUESTS.act(what, body)
            else:
                raise LibError(404, "not found")
            self._lib_json(200, data)
        except LibError as e:
            self._lib_json(e.code, {"error": str(e)})
        except Exception as e:
            self._lib_json(500, {"error": str(e)[:200]})

    def _guest_enter(self):
        """GET /api/guests/enter?t= — comes only through a guest's nginx (/_owner-entry), so
        the cookie lands on the guest's address. Nothing else of the panel is reachable there."""
        ticket = parse_qs(urlparse(getattr(self, "path", "/")).query).get("t", [""])[0]
        e = GUESTS.enter(ticket, self.headers.get("Host", ""))
        if not e:
            body = GUEST_ENTRY_SPENT.encode("utf-8")
            self.send_response(403)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
        else:
            body = b""
            self.send_response(302)
            self.send_header("Set-Cookie", f"{e['cookie']}; Path=/; Max-Age={e['max_age']}; "
                                           "HttpOnly; SameSite=Lax; Secure")
            self.send_header("Location", "/")
            self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def _library_post(self):
        route = self._route()
        if not route.startswith(LIB_ROUTE + "/"):
            return self._lib_json(404, {"error": "not found"})
        try:
            body = self._write_body()
            self._lib_json(200, lib_post(route[len(LIB_ROUTE) + 1:], body))
        except LibError as e:
            self._lib_json(e.code, {"error": str(e), **e.extra})
        except Exception as e:
            self._lib_json(500, {"error": str(e)[:200]})

    def _is_library(self):
        r = self._route()
        return r == LIB_ROUTE or r.startswith(LIB_ROUTE + "/")

    def do_GET(self):
        if self._is_library():
            return self._library_get()
        if self._route() == SERVER_ROUTE:
            return self._server_get()
        if self._route() == QUOTA_ROUTE:
            return self._lib_json(200, QUOTA.get())
        if self._route() == QUOTA_CLAUDE_ROUTE:
            return self._quota_claude_job()
        if self._route() == QUOTA_TERMS_ROUTE:
            return self._lib_json(200, quota_terminals())
        if self._is_services():
            return self._services("GET")
        if self._is_guests():
            return self._guests("GET")
        if self._is_updates():
            return self._updates("GET")
        if (urlparse(getattr(self, "path", "/")).path.rstrip("/") or "/") == "/telegram":
            body = tg_render().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        agents_meta = load_agents()

        # Phase 1: gather data + first CPU sample
        session_data = {}
        for t in SESSIONS:
            sid = t["id"]
            session_name = t["session"]

            r = subprocess.run(
                ["tmux", "has-session", "-t", "=" + session_name],   # exact, not a prefix
                capture_output=True,
            )
            if r.returncode != 0:
                session_data[sid] = {"active": False}
                continue

            pane_pid = get_pane_pid(session_name)
            cwd = get_cwd(session_name)
            pane_project, pane_task = parse_pane(session_name)

            # CWD-based project as fallback
            cwd_project = detect_project_from_cwd(cwd)
            auto_project = pane_project or cwd_project
            auto_task = pane_task

            if not pane_pid:
                session_data[sid] = {
                    "active": True,
                    "auto_project": auto_project,
                    "auto_task": auto_task,
                }
                continue

            session_data[sid] = {
                "active": True,
                "pane_pid": pane_pid,
                "ticks1": tree_cpu_ticks(pane_pid),
                "auto_project": auto_project,
                "auto_task": auto_task,
            }

        # Phase 2: CPU sampling
        time.sleep(SAMPLE_INTERVAL)

        # Phase 3: build response
        result = {}
        for t in SESSIONS:
            sid = t["id"]
            data = session_data[sid]
            meta = agents_meta.get(sid, {})
            active = data.get("active", False)
            working = False

            if "pane_pid" in data:
                working = is_working(data["ticks1"], tree_cpu_ticks(data["pane_pid"]))

            auto_proj = data.get("auto_project", "")
            auto_task = data.get("auto_task", "")

            saved_proj = meta.get("project", "")
            locked = meta.get("locked", False)

            # Auto-detect: set project and lock when found
            if not locked and auto_proj:
                if sid not in agents_meta:
                    agents_meta[sid] = {}
                agents_meta[sid]["project"] = auto_proj
                agents_meta[sid]["locked"] = True
                save_agents(agents_meta)
                saved_proj = auto_proj
                locked = True

            result[sid] = {
                "active": active,
                "working": working,
                "path": t["path"],
                "project": saved_proj,
                "auto_project": auto_proj,
                "task": meta.get("task") or auto_task,
                "locked": locked,
                # Per-agent overrides — UI uses these to render the model /
                # effort selectors. Empty string = "use default".
                "model": meta.get("model", ""),
                "effort": meta.get("effort", ""),
            }

        result["_system"] = get_system_stats()
        # Respect the user's saved _order:
        #   - missing key OR empty list → fresh install / new browser → start with
        #     the first four agents (add more with the "+ Claude" button)
        #   - non-empty list → echo it exactly, only stripping ids that no
        #     longer exist as a SESSION (e.g. leftover "k1" from a removed
        #     setup). DO NOT re-add ids the user explicitly removed with ×,
        #     or "closed" agents come back after every poll/refresh.
        all_ids = [t["id"] for t in SESSIONS]
        stored = agents_meta.get("_order")
        if isinstance(stored, list) and stored:
            result["_order"] = [i for i in stored if i in all_ids]
        else:
            result["_order"] = all_ids[:4]
        self._json_response(200, result)

    def _telegram_post(self):
        length = int(self.headers.get("Content-Length", 0))
        form = parse_qs(self.rfile.read(length).decode("utf-8"))
        token = form.get("token", [""])[0].strip()
        owner = form.get("owner", [""])[0].strip()
        action = form.get("action", ["start"])[0]
        if action == "notify":
            msg = tg_notify_action(form.get("notify_token", [""])[0])
        else:
            msg = tg_action(action, token, owner)
        out = tg_render(msg).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def _quota_claude(self):
        """POST /api/quota/claude: the guest's "Your Claude" dialog, passed to the
        turnstile with the pass. Only behind a turnstile; the body is never logged."""
        try:
            src = Quota.source()
            if src is None or src[0] != "guest":
                raise LibError(404, "not found")
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                raise LibError(400, "bad Content-Length")
            if length > QUOTA_CLAUDE_BODY_MAX:       # before a byte of it is read
                self.close_connection = True
                raise LibError(413, "body too large")
            ctype = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
            if ctype != "application/json":
                raise LibError(415, "Content-Type must be application/json")
            origin = self.headers.get("Origin")
            if origin is not None and not lib_origin_ok(origin, self.headers):
                raise LibError(403, "cross-origin request refused")
            raw = self.rfile.read(length) if length > 0 else b""
            job = CLAUDE_JOBS.start(src[1], src[2], raw, quota_claude_body(raw))
            del raw
            if job is None:
                raise LibError(429, "too many checks at once; try again in a moment")
            self._lib_json(202, {"job": job})
        except LibError as e:
            self._lib_json(e.code, {"error": str(e)})
        except Exception as e:                       # never the body: only what failed
            self._lib_json(500, {"error": f"failed ({type(e).__name__})"})

    def _quota_claude_job(self):
        """GET /api/quota/claude?job=<id>: {"pending": true} or {"status", "body"}."""
        src = Quota.source()
        if src is None or src[0] != "guest":
            return self._lib_json(404, {"error": "not found"})
        job = parse_qs(urlparse(self.path).query).get("job", [""])[0]
        d = CLAUDE_JOBS.get(job) if job else None
        if d is None:
            return self._lib_json(404, {"error": "no such check (it may have expired)"})
        return self._lib_json(200, d)

    def do_POST(self):
        if self._route() == QUOTA_CLAUDE_ROUTE:
            return self._quota_claude()
        if self._is_library():
            return self._library_post()
        if self._is_services():
            return self._services("POST")
        if self._is_guests():
            return self._guests("POST")
        if self._is_updates():
            return self._updates("POST")
        if (urlparse(getattr(self, "path", "/")).path.rstrip("/") or "/") == "/telegram":
            return self._telegram_post()
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))

        code, data = slot_post(body)
        self._json_response(code, data)

    def do_OPTIONS(self):
        self.send_response(204)
        if (self._is_library() or self._is_services() or self._is_guests()
                or self._is_updates() or self._route() == SERVER_ROUTE):   # no CORS preflight
            self.end_headers()
            return
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _json_response(self, code, data, cors=True):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-cache")
        if cors:
            self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

    def log_message(self, *args):
        pass
