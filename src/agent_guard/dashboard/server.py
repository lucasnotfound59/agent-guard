"""The loopback HTTP server behind `guard dashboard`: one static page and a read-only JSON API.

Security model (from FTC Knowledge Bank's dashboard):
- listens on 127.0.0.1 only, on a free port unless --port is given;
- a fresh random token per run, handed over in the link's #fragment (never sent to the server by the
  browser, never logged); every API call must send it as `Authorization: Bearer <token>`;
- the Host header must be the exact 127.0.0.1:<port> (stops DNS rebinding), a foreign Origin or a
  cross-site fetch is refused, and POST additionally requires this dashboard's own Origin and JSON;
- rules never change here: approving and rejecting stay in `guard review`, in the user's own terminal. The
  Toolbox (skills, MCP servers, plugins, AGENTS.md) can change files, each change backed up and logged.
"""

from __future__ import annotations

import hmac
import json
import secrets as token_source  # the standard library module; agent_guard.secrets is the gitleaks wrapper
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import SplitResult, parse_qsl, urlsplit

from agent_guard.dashboard import data, toolbox
from agent_guard.paths import contract, log_file
from agent_guard.rules import global_rules

API_VERSION = 1
STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
}
ROUTES = {
    "/api/version": "GET",
    "/api/overview": "GET",
    "/api/rules": "GET",
    "/api/activity": "GET",
    "/api/proposals": "GET",
    "/api/health": "GET",
    "/api/toolbox": "GET",
    "/api/text": "GET",
    "/api/check": "POST",
    "/api/dry-run": "POST",
    "/api/manage": "POST",
}
MAX_BODY = data.MAX_CHECK_TEXT + 64 * 1024
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
       "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


class Problem(Exception):
    def __init__(self, status: int, code: str, message: str, headers: dict[str, str] | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.headers = status, code, message, headers or {}


def _int(value: str | None, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError:
        raise Problem(400, "malformed_request", f"{value!r} is not a whole number") from None


class Dashboard:
    """State shared by every request: the token, the caches, and the bound socket."""

    def __init__(self, port: int = 0):
        self.token = token_source.token_urlsafe(32)
        self.log = data.LogCache()
        self.health = data.HealthCache()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), _handler(self))
        self.httpd.daemon_threads = True

    @property
    def port(self) -> int:
        return int(self.httpd.server_address[1])

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def url(self) -> str:
        return f"{self.origin}/#token={self.token}"

    def serve_forever(self) -> None:
        self.httpd.serve_forever()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def get(self, path: str, q: dict[str, str]) -> dict[str, Any]:
        window = q.get("window", "7d")
        if path == "/api/version":
            return {"pending": len(global_rules("candidate")), "logPath": contract(log_file()),
                    "logRecords": len(self.log.records())}
        if path == "/api/overview":
            return data.overview(self.log, window)
        if path == "/api/rules":
            return data.rules(self.log, window, q.get("repo"))
        if path == "/api/activity":
            return data.activity(self.log, window, q, _int(q.get("offset"), 0), _int(q.get("limit"), 50))
        if path == "/api/proposals":
            return data.proposals(self.log, window)
        if path == "/api/health":
            return data.health(self.log, self.health, window, force=q.get("refresh") == "1")
        if path == "/api/toolbox":
            return toolbox.inventory()
        if path == "/api/text":
            kind = q.get("kind", "")
            if kind not in ("agents_md", "skill"):
                raise Problem(400, "malformed_request", "kind must be agents_md or skill")
            return toolbox.read_text(kind, q.get("name", ""), q.get("agent", ""))
        raise Problem(404, "not_found", "Unknown dashboard route")

    def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        if path == "/api/check":
            return data.check(body)
        if path == "/api/dry-run":
            return data.dry_run(str(body.get("id") or ""))
        if path == "/api/manage":
            return toolbox.manage(body)
        raise Problem(404, "not_found", "Unknown dashboard route")


