"""Small asynchronous mihomo client, independent of Textual.

Streams reconnect independently. Authentication is always a header, never a query
parameter. Redirects are rejected for HTTP requests; environment proxies are not
used. TLS verification is enabled unless explicitly disabled by the operator.
"""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import AsyncIterator, Callable
from typing import Any
from urllib.parse import quote

import aiohttp
from yarl import URL

from .config import Settings

StatusCallback = Callable[[str, bool, str], None]


class APIError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def segment(name: str) -> str:
    """Encode an entire path component, including '/' in a node or group name."""
    return quote(name, safe="")


class MihomoClient:
    def __init__(self, settings: Settings, status: StatusCallback | None = None) -> None:
        self.settings = settings
        self.status = status or (lambda *_: None)
        self.session: aiohttp.ClientSession | None = None
        self._websockets: dict[str, aiohttp.ClientWebSocketResponse] = {}
        self._closing = False

    async def __aenter__(self) -> MihomoClient:
        self.session = aiohttp.ClientSession(
            headers={"Authorization": f"Bearer {self.settings.secret}"}
            if self.settings.secret
            else {},
            timeout=aiohttp.ClientTimeout(total=self.settings.timeout),
            connector=aiohttp.TCPConnector(ssl=self.settings.verify_tls),
            trust_env=False,
        )
        return self

    async def __aexit__(self, *_: Any) -> None:
        self._closing = True
        if self.session is not None:
            await self.session.close()

    def _session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            raise APIError("Client is not connected")
        return self.session

    def _url(self, path: str) -> URL:
        # Preserve percent-encoded slashes; aiohttp must not canonicalize them away.
        return URL(self.settings.url + path, encoded=True)

    def _error(self, status: int) -> APIError:
        if status in (401, 403):
            return APIError(f"HTTP {status}: authentication denied; check MIHOMO_SECRET", status)
        if 300 <= status < 400:
            return APIError("Redirect refused; configure the controller's final URL", status)
        return APIError(f"HTTP {status}: mihomo rejected the request", status)

    def network_error(self, exc: BaseException) -> APIError:
        # Avoid echoing request URLs, headers, or server-controlled response bodies.
        if isinstance(exc, APIError):
            return exc
        if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
            return APIError("Request timed out")
        return APIError(f"Connection error ({type(exc).__name__}); check controller and TLS")

    async def request(
        self,
        method: str,
        path: str,
        *,
        data: dict | None = None,
        params: dict | None = None,
    ) -> dict[str, Any]:
        try:
            async with self._session().request(
                method,
                self._url(path),
                json=data,
                params=params,
                proxy=self.settings.proxy or None,
                allow_redirects=False,
            ) as response:
                if not 200 <= response.status < 300:
                    raise self._error(response.status)
                if response.status == 204:
                    return {}
                value = await response.json(content_type=None)
                if not isinstance(value, dict):
                    raise APIError("Invalid JSON object from controller")
                return value
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            raise self.network_error(exc) from None

    async def snapshot(self) -> tuple[dict, dict, dict]:
        # gather(return_exceptions=True) observes every request before propagating.
        results = await asyncio.gather(
            self.request("GET", "/version"),
            self.request("GET", "/configs"),
            self.request("GET", "/proxies"),
            return_exceptions=True,
        )
        for value in results:
            if isinstance(value, BaseException):
                raise value
        version, configs, proxies = results
        if not isinstance(proxies.get("proxies"), dict):
            raise APIError("Invalid proxy list from controller")
        return version, configs, proxies["proxies"]

    async def select_proxy(self, group: str, node: str) -> None:
        await self.request("PUT", f"/proxies/{segment(group)}", data={"name": node})

    async def unpin_proxy(self, group: str) -> None:
        await self.request("DELETE", f"/proxies/{segment(group)}")

    async def test_delay(self, node: str) -> int:
        result = await self.request(
            "GET",
            f"/proxies/{segment(node)}/delay",
            params={"url": self.settings.test_url, "timeout": 5000},
        )
        delay = result.get("delay")
        if not isinstance(delay, int) or isinstance(delay, bool) or delay <= 0:
            raise APIError("Latency test failed or timed out")
        return delay

    async def set_mode(self, mode: str) -> None:
        if mode not in {"rule", "global", "direct"}:
            raise APIError("Unsupported mode")
        await self.request("PATCH", "/configs", data={"mode": mode})

    async def close_connection(self, connection_id: str | None = None) -> None:
        path = "/connections" + (f"/{segment(connection_id)}" if connection_id else "")
        await self.request("DELETE", path)

    @staticmethod
    async def close_websocket(websocket: aiohttp.ClientWebSocketResponse) -> None:
        # Some mihomo versions only write frames and never reply to CLOSE.
        # aiohttp's per-frame close timeout can reset forever as data arrives.
        # Bound the *entire* handshake; cancellation closes the underlying transport.
        try:
            async with asyncio.timeout(1):
                await websocket.close()
        except TimeoutError:
            pass

    async def reconnect_streams(self) -> None:
        """Invalidate idle streams too when the HTTP health check loses the kernel."""
        await asyncio.gather(*(self.close_websocket(ws) for ws in list(self._websockets.values())))

    async def stream(self, name: str, params: dict | None = None) -> AsyncIterator[dict]:
        """Reconnect until cancelled; malformed frames do not terminate the app.

        Do not require PONGs: some mihomo endpoints are write-only. Continuous
        streams have a receive deadline; logs may legitimately remain idle.
        """
        delay = 0.5
        receive_timeout = (
            None
            if name == "logs"
            else max(5, self.settings.timeout, self.settings.interval / 1000 * 3)
        )
        while not self._closing:
            websocket = None
            try:
                try:
                    websocket = await self._session().ws_connect(
                        self._url(f"/{name}"),
                        params=params,
                        proxy=self.settings.proxy or None,
                        heartbeat=None,
                        timeout=aiohttp.ClientWSTimeout(ws_receive=receive_timeout, ws_close=1),
                        max_msg_size=16 * 1024 * 1024,
                    )
                    self._websockets[name] = websocket
                    self.status(name, True, "live")
                    async for message in websocket:
                        if message.type in (aiohttp.WSMsgType.TEXT, aiohttp.WSMsgType.BINARY):
                            try:
                                value = json.loads(message.data)
                                if not isinstance(value, dict):
                                    raise ValueError("Expected an object")
                            except (ValueError, UnicodeError):
                                self.status(name, False, "invalid frame; waiting for next update")
                                continue
                            delay = 0.5
                            self.status(name, True, "live")
                            yield value
                        elif message.type == aiohttp.WSMsgType.ERROR:
                            raise APIError("WebSocket transport error")
                    raise APIError("Stream closed")
                finally:
                    if websocket is not None:
                        self._websockets.pop(name, None)
                        await self.close_websocket(websocket)
            except asyncio.CancelledError:
                raise
            except (APIError, aiohttp.ClientError, TimeoutError, OSError) as exc:
                if self._closing:
                    return
                if isinstance(exc, aiohttp.WSServerHandshakeError):
                    error = self._error(exc.status)
                else:
                    error = self.network_error(exc)
                # Bad credentials won't improve with a rapid retry loop.
                if error.status in (401, 403, 404):
                    delay = max(delay, 10)
                self.status(name, False, f"{error}; retrying in {delay:g}s")
                await asyncio.sleep(delay + random.uniform(0, delay * 0.1))
                delay = min(delay * 2, 30)
