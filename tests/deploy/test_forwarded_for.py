"""X-Forwarded-For is trusted only from the configured proxy (deploy/serve.py, CAUTELA_TRUSTED_PROXY).

The per-address rate limits key on the client address the app sees (api/app.py). Behind OpenLiteSpeed that address
must come from X-Forwarded-For, but only when the connection comes from the proxy: from anywhere else the header is
the caller's own claim and would let one client spread its requests over made-up addresses. These tests build the
server configuration exactly as `serve.main` does and send requests through the middleware stack uvicorn wraps
around the app. No network, no bundle."""

from __future__ import annotations

import asyncio

import pytest

from deploy import serve

PROXY = "10.83.30.1"
VISITOR = "203.0.113.7"
DIRECT = "198.51.100.9"


def served_config(monkeypatch, tmp_path, trusted: str | None):
    """The uvicorn config `serve.main` builds, around an app that records the client address it sees."""
    seen: list[str] = []

    async def app(scope, receive, send):
        seen.append(scope["client"][0])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    for name in ("CAUTELA_BUNDLE_DIR", "CAUTELA_BUNDLE_LOCK", "CAUTELA_TRUSTED_PROXY"):
        monkeypatch.delenv(name, raising=False)
    if trusted is not None:
        monkeypatch.setenv("CAUTELA_TRUSTED_PROXY", trusted)
    monkeypatch.setenv("CAUTELA_DEMO_MODE", "0")  # no reset timer, no audit cleanup
    monkeypatch.setenv("CAUTELA_AUDIT_DIR", str(tmp_path / "audit"))
    monkeypatch.setattr(serve, "check_runtime_files", lambda: None)
    monkeypatch.setattr(serve, "build_app", lambda settings: app)
    captured = []
    monkeypatch.setattr(serve.uvicorn.Server, "run", lambda self: captured.append(self.config))
    serve.main()
    config = captured[0]
    config.load()
    return config, seen


def client_seen(config, seen: list[str], peer: str, forwarded: str | None) -> str:
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded else []
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET", "scheme": "http",
             "path": "/health", "raw_path": b"/health", "query_string": b"", "root_path": "", "headers": headers,
             "client": (peer, 40000), "server": ("127.0.0.1", 8000)}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        return None

    asyncio.run(config.loaded_app(scope, receive, send))
    return seen[-1]


def test_forwarded_address_is_used_when_the_connection_comes_from_the_proxy(monkeypatch, tmp_path):
    config, seen = served_config(monkeypatch, tmp_path, PROXY)
    assert config.proxy_headers is True
    assert client_seen(config, seen, PROXY, VISITOR) == VISITOR


def test_forwarded_header_from_anyone_else_is_ignored(monkeypatch, tmp_path):
    config, seen = served_config(monkeypatch, tmp_path, PROXY)
    assert client_seen(config, seen, DIRECT, VISITOR) == DIRECT, "a caller cannot choose its own rate-limit bucket"
    assert client_seen(config, seen, DIRECT, None) == DIRECT


@pytest.mark.parametrize("peer", (PROXY, DIRECT))
def test_without_configuration_only_loopback_is_trusted(monkeypatch, tmp_path, peer):
    config, seen = served_config(monkeypatch, tmp_path, None)
    assert client_seen(config, seen, peer, VISITOR) == peer
    assert client_seen(config, seen, "127.0.0.1", VISITOR) == VISITOR
