import asyncio

import pytest
from textual.widgets import Button, DataTable, Input, RichLog, Select, Static, TabbedContent

from m4o_dashboard.app import Dashboard
from m4o_dashboard.config import Settings
from m4o_dashboard.models import DashboardState
from m4o_dashboard.ui import Confirm, Information, Logs, Proxies, selected_key, sync_table


async def eventually(predicate):
    async with asyncio.timeout(4):
        # Textual and the remote fixture expose state, not a shared asyncio.Event.
        while not predicate():  # noqa: ASYNC110
            await asyncio.sleep(0.03)


@pytest.fixture
async def dashboard(demo):
    app = Dashboard(Settings(url=demo.url, secret=demo.secret, refresh_interval=1))
    async with app.run_test(size=(120, 36)) as pilot:
        await eventually(lambda: all(value[0] for value in app.statuses.values()))
        await pilot.pause()
        yield app, pilot


async def test_initial_state_page_keys_search_and_modals(dashboard):
    app, pilot = dashboard
    assert app.state.version == "demo-1.0"
    assert len(app.state.connections) == 20
    assert app.query_one("#overview-view").has_focus
    assert 0 < app.query_one("#download-rate").region.y < 20
    await pilot.press("2")
    assert app.query_one("#pages", TabbedContent).active == "proxies"
    assert app.query_one("#groups", DataTable).row_count == 3
    await pilot.press("/")
    assert app.query_one("#proxy-search", Input).has_focus
    await pilot.press("r", "q", "1", "2", "3")
    assert app.query_one("#proxy-search", Input).value == "rq123"
    assert app.current_page() == "proxies"  # Typing is not global navigation.
    app.query_one("#proxy-search", Input).value = "Tokyo"
    await pilot.pause()
    assert app.query_one("#nodes", DataTable).row_count == 1
    await pilot.press("escape", "?")
    assert isinstance(app.screen, Information)
    await pilot.press("/", "2", "t", "delete", "space", "?")
    assert len(app.screen_stack) == 2  # No shortcuts leak into the obscured page.
    await pilot.press("escape")
    assert len(app.screen_stack) == 1


async def test_keyboard_proxy_switch_delay_and_automatic_group(dashboard, demo):
    app, pilot = dashboard
    await pilot.press("2", "enter", "down", "enter")
    await eventually(lambda: demo.proxies[demo.group]["now"] == demo.nodes[1])
    await eventually(lambda: not app._mutation_busy)
    assert app.state.proxies[demo.group]["now"] == demo.nodes[1]
    await pilot.press("t")
    await eventually(lambda: demo.nodes[1] in app.state.delays)
    assert app.state.delays[demo.nodes[1]] > 0
    await eventually(lambda: not app._mutation_busy)
    table = app.query_one("#groups", DataTable)
    table.focus()
    table.move_cursor(row=1)
    await pilot.pause()
    assert app.query_one(Proxies).group == "自动选择"
    await pilot.press("enter", "down", "enter")
    await eventually(lambda: demo.proxies["自动选择"]["fixed"] != "")
    await eventually(lambda: not app._mutation_busy)
    app.clear_notifications()
    await pilot.click("#unpin-node")
    await eventually(lambda: demo.proxies["自动选择"]["fixed"] == "")
    await eventually(lambda: not app._mutation_busy)
    table.focus()
    table.move_cursor(row=2)
    await pilot.pause()
    assert app.query_one("#use-node", Button).disabled
    assert app.query_one("#unpin-node", Button).disabled


async def test_mouse_tabs_node_button_and_mode_change(dashboard, demo):
    app, pilot = dashboard
    await pilot.click("#--content-tab-proxies")
    await pilot.pause()
    assert app.current_page() == "proxies"
    # Actual cell click, then button click: no programmatic command invocation.
    await pilot.click("#nodes", offset=(5, 3))
    await pilot.click("#use-node")
    await eventually(lambda: demo.proxies[demo.group]["now"] == demo.nodes[1])
    await eventually(lambda: not app._mutation_busy)
    app.clear_notifications()
    await pilot.press("1")
    app.query_one("#mode", Select).value = "global"
    await pilot.click("#apply-mode")
    await eventually(lambda: demo.mode == "global")
    await eventually(lambda: not app._mutation_busy)
    assert app.state.configs["mode"] == "global"


