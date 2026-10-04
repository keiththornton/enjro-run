"""Pure ASGI security middleware: Host guard, Origin/content-type guard, then auth.

Order matters:
1. Host guard: the Host header must name this machine. This blocks DNS rebinding,
   where an attacker's domain resolves to 127.0.0.1 so their page can read our
   responses.
2. Origin guard: non-GET requests from a browser must be same-origin. Admin and
   upstream writes must also be application/json, which a cross-site <form> or
   no-preflight fetch can't send. No CORS headers are ever emitted.
3. Auth: protected prefixes need the key, or a session cookie derived from it.
   Loopback peers are exempt when no key is set, and also when trust_loopback is
   on, except for requests naming a public_hosts name: a tunnel or reverse proxy on
   this machine makes internet traffic arrive from loopback. The dashboard shell is
   public so a LAN browser can load the key prompt.

This is not BaseHTTPMiddleware: it passes `send` straight through, so streaming
responses aren't buffered.
"""

import hashlib
import hmac
import ipaddress
import json
import logging
import socket
from collections.abc import Callable
from http.cookies import SimpleCookie
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

from strixrun.settings import Settings, is_loopback_host, verify_api_key

PROTECTED_PREFIXES = ("/v1", "/admin", "/upstream", "/metrics")
JSON_ONLY_PREFIXES = ("/admin", "/upstream")
PUBLIC_ENDPOINTS = {("POST", "/admin/session")}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
COOKIE_NAME = "strixrun_session"

log = logging.getLogger(__name__)


def session_cookie_value(secret: bytes, key_hash: str | None) -> str:
    """Cookie value bound to the current key: rotating or clearing the key invalidates it."""
    return hmac.new(secret, (key_hash or "").encode(), hashlib.sha256).hexdigest()


def local_addresses() -> set[str]:
    addrs: set[str] = set()
    try:
        import psutil

        for nics in psutil.net_if_addrs().values():
            for a in nics:
                if a.family in (socket.AF_INET, socket.AF_INET6):
                    addrs.add(a.address.split("%", 1)[0].lower())
    except Exception:  # interface enumeration is best-effort
        log.warning("could not list network interfaces", exc_info=True)
    return addrs


def local_hostnames() -> set[str]:
    name = socket.gethostname().lower()
    short = name.split(".", 1)[0]
    return {name, short, f"{short}.local"}


def _split_host(value: str) -> str:
    value = value.strip().lower()
    if value.startswith("["):
        return value[1 : value.find("]")] if "]" in value else value
    return value.rsplit(":", 1)[0] if value.count(":") == 1 else value


def _public_names(s: Settings) -> set[str]:
    return {_split_host(h) for h in s.public_hosts}


def set_session_cookie(resp: Response, request: Request, s: Settings) -> None:
    """Issue the dashboard session cookie, Secure when served at an HTTPS public host."""
    host = _split_host(request.headers.get("host", ""))
    resp.set_cookie(
        COOKIE_NAME, session_cookie_value(request.app.state.session_secret, s.api_key_hash),
        httponly=True, samesite="strict", path="/", max_age=30 * 86400,
        secure=host in _public_names(s),
    )


def _is_loopback_peer(peer: str | None) -> bool:
    if not peer:
        return False
    try:
        return ipaddress.ip_address(peer).is_loopback
    except ValueError:
        return False


class SecurityMiddleware:
    def __init__(self, app: ASGIApp, settings: Callable[[], Settings], session_secret: bytes,
                 port: Callable[[], int]) -> None:
        self.app = app
        self.settings = settings
        self.secret = session_secret
        self.port = port
        self._lan_names: set[str] | None = None

    def _allowed_hosts(self, s: Settings) -> set[str]:
        allowed = {"localhost", "127.0.0.1", "::1"}
        if not is_loopback_host(s.host):
            if self._lan_names is None:
                self._lan_names = local_addresses() | local_hostnames()
            allowed |= self._lan_names
            if s.host not in ("0.0.0.0", "::"):
                allowed.add(s.host.lower())
        allowed |= _public_names(s)
        return allowed

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        s = self.settings()
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        method, path = scope["method"], scope["path"]

        host = _split_host(headers.get("host", ""))
        allowed = self._allowed_hosts(s)
        if host not in allowed:
            await _deny(send, 403, "permission_error", f"host {host or '(none)'!r} is not allowed")
            return

        if method not in SAFE_METHODS:
            origin = headers.get("origin")
            if origin and origin != "null":
                o = urlsplit(origin)
                if (o.hostname or "").lower() not in allowed or o.netloc.lower() != headers.get("host", "").lower():
                    await _deny(send, 403, "permission_error", "cross-origin requests are not allowed")
                    return
            elif origin == "null":
                await _deny(send, 403, "permission_error", "cross-origin requests are not allowed")
                return
            # Only POST can be sent cross-site without a CORS preflight (which we never
            # grant); requiring JSON blocks <form> and no-preflight fetch POSTs.
            if method == "POST" and path.startswith(JSON_ONLY_PREFIXES):
                ctype = headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if ctype != "application/json":
                    await _deny(send, 415, "invalid_request_error",
                                "admin requests must be sent as application/json")
                    return

        peer = scope.get("client")[0] if scope.get("client") else None
        key_id = self._authenticate(s, headers, peer, public=host in _public_names(s))
        protected = path.startswith(PROTECTED_PREFIXES) and (method, path) not in PUBLIC_ENDPOINTS
        if protected and key_id is None:
            if not s.api_key_hash:
                await _deny(send, 403, "permission_error",
                            "access from other machines requires an API key (`strixrun key set`)")
            else:
                await _deny(send, 401, "authentication_error", "a valid API key is required",
                            extra_headers=[(b"www-authenticate", b'Bearer realm="strixrun"')])
            return

        scope.setdefault("state", {})["key_id"] = key_id
        await self.app(scope, receive, send)

    def _authenticate(self, s: Settings, headers: dict[str, str], peer: str | None,
                      public: bool = False) -> str | None:
        loopback = _is_loopback_peer(peer) and not public
        if not s.api_key_hash:
            return "loopback" if loopback else None
        auth = headers.get("authorization", "")
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else headers.get("x-api-key")
        if token and verify_api_key(token, s.api_key_hash):
            return "key"
        cookie = SimpleCookie(headers.get("cookie", "")).get(COOKIE_NAME)
        if cookie and hmac.compare_digest(
            cookie.value, session_cookie_value(self.secret, s.api_key_hash)
        ):
            return "session"
        if loopback and s.trust_loopback:
            return "loopback"
        return None


async def _deny(send: Send, status: int, etype: str, message: str,
                extra_headers: list[tuple[bytes, bytes]] | None = None) -> None:
    body = json.dumps({"error": {"message": message, "type": etype, "code": status}}).encode()
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [(b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()), *(extra_headers or [])],
    })
    await send({"type": "http.response.body", "body": body})
