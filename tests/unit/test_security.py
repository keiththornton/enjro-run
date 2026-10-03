import asyncio

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from strixrun.security import SecurityMiddleware, session_cookie_value
from strixrun.settings import Settings, generate_api_key, hash_api_key

KEY = generate_api_key()
SECRET = b"s" * 32


def build(settings: Settings) -> FastAPI:
    app = FastAPI()

    @app.get("/")
    def index():
        return {"page": "shell"}

    @app.get("/assets/app.js")
    def asset():
        return {"js": True}

    @app.get("/admin/ps")
    def ps(request: Request):
        return {"key_id": request.scope["state"]["key_id"]}

    @app.post("/admin/models/x/unload")
    def unload():
        return {"ok": True}

    @app.post("/admin/session")
    async def session(request: Request):
        body = await request.json()
        resp = JSONResponse({"ok": body.get("key") == KEY})
        resp.set_cookie("strixrun_session", session_cookie_value(SECRET, settings.api_key_hash),
                        httponly=True, samesite="strict")
        return resp

    @app.get("/v1/models")
    def models():
        return {"object": "list", "data": []}

    @app.post("/v1/chat/completions")
    async def chat():
        async def gen():
            for i in range(3):
                yield f"data: {i}\n\n"
                await asyncio.sleep(0.3)

        return StreamingResponse(gen(), media_type="text/event-stream")

    app.add_middleware(SecurityMiddleware, settings=lambda: settings, session_secret=SECRET,
                       port=lambda: 8790)
    return app


def client(app, peer="127.0.0.1", host="127.0.0.1:8790"):
    transport = httpx.ASGITransport(app=app, client=(peer, 50000))
    return httpx.AsyncClient(transport=transport, base_url=f"http://{host}")


LOCAL = Settings()  # loopback, no key
LAN = Settings(host="0.0.0.0", api_key_hash=hash_api_key(KEY))
LAN_STRICT = Settings(host="0.0.0.0", api_key_hash=hash_api_key(KEY), trust_loopback=False)


# ---- host guard (DNS rebinding) ---------------------------------------------------------


@pytest.mark.parametrize("host", ["evil.com:8790", "evil.com", "127.0.0.1.evil.com:8790"])
async def test_rebinding_host_rejected(host):
    async with client(build(LOCAL), host=host) as c:
        r = await c.get("/admin/ps")
    assert r.status_code == 403
    assert "host" in r.json()["error"]["message"].lower()


@pytest.mark.parametrize("host", ["127.0.0.1:8790", "localhost:8790", "[::1]:8790", "LOCALHOST:8790"])
async def test_loopback_hosts_allowed(host):
    async with client(build(LOCAL), host=host) as c:
        assert (await c.get("/admin/ps")).status_code == 200


async def test_lan_ip_host_allowed_when_listening_on_lan(monkeypatch):
    monkeypatch.setattr("strixrun.security.local_addresses", lambda: {"192.168.1.10"})
    async with client(build(LAN), peer="192.168.1.20", host="192.168.1.10:8790") as c:
        r = await c.get("/v1/models", headers={"Authorization": f"Bearer {KEY}"})
    assert r.status_code == 200


# ---- origin / content-type guard (CSRF) --------------------------------------------------


async def test_cross_site_origin_post_rejected():
    async with client(build(LOCAL)) as c:
        r = await c.post("/admin/models/x/unload", json={},
                         headers={"Origin": "https://evil.com"})
    assert r.status_code == 403


async def test_same_origin_post_allowed():
    async with client(build(LOCAL)) as c:
        r = await c.post("/admin/models/x/unload", json={},
                         headers={"Origin": "http://127.0.0.1:8790"})
    assert r.status_code == 200


async def test_text_plain_admin_post_rejected():
    async with client(build(LOCAL)) as c:
        r = await c.post("/admin/models/x/unload", content=b"{}",
                         headers={"Content-Type": "text/plain"})
    assert r.status_code == 415


async def test_cross_site_v1_post_rejected():
    async with client(build(LOCAL)) as c:
        r = await c.post("/v1/chat/completions", json={}, headers={"Origin": "http://evil.com"})
    assert r.status_code == 403


