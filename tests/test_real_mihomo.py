"""Opt-in contract test against an isolated installed mihomo executable.

Run with M4O_TEST_MIHOMO=1 pytest tests/test_real_mihomo.py. No live configuration
is read, no proxy listener is enabled, and no external network access is needed.
"""

import asyncio
import os
import secrets
import shutil
import socket
from contextlib import aclosing

import pytest

from m4o_dashboard.api import APIError, MihomoClient
from m4o_dashboard.config import Settings

pytestmark = pytest.mark.skipif(
    os.environ.get("M4O_TEST_MIHOMO") != "1" or shutil.which("mihomo") is None,
    reason="opt in with M4O_TEST_MIHOMO=1 and install mihomo",
)


async def test_real_mihomo_contract(tmp_path):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    secret = secrets.token_hex(24)
    group = "测试 / Proxy 🚀"
    config = tmp_path / "config.yaml"
    config.write_text(
        f"external-controller: 127.0.0.1:{port}\nsecret: '{secret}'\n"
        "mode: rule\nlog-level: debug\nallow-lan: false\nipv6: false\n"
        "dns:\n  enable: false\nprofile:\n  store-selected: false\n"
        "proxy-groups:\n"
        f"  - name: '{group}'\n    type: select\n    proxies: [DIRECT, REJECT]\n"
        "rules:\n  - MATCH,DIRECT\n",
        encoding="utf-8",
    )
    with (tmp_path / "mihomo.log").open("wb") as log:
        process = await asyncio.create_subprocess_exec(
            shutil.which("mihomo"),
            "-d",
            str(tmp_path),
            "-f",
            str(config),
            stdout=log,
            stderr=log,
        )
        try:
            async with MihomoClient(
                Settings(url=f"http://127.0.0.1:{port}", secret=secret)
            ) as client:
                async with asyncio.timeout(8):
                    while True:
                        if process.returncode is not None:
                            pytest.fail("Isolated mihomo exited before becoming ready")
                        try:
                            version, configs, proxies = await client.snapshot()
                            break
                        except APIError:
                            await asyncio.sleep(0.1)
                assert version["meta"]
                assert configs["mode"] == "rule"
                assert group in proxies
                await client.select_proxy(group, "REJECT")
                _, _, proxies = await client.snapshot()
                assert proxies[group]["now"] == "REJECT"
                await client.select_proxy(group, "DIRECT")
                await client.set_mode("direct")
                assert (await client.request("GET", "/configs"))["mode"] == "direct"
                for name in ("traffic", "connections"):
                    async with aclosing(client.stream(name)) as frames:
                        data = await asyncio.wait_for(anext(frames), 3)
                        assert isinstance(data, dict)
                        assert "up" in data if name == "traffic" else "connections" in data
                # Logs can legitimately be idle. Verify the authenticated handshake.
                logs = await client.session.ws_connect(f"{client.settings.url}/logs?level=debug")
                try:
                    assert not logs.closed
                finally:
                    await client.close_websocket(logs)
                await client.close_connection()  # Only this isolated kernel's empty list.
        finally:
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 5)
                except TimeoutError:
                    process.kill()
                    await process.wait()
