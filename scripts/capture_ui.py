"""Capture every dashboard page using the local demo, never a real controller."""

import argparse
import asyncio
from pathlib import Path

from textual.widgets import TabPane

from m4o_dashboard.app import Dashboard
from m4o_dashboard.config import Settings


async def capture(output: Path, width: int, height: int) -> None:
    output.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240
    app = Dashboard(Settings(), demo=True)
    async with app.run_test(size=(width, height), notifications=True) as pilot:
        async with asyncio.timeout(10):
            while not all(status[0] for status in app.statuses.values()):  # noqa: ASYNC110
                await asyncio.sleep(0.05)
        await pilot.pause(1)
        for pane in app.query(TabPane):
            app.action_page(pane.id)
            await pilot.pause()
            path = app.save_screenshot(f"{pane.id}-{width}x{height}.svg", path=str(output))
            print(path)
        await pilot.press("ctrl+c")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("screenshots"))
    parser.add_argument("--width", type=int, default=160)
    parser.add_argument("--height", type=int, default=49)
    args = parser.parse_args()
    asyncio.run(capture(args.output, args.width, args.height))


if __name__ == "__main__":
    main()