async def test_connections_filter_sort_detail_and_destructive_confirmation(dashboard, demo):
    app, pilot = dashboard
    await pilot.press("3")
    table = app.query_one("#connections-table", DataTable)
    table.move_cursor(row=2)
    key = selected_key(table)
    app.query_one("#connection-sort", Select).value = "download"
    await pilot.pause()
    assert selected_key(table) == key
    await pilot.press("enter")
    assert isinstance(app.screen, Information)
    await pilot.press("escape", "delete")
    assert isinstance(app.screen, Confirm)
    assert app.screen.query_one("#cancel", Button).has_focus
    await pilot.press("escape")
    assert key in demo.connections
    await pilot.press("delete")
    await pilot.click("#confirm")
    await eventually(lambda: key not in demo.connections)
    await eventually(lambda: not app._mutation_busy)
    assert key not in app.state.connections
    app.clear_notifications()
    app.query_one("#connection-search", Input).value = "firefox"
    await pilot.pause()
    assert 0 < table.row_count < len(demo.connections)
    await pilot.click("#close-all")
    assert isinstance(app.screen, Confirm)
    await pilot.click("#confirm")
    await eventually(lambda: not demo.connections)
    await eventually(lambda: not app._mutation_busy)
    assert not app.state.connections


async def test_logs_filter_pause_resume_and_clear(dashboard):
    app, pilot = dashboard
    await pilot.press("4")
    panel = app.query_one(Logs)
    output = app.query_one("#log-output", RichLog)
    await eventually(lambda: bool(output.lines))
    await pilot.press("space")
    assert panel.paused
    old_seen = panel.last_seen
    app.state.add_log({"type": "error", "payload": "unique failure token"})
    await pilot.pause(0.3)
    assert panel.last_seen == old_seen
    app.query_one("#log-search", Input).value = "unique failure token"
    await pilot.pause()
    await pilot.click("#pause-logs")
    await pilot.pause()
    assert not panel.paused
    assert "unique failure token" in "\n".join(line.text for line in output.lines)
    assert "[TCP]" not in "\n".join(line.text for line in output.lines)
    await pilot.click("#clear-logs")
    await pilot.pause()
    assert "unique failure token" not in "\n".join(line.text for line in output.lines)
    assert all(entry.message != "unique failure token" for entry in app.state.logs)


async def test_incremental_rows_preserve_key_and_scroll(dashboard):
    app, pilot = dashboard
    await pilot.press("3")
    table = app.query_one("#connections-table", DataTable)
    table.move_cursor(row=10)
    key = selected_key(table)
    rows = [(entry.id, (entry.id,) + ("changed",) * 9) for entry in app.state.connections.values()]
    rows.reverse()
    sync_table(table, rows)
    assert selected_key(table) == key
    assert table.get_row(key)[1].plain == "changed"
    sync_table(table, [])
    assert selected_key(table) is None


@pytest.mark.parametrize("terminal_size", [(80, 24), (160, 48)])
async def test_demo_resize_and_clean_shutdown(terminal_size):
    app = Dashboard(Settings(), demo=True)
    client = None
    async with app.run_test(size=terminal_size) as pilot:
        await eventually(lambda: all(value[0] for value in app.statuses.values()))
        client = app.client
        for page in ("1", "2", "3", "4"):
            await pilot.press(page)
            await pilot.pause()
            if page == "1":
                # Focusing the mode selector used to scroll the traffic cards off screen.
                rate = app.query_one("#download-rate")
                assert 4 <= rate.region.y < terminal_size[1] - 1
        for identifier in ("#log-search", "#pause-logs", "#clear-logs"):
            widget = app.query_one(identifier)
            assert widget.region.right <= terminal_size[0]
            assert widget.region.bottom <= terminal_size[1]
        await pilot.resize_terminal(100, 30)
        await pilot.press("2", "enter", "down", "enter")
        await eventually(lambda: not app._mutation_busy)
        await pilot.press("q")
    assert client.session.closed
    assert app.client is None


async def test_auth_failure_stays_interactive_and_redacts_secret(demo):
    app = Dashboard(Settings(url=demo.url, secret="DO-NOT-LEAK"))
    async with app.run_test(size=(80, 24)) as pilot:
        await eventually(lambda: "401" in app.statuses["http"][1])
        await pilot.pause(0.3)
        assert all(not value[0] for value in app.statuses.values())
        status = str(app.query_one("#network-status", Static).render())
        assert "401" in status
        assert "DO-NOT-LEAK" not in status
        await pilot.press("2", "3", "4", "?")
        assert isinstance(app.screen, Information)
        await pilot.press("escape", "q")


async def test_large_log_burst_is_bounded(dashboard):
    app, pilot = dashboard
    await pilot.press("4")
    logs = app.query_one(Logs)
    logs.state = DashboardState(max_logs=100)
    logs.last_seen = 0
    logs.rebuild = False
    for index in range(1000):
        logs.state.add_log({"payload": f"burst {index}"})
    logs.render_state()
    assert len(logs.state.logs) == 100
    assert logs.skipped == 900
