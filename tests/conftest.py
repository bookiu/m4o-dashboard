import asyncio

import pytest
from aiohttp import web
from textual.widgets import Static

from m4o_dashboard.api import MihomoClient
from m4o_dashboard.app import Dashboard
from m4o_dashboard.config import Settings
from m4o_dashboard.demo import DemoServer


@pytest.fixture
async def demo():
    async with DemoServer() as server:
        yield server


async def eventually(predicate):
    async with asyncio.timeout(4):
        while not predicate():  # noqa: ASYNC110
            await asyncio.sleep(0.03)


@pytest.fixture
async def dashboard(demo):
    app = Dashboard(Settings(url=demo.url, secret=demo.secret, refresh_interval=1))
    async with app.run_test(size=(120, 36), notifications=True) as pilot:
        await eventually(lambda: all(value[0] for value in app.statuses.values()))
        await eventually(
            lambda: "已连接" in app.query_one("#network-status", Static).render().plain
        )
        await pilot.pause()
        yield app, pilot


@pytest.fixture
async def client(demo):
    async with MihomoClient(Settings(url=demo.url, secret=demo.secret)) as api:
        yield api


@pytest.fixture
async def serve():
    runners = []

    async def start(app):
        runner = web.AppRunner(app, access_log=None, shutdown_timeout=0.1)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", 0).start()
        runners.append(runner)
        return f"http://127.0.0.1:{runner.addresses[0][1]}"

    yield start
    for runner in runners:
        await runner.cleanup()
