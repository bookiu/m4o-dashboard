import asyncio
from contextlib import aclosing

import pytest
from aiohttp import web

from m4o_dashboard.api import APIError, MihomoClient, segment
from m4o_dashboard.config import Settings


async def test_snapshot_selection_encoding_modes_and_delay(client, demo, monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
    version, configs, proxies = await client.snapshot()
    assert version["version"] == "demo-1.0"
    assert configs["mode"] == "rule"
    assert demo.group in proxies
    await client.select_proxy(demo.group, "日本 / Tokyo")
    assert demo.proxies[demo.group]["now"] == "日本 / Tokyo"
    assert demo.calls[-1][1] == f"/proxies/{segment(demo.group)}"
    assert "%2F" in demo.calls[-1][1]
    delay = await client.test_delay("日本 / Tokyo")
    assert delay > 0
    assert "timeout=5000" in demo.calls[-1][1]
    await client.set_mode("global")
    assert demo.mode == "global"
    with pytest.raises(APIError, match="Unsupported mode"):
        await client.set_mode("bad")
    await client.select_proxy("自动选择", demo.nodes[1])
    assert demo.proxies["自动选择"]["fixed"] == demo.nodes[1]
    await client.unpin_proxy("自动选择")
    assert demo.proxies["自动选择"]["fixed"] == ""


async def test_close_connections_and_no_content(client, demo):
    key = next(iter(demo.connections))
    await client.close_connection(key)
    assert key not in demo.connections
    await client.close_connection()
    assert not demo.connections


@pytest.mark.parametrize("stream_name", ["traffic", "connections", "logs"])
async def test_real_websocket_frames_and_cleanup(client, stream_name):
    async with aclosing(client.stream(stream_name)) as stream:
        frame = await asyncio.wait_for(anext(stream), 2)
        assert isinstance(frame, dict)
        assert frame


async def test_authentication_and_no_secret_in_error(demo):
    async with MihomoClient(Settings(url=demo.url, secret="WRONG-SECRET")) as client:
        with pytest.raises(APIError) as error:
            await client.snapshot()
        assert error.value.status == 401
        assert "WRONG-SECRET" not in str(error.value)


async def test_http_errors_bad_json_and_redirects(serve):
    async def bad_json(request):
        return web.Response(text="not-json")

    async def redirect(request):
        raise web.HTTPFound("/secret-target")

    async def slow(request):
        await asyncio.sleep(1.2)
        return web.json_response({})

    async def missing(request):
        return web.json_response({"message": "sensitive body"}, status=500)

    server = web.Application()
    server.add_routes(
        [
            web.get("/json", bad_json),
            web.get("/redirect", redirect),
            web.get("/slow", slow),
            web.get("/error", missing),
        ]
    )
    url = await serve(server)
    async with MihomoClient(Settings(url=url, timeout=1)) as client:
        with pytest.raises(APIError):
            await client.request("GET", "/json")
        with pytest.raises(APIError, match="Redirect refused"):
            await client.request("GET", "/redirect")
        with pytest.raises(APIError, match="timed out"):
            await client.request("GET", "/slow")
        with pytest.raises(APIError) as error:
            await client.request("GET", "/error")
        assert error.value.status == 500
        assert "sensitive body" not in str(error.value)


async def test_reconnect_skips_bad_frames_and_header_auth(serve):
    handshakes = []

    async def stream(request):
        handshakes.append(request.headers.get("Authorization"))
        assert "token" not in request.query and "secret" not in request.query
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_str("invalid json")
        await ws.send_json([])
        await ws.send_json({"up": len(handshakes), "down": 2})
        await ws.close()
        return ws

    server = web.Application()
    server.router.add_get("/traffic", stream)
    url = await serve(server)
    statuses = []
    async with MihomoClient(
        Settings(url=url, secret="test"), lambda *args: statuses.append(args)
    ) as client:
        async with aclosing(client.stream("traffic")) as frames:
            first = await asyncio.wait_for(anext(frames), 3)
            second = await asyncio.wait_for(anext(frames), 3)
    assert first["up"] == 1
    assert second["up"] == 2
    assert handshakes == ["Bearer test", "Bearer test"]
    assert any(not connected and "invalid frame" in reason for _, connected, reason in statuses)
    assert any(not connected and "retrying" in reason for _, connected, reason in statuses)
    assert client.session.closed


async def test_close_handshake_is_bounded_for_write_only_mihomo_stream(serve):
    async def write_only(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        try:
            # Reproduce kernels that keep sending data but never read CLOSE/PING.
            while True:
                await ws.send_json({"up": 1, "down": 2})
                await asyncio.sleep(0.03)
        except ConnectionError:
            return ws

    server = web.Application()
    server.router.add_get("/traffic", write_only)
    url = await serve(server)
    async with MihomoClient(Settings(url=url)) as client:
        stream = client.stream("traffic")
        assert (await asyncio.wait_for(anext(stream), 2))["up"] == 1
        await asyncio.wait_for(stream.aclose(), 2)
        assert not client._websockets


async def test_auth_backoff_cancellable_without_retry_storm(demo):
    failed = asyncio.Event()
    statuses = []

    def status(*args):
        statuses.append(args)
        if not args[1]:
            failed.set()

    async with MihomoClient(Settings(url=demo.url, secret="wrong"), status) as client:
        async with aclosing(client.stream("logs")) as stream:
            pending = asyncio.create_task(anext(stream))
            await asyncio.wait_for(failed.wait(), 2)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
    assert len(statuses) == 1
    assert "401" in statuses[0][2]
    assert "10s" in statuses[0][2]