async def test_no_cors_headers_anywhere():
    async with client(build(LOCAL)) as c:
        r = await c.get("/v1/models", headers={"Origin": "http://127.0.0.1:8790"})
        r2 = await c.options("/v1/models", headers={"Origin": "http://evil.com",
                                                     "Access-Control-Request-Method": "POST"})
    for resp in (r, r2):
        assert not any(h.lower().startswith("access-control-") for h in resp.headers)


# ---- auth matrix ------------------------------------------------------------------------


async def test_no_key_loopback_allowed():
    async with client(build(LOCAL)) as c:
        r = await c.get("/admin/ps")
    assert r.status_code == 200 and r.json()["key_id"] == "loopback"


async def test_no_key_lan_peer_rejected():
    async with client(build(LOCAL), peer="192.168.1.20", host="localhost:8790") as c:
        r = await c.get("/v1/models")
    assert r.status_code in (401, 403)


async def test_key_lan_needs_key():
    async with client(build(LAN), peer="192.168.1.20", host="localhost:8790") as c:
        assert (await c.get("/admin/ps")).status_code == 401
        assert (await c.get("/v1/models", headers={"Authorization": "Bearer wrong"})).status_code == 401
        r = await c.get("/admin/ps", headers={"Authorization": f"Bearer {KEY}"})
        assert r.status_code == 200 and r.json()["key_id"] == "key"
        assert (await c.get("/v1/models", headers={"x-api-key": KEY})).status_code == 200


async def test_401_is_openai_shaped_with_www_authenticate():
    async with client(build(LAN), peer="192.168.1.20", host="localhost:8790") as c:
        r = await c.get("/v1/models")
    assert r.json()["error"]["type"] == "authentication_error"
    assert r.headers["www-authenticate"].startswith("Bearer")


async def test_trust_loopback_toggle():
    async with client(build(LAN)) as c:
        assert (await c.get("/admin/ps")).status_code == 200
    async with client(build(LAN_STRICT)) as c:
        assert (await c.get("/admin/ps")).status_code == 401
        assert (await c.get("/admin/ps", headers={"Authorization": f"Bearer {KEY}"})).status_code == 200


async def test_shell_is_public_from_lan():
    async with client(build(LAN), peer="192.168.1.20", host="localhost:8790") as c:
        assert (await c.get("/")).status_code == 200
        assert (await c.get("/assets/app.js")).status_code == 200
        assert (await c.get("/models")).status_code == 404  # unknown SPA path still reaches the app


async def test_session_cookie_flow():
    async with client(build(LAN), peer="192.168.1.20", host="localhost:8790") as c:
        r = await c.post("/admin/session", json={"key": KEY})
        assert r.status_code == 200
        cookie = r.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=strict" in cookie
        r = await c.get("/admin/ps")  # cookie jar sends it
        assert r.status_code == 200 and r.json()["key_id"] == "session"


async def test_forged_cookie_rejected():
    async with client(build(LAN), peer="192.168.1.20", host="localhost:8790") as c:
        c.cookies.set("strixrun_session", "forged")
        assert (await c.get("/admin/ps")).status_code == 401


async def test_rotated_key_invalidates_cookie():
    settings = Settings(host="0.0.0.0", api_key_hash=hash_api_key(KEY))
    app = build(settings)
    old_cookie = session_cookie_value(SECRET, settings.api_key_hash)
    settings.api_key_hash = hash_api_key(generate_api_key())
    async with client(app, peer="192.168.1.20", host="localhost:8790") as c:
        c.cookies.set("strixrun_session", old_cookie)
        assert (await c.get("/admin/ps")).status_code == 401


# ---- streaming passes through incrementally ---------------------------------------------


async def test_streaming_flushes_through_middleware():
    from tests.integration.conftest import serve_app

    async with serve_app(build(LOCAL)) as url:
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        times = []
        async with httpx.AsyncClient() as c:
            async with c.stream("POST", f"{url}/v1/chat/completions", json={}) as r:
                async for line in r.aiter_lines():
                    if line.startswith("data:"):
                        times.append(loop.time() - t0)
    assert len(times) == 3
    assert times[0] < times[-1] - 0.4  # first event arrived long before the last
