"""In-process, loopback-only mihomo simulator for demos and integration tests.

It never contacts an actual controller and never creates proxy connections.
"""

from __future__ import annotations

import asyncio
import math
import secrets
from contextlib import suppress
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from aiohttp import web


class DemoServer:
    def __init__(self) -> None:
        self.secret = secrets.token_urlsafe(24)
        self.url = ""
        self.calls: list[tuple[str, str, Any]] = []
        self.websockets: set[web.WebSocketResponse] = set()
        self.runner: web.AppRunner | None = None
        self.ticker: asyncio.Task | None = None
        self.tick = 0
        self.mode = "rule"
        self.up = 128_000
        self.down = 2_048_000
        self.upload_total = 12_000_000
        self.download_total = 980_000_000
        self.nodes = ["香港 HK-01", "日本 / Tokyo", "Singapore 🇸🇬", "DIRECT"]
        self.group = "🚀 节点 / Proxy"
        self.proxies: dict[str, dict] = {
            node: {
                "name": node,
                "type": "Direct" if node == "DIRECT" else "Shadowsocks",
                "udp": True,
                "alive": True,
                "history": [{"delay": 25 + index * 31}],
            }
            for index, node in enumerate(self.nodes)
        }
        self.proxies.update(
            {
                self.group: {
                    "name": self.group,
                    "type": "Selector",
                    "all": self.nodes.copy(),
                    "now": self.nodes[0],
                },
                "自动选择": {
                    "name": "自动选择",
                    "type": "URLTest",
                    "all": self.nodes[:3],
                    "now": self.nodes[0],
                    "fixed": "",
                },
                "负载均衡": {"name": "负载均衡", "type": "LoadBalance", "all": self.nodes[:3]},
            }
        )
        self.connections = {
            f"demo-{index:04d}": {
                "id": f"demo-{index:04d}",
                "metadata": {
                    "host": ("github.com", "example.org", "video.example.com")[index % 3],
                    "destinationPort": "443",
                    "network": "tcp" if index % 3 else "udp",
                    "sourceIP": "192.168.1.10",
                    "sourcePort": str(50000 + index),
                    "destinationIP": "198.51.100.10",
                    "process": ("firefox", "curl", "mpv")[index % 3],
                },
                "upload": index * 12345,
                "download": index * 345678,
                "start": datetime.now(UTC).isoformat(),
                "chains": [self.nodes[0], self.group],
                "rule": "DomainSuffix",
                "rulePayload": "example.com",
            }
            for index in range(1, 21)
        }

    async def __aenter__(self) -> DemoServer:
        @web.middleware
        async def authenticate(request: web.Request, handler):
            if not secrets.compare_digest(
                request.headers.get("Authorization", ""), f"Bearer {self.secret}"
            ):
                return web.json_response({"message": "Unauthorized"}, status=401)
            return await handler(request)

        app = web.Application(middlewares=[authenticate])
        app.add_routes(
            [
                web.get("/version", self.version),
                web.get("/configs", self.configs),
                web.patch("/configs", self.configs),
                web.get("/proxies", self.list_proxies),
                web.get("/proxies/{name}/delay", self.delay),
                web.get("/proxies/{name}", self.proxy),
                web.put("/proxies/{name}", self.proxy),
                web.delete("/proxies/{name}", self.proxy),
                web.get("/traffic", self.traffic),
                web.get("/logs", self.logs),
                web.get("/connections", self.list_connections),
                web.delete("/connections", self.close_connections),
                web.delete("/connections/{id}", self.close_connections),
            ]
        )
        app.on_shutdown.append(self.shutdown)
        self.runner = web.AppRunner(app, access_log=None, shutdown_timeout=1)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.url = f"http://127.0.0.1:{self.runner.addresses[0][1]}"
        self.ticker = asyncio.create_task(self.advance())
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self.ticker:
            self.ticker.cancel()
            with suppress(asyncio.CancelledError):
                await self.ticker
        if self.runner:
            await self.runner.cleanup()

    async def shutdown(self, app: web.Application) -> None:
        for websocket in list(self.websockets):
            await websocket.close(code=1001, message=b"Demo stopped")

    async def advance(self) -> None:
        while True:
            await asyncio.sleep(1)
            self.tick += 1
            self.down = int((2.5 + math.sin(self.tick / 4)) * 1024 * 1024)
            self.up = int((0.2 + 0.1 * math.cos(self.tick / 3)) * 1024 * 1024)
            self.upload_total += self.up
            self.download_total += self.down
            for index, connection in enumerate(self.connections.values(), start=1):
                connection["download"] += int(self.down / (index * 8))
                connection["upload"] += int(self.up / (index * 5))

    async def version(self, request: web.Request) -> web.Response:
        return web.json_response({"meta": True, "version": "demo-1.0"})

    async def configs(self, request: web.Request) -> web.Response:
        if request.method == "PATCH":
            data = await request.json()
            self.calls.append(("PATCH", request.raw_path, data))
            if data.get("mode") not in {"rule", "global", "direct"}:
                raise web.HTTPBadRequest()
            self.mode = data["mode"]
            return web.Response(status=204)
        return web.json_response({"mode": self.mode, "log-level": "debug", "mixed-port": 7890})

    async def list_proxies(self, request: web.Request) -> web.Response:
        return web.json_response({"proxies": self.proxies})

    async def proxy(self, request: web.Request) -> web.Response:
        name = request.match_info["name"]
        proxy = self.proxies.get(name)
        if proxy is None:
            raise web.HTTPNotFound()
        if request.method == "GET":
            return web.json_response(proxy)
        if request.method == "DELETE":
            self.calls.append(("DELETE", request.raw_path, None))
            if proxy["type"] not in {"URLTest", "Fallback"}:
                raise web.HTTPBadRequest()
            proxy["fixed"] = ""
            return web.Response(status=204)
        data = await request.json()
        self.calls.append(("PUT", request.raw_path, data))
        if proxy["type"] not in {"Selector", "URLTest", "Fallback"} or data.get(
            "name"
        ) not in proxy.get("all", []):
            raise web.HTTPBadRequest()
        proxy["now"] = data["name"]
        if proxy["type"] != "Selector":
            proxy["fixed"] = data["name"]
        return web.Response(status=204)

    async def delay(self, request: web.Request) -> web.Response:
        name = request.match_info["name"]
        if name not in self.proxies:
            raise web.HTTPNotFound()
        self.calls.append(("GET", request.raw_path, None))
        delay = 20 + sum(name.encode()) % 140
        self.proxies[name]["history"] = [{"delay": delay}]
        return web.json_response({"delay": delay})

    def connection_snapshot(self) -> dict:
        return {
            "connections": list(deepcopy(self.connections).values()),
            "uploadTotal": self.upload_total,
            "downloadTotal": self.download_total,
            "memory": 48 * 1024 * 1024,
        }

    async def list_connections(self, request: web.Request) -> web.StreamResponse:
        if request.headers.get("Upgrade", "").lower() == "websocket":
            interval = max(0.05, int(request.query.get("interval", "1000")) / 1000)
            return await self.stream(request, self.connection_snapshot, interval)
        return web.json_response(self.connection_snapshot())

    async def close_connections(self, request: web.Request) -> web.Response:
        self.calls.append(("DELETE", request.raw_path, None))
        if "id" in request.match_info:
            self.connections.pop(request.match_info["id"], None)
        else:
            self.connections.clear()
        return web.Response(status=204)

    async def traffic(self, request: web.Request) -> web.StreamResponse:
        return await self.stream(
            request,
            lambda: {
                "up": self.up,
                "down": self.down,
                "upTotal": self.upload_total,
                "downTotal": self.download_total,
            },
            1,
        )

    async def logs(self, request: web.Request) -> web.StreamResponse:
        counter = 0

        def entry() -> dict:
            nonlocal counter
            counter += 1
            level = ("info", "debug", "info", "warning", "error")[counter % 5]
            return {
                "type": level,
                "payload": (
                    f"[TCP] 192.168.1.10:{50000 + counter} → example.com:443 "
                    f"match DomainSuffix using {self.group}[{self.proxies[self.group]['now']}]"
                ),
            }

        return await self.stream(request, entry, 0.35)

    async def stream(
        self, request: web.Request, snapshot, interval: float
    ) -> web.WebSocketResponse:
        websocket = web.WebSocketResponse(heartbeat=20)
        await websocket.prepare(request)
        self.websockets.add(websocket)

        async def send() -> None:
            while not websocket.closed:
                await websocket.send_json(snapshot())
                await asyncio.sleep(interval)

        task = asyncio.create_task(send())
        try:
            # Consume close/ping frames so shutdown is immediate and clean.
            async for _ in websocket:
                pass
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError, ConnectionError):
                await task
            self.websockets.discard(websocket)
        return websocket
