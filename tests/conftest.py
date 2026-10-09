import pytest
from aiohttp import web

from m4o_dashboard.api import MihomoClient
from m4o_dashboard.config import Settings
from m4o_dashboard.demo import DemoServer


@pytest.fixture
async def demo():
    async with DemoServer() as server:
        yield server


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
