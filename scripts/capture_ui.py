"""Capture every dashboard page using the local demo, never a real controller."""

import argparse
import asyncio
from pathlib import Path

from textual.widgets import Checkbox, DataTable, Select, TabPane

from m4o_dashboard.app import Dashboard
from m4o_dashboard.config import Settings
from m4o_dashboard.logs import Logs


async def capture(output: Path, width: int, height: int, warmup: float = 1) -> None:
    output.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240
    app = Dashboard(Settings(), demo=True)
    async with app.run_test(size=(width, height), notifications=True) as pilot:
        async with asyncio.timeout(10):
            while not all(status[0] for status in app.statuses.values()):  # noqa: ASYNC110
                await asyncio.sleep(0.05)
        await pilot.pause(warmup)
        for pane in app.query(TabPane):
            app.action_page(pane.id)
            await pilot.pause()
            if pane.id == "proxies":
                app.query_one("#nodes", DataTable).move_cursor(row=1, animate=False)
            elif pane.id == "rules":
                app.query_one("#rules-table", DataTable).move_cursor(row=1, animate=False)
            elif pane.id == "logs":
                app.query_one(Logs).set_paused(True)
                table = app.query_one("#log-output", DataTable)
                if table.row_count > 3:
                    table.move_cursor(row=table.row_count - 3, animate=False)
            elif pane.id == "config":
                app.query_one("#config-allow-lan", Checkbox).value = True
                app.query_one("#config-ipv6", Checkbox).value = False
                app.query_one("#config-log-level", Select).value = "debug"
                app.query_one("#config-log-level", Select).focus()
            await pilot.pause()
            path = app.save_screenshot(f"{pane.id}-{width}x{height}.svg", path=str(output))
            print(path)
        await pilot.press("ctrl+c")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("screenshots"))
    parser.add_argument("--width", type=int, default=160)
    parser.add_argument("--height", type=int, default=49)
    parser.add_argument("--warmup", type=float, default=1, help="Seconds of real demo history")
    args = parser.parse_args()
    asyncio.run(capture(args.output, args.width, args.height, args.warmup))


if __name__ == "__main__":
    main()