def _handler(app: Dashboard) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "agent-guard-dashboard"
        sys_version = ""

        def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
            return  # no access log: the terminal shows the link and errors only (errors still go to log_error)

        def do_GET(self) -> None:
            self._dispatch("GET")

        def do_HEAD(self) -> None:
            self._dispatch("HEAD")

        def do_POST(self) -> None:
            self._dispatch("POST")

        def do_PUT(self) -> None:
            self._dispatch("PUT")

        def do_DELETE(self) -> None:
            self._dispatch("DELETE")

        def do_PATCH(self) -> None:
            self._dispatch("PATCH")

        def do_OPTIONS(self) -> None:
            self._dispatch("OPTIONS")

        # ------------------------------------------------------------ dispatch

        def _dispatch(self, method: str) -> None:
            path = self.path.split("?", 1)[0]
            try:
                self._guard()
                parts = urlsplit(self.path)
                if parts.path.startswith("/api/"):
                    self._api(method, parts)
                elif method in ("GET", "HEAD"):
                    self._static(parts.path, head=method == "HEAD")
                else:
                    raise Problem(405, "method_not_allowed", "The page is GET only", {"Allow": "GET, HEAD"})
            except Problem as p:
                self._error(p.status, p.code, p.message, p.headers)
            except toolbox.Conflict as e:
                self._error(409, "conflict", str(e))
            except data.BadRequest as e:
                self._error(400, "bad_request", str(e))
            except Exception as e:  # report it, keep serving other requests
                print(f"agent-guard dashboard: {method} {path} failed: {type(e).__name__}: {e}", file=sys.stderr)
                self._error(500, "internal_error", "The dashboard hit an error; the details are in its terminal.")

        def _guard(self) -> None:
            if self.headers.get_all("Host") != [f"127.0.0.1:{app.port}"]:
                raise Problem(403, "forbidden_host", "Open the dashboard with the exact 127.0.0.1 link it printed.")
            origins = self.headers.get_all("Origin")
            if origins is not None and origins != [app.origin]:
                raise Problem(403, "forbidden_origin", "Requests from other sites are refused.")
            if any(v.strip().lower() == "cross-site" for v in self.headers.get_all("Sec-Fetch-Site") or []):
                raise Problem(403, "forbidden_origin", "Cross-site requests are refused.")

        def _authorized(self) -> bool:
            sent = self.headers.get_all("Authorization") or []
            return len(sent) == 1 and hmac.compare_digest(sent[0].encode(), f"Bearer {app.token}".encode())

        def _api(self, method: str, parts: SplitResult) -> None:
            if not self._authorized():
                raise Problem(401, "unauthorized", "Missing or wrong token. Open the full link `guard dashboard` printed.")
            expected = ROUTES.get(parts.path)
            if expected is None:
                raise Problem(404, "not_found", "Unknown dashboard route")
            if method != expected:
                raise Problem(405, "method_not_allowed", f"Use {expected}", {"Allow": expected})
            query = self._query(parts.query)
            if method == "GET":
                payload = app.get(parts.path, query)
            else:
                if query:
                    raise Problem(400, "malformed_request", "POST takes a JSON body, not query parameters")
                if self.headers.get_all("Origin") != [app.origin]:
                    raise Problem(403, "forbidden_origin", "POST requires this dashboard's Origin")
                ctype = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
                if ctype != "application/json":
                    raise Problem(415, "malformed_request", "POST requires application/json")
                payload = app.post(parts.path, self._body())
            self._json(200, {"apiVersion": API_VERSION, "ok": True, "version": data.version(app.log), **payload})

        def _query(self, raw: str) -> dict[str, str]:
            out: dict[str, str] = {}
            try:
                pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=bool(raw))
            except ValueError:
                raise Problem(400, "malformed_request", "Invalid query string") from None
            for k, v in pairs:
                if k in out:
                    raise Problem(400, "malformed_request", f"Duplicate query parameter {k!r}")
                out[k] = v
            return out

        def _body(self) -> dict[str, Any]:
            length = self.headers.get("Content-Length")
            if length is None or not length.isdigit():
                raise Problem(411, "malformed_request", "Content-Length is required")
            if int(length) > MAX_BODY:
                raise Problem(413, "body_too_large", "The request body is too large")
            raw = self.rfile.read(int(length))
            try:
                body = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise Problem(400, "malformed_request", "The body must be one JSON object") from None
            if not isinstance(body, dict):
                raise Problem(400, "malformed_request", "The body must be one JSON object")
            return body

        # ------------------------------------------------------------ responses

        def _static(self, path: str, head: bool) -> None:
            entry = STATIC.get(path)
            if entry is None:
                raise Problem(404, "not_found", "Not found")
            name, ctype = entry
            body = (STATIC_DIR / name).read_bytes()
            extra = {"Content-Security-Policy": CSP} if name.endswith(".html") else {}
            self._send(200, ctype, body, extra, head=head)

        def _error(self, status: int, code: str, message: str, headers: dict[str, str] | None = None) -> None:
            self._json(status, {"apiVersion": API_VERSION, "ok": False, "error": {"code": code, "message": message}},
                       headers)

        def _json(self, status: int, payload: dict[str, Any], headers: dict[str, str] | None = None) -> None:
            body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            self._send(status, "application/json; charset=utf-8", body, headers or {}, head=self.command == "HEAD")

        def _send(self, status: int, ctype: str, body: bytes, headers: dict[str, str], head: bool = False) -> None:
            self.send_response(status)
            for k, v in {
                "Content-Type": ctype,
                "Content-Length": str(len(body)),
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "X-Frame-Options": "DENY",
                "Cross-Origin-Resource-Policy": "same-origin",
                **headers,
            }.items():
                self.send_header(k, v)
            self.end_headers()
            if not head:
                self.wfile.write(body)

    return Handler


def serve(port: int = 0, open_browser: bool = True) -> int:
    try:
        app = Dashboard(port)
    except (OSError, OverflowError) as e:
        print(f"guard dashboard: cannot listen on 127.0.0.1:{port}: {e}", file=sys.stderr)
        return 1
    print(f"agent-guard dashboard: {app.url}")
    print("The Toolbox can change agent configs, skills and AGENTS.md (each change backed up and logged); rules "
          "can't change here. The link holds this run's token, so keep it to yourself. Ctrl+C stops it.", flush=True)
    if open_browser and not webbrowser.open(app.url):
        print("Couldn't open a browser; open the link above yourself.", flush=True)
    try:
        app.serve_forever()
    except KeyboardInterrupt:
        print("\nagent-guard dashboard stopped.")
    finally:
        app.httpd.server_close()
    return 0
