import time

import pytest
from conftest import eventually
from textual.app import App
from textual.widgets import Button, Checkbox, DataTable, Input, Select, Static

from m4o_dashboard.api import APIError
from m4o_dashboard.app import Dashboard
from m4o_dashboard.config import Settings
from m4o_dashboard.models import DashboardState
from m4o_dashboard.overview import Overview, TrafficChart
from m4o_dashboard.panels import KernelConfig, Rules
from m4o_dashboard.ui import Confirm, Information, add_columns, selected_key, sync_table


def test_chart_uses_last_sixty_seconds_not_the_sample_count():
    state = DashboardState()
    now = time.monotonic()
    state.update_traffic({"up": 0, "down": 1024**3}, now=now - 61)
    state.update_traffic({"up": 512, "down": 2048}, now=now - 30)
    state.update_traffic({"up": 0, "down": 1024**3}, now=now + 10)
    chart = TrafficChart(state).render().plain
    assert "2.0 KiB/s" in chart
    assert "GiB" not in chart
    assert "-60s" in chart


async def test_table_row_identity_is_independent_of_duplicate_visible_labels():
    app = App()
    async with app.run_test():
        table = DataTable(cursor_type="row")
        await app.mount(table)
        add_columns(table, [("进程", 12), ("目标", 25)])
        rows = [("id-one", ("firefox", "one.example")), ("id-two", ("firefox", "two.example"))]
        sync_table(table, rows)
        table.move_cursor(row=1)
        sync_table(table, list(reversed(rows)))
        assert selected_key(table) == "id-two"
        assert table.get_row_at(0)[1].plain == "two.example"
        assert table.cursor_row == 0


async def test_rule_search_details_and_endpoint_failure_are_isolated(dashboard, demo, monkeypatch):
    app, pilot = dashboard
    await eventually(lambda: app.state.rules is not None)
    await pilot.press("3", "/")
    await pilot.press("g", "i", "t", "h", "u", "b")
    table = app.query_one("#rules-table", DataTable)
    assert table.row_count == 1
    assert table.get_row_at(0)[2].plain == "github.com"
    await pilot.press("enter", "enter")
    assert isinstance(app.screen, Information)
    await pilot.press("escape")
    previous = list(app.state.rules)

    async def unsupported_rules():
        raise APIError("HTTP 404: rules unavailable", status=404)

    monkeypatch.setattr(app.client, "rules", unsupported_rules)
    demo.mode = "direct"
    await app.refresh_snapshot()
    app.query_one(Rules).render_state()
    assert app.state.configs["mode"] == "direct"
    assert app.state.rules == previous
    assert "404" in app.state.rules_error
    assert app.statuses["http"][0]
    assert "保留最后数据" in app.query_one("#rules-status", Static).render().plain


async def test_config_draft_survives_polling_and_only_changed_fields_are_applied(dashboard, demo):
    app, pilot = dashboard
    await pilot.press("6")
    port = app.query_one("#config-mixed-port", Input)
    port.value = "7891"
    demo.config_values["ipv6"] = False
    await app.refresh_snapshot()
    app.flush_ui()
    assert port.value == "7891"
    assert app.query_one("#config-ipv6", Checkbox).value  # Unsaved form stays intact.
    before = len([call for call in demo.calls if call[0] == "PATCH"])
    await pilot.press("ctrl+s")
    await eventually(lambda: demo.config_values["mixed-port"] == 7891)
    await eventually(lambda: not app._mutation_busy)
    assert len([call for call in demo.calls if call[0] == "PATCH"]) == before + 1
    assert demo.config_values["ipv6"] is False  # The unchanged field was not sent.
    assert app.query_one("#config-ipv6", Checkbox).value is False
    assert not app.query_one(KernelConfig).dirty


async def test_invalid_config_and_typing_never_write_or_trigger_navigation(dashboard, demo):
    app, pilot = dashboard
    await pilot.press("6")
    port = app.query_one("#config-mixed-port", Input)
    port.value = ""
    await pilot.press("1", "r", "q")
    assert app.current_page() == "config"
    assert port.value == "1rq"
    before = [call for call in demo.calls if call[0] == "PATCH"]
    await pilot.press("ctrl+s")
    port.value = "70000"
    await pilot.press("ctrl+s")
    await eventually(lambda: not app._mutation_busy)
    assert [call for call in demo.calls if call[0] == "PATCH"] == before
    assert port.value == "70000"
    await pilot.press("escape")
    assert port.value == "7890"
    assert not app.query_one(KernelConfig).dirty
    await pilot.press("4")
    assert app.current_page() == "connections"


async def test_config_before_connection_or_with_unsupported_fields_stays_usable(demo):
    app = Dashboard(Settings(url=demo.url, secret="wrong"))
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.press("6")
        assert app.query_one("#apply-config", Button).disabled
        assert app.query_one("#config-log-level", Select).value is Select.NULL
        app.state.configs = {"mixed-port": 7890}
        app.query_one(KernelConfig).render_state()
        assert app.query_one("#config-log-level", Select).disabled
        assert app.query_one("#config-ipv6", Checkbox).disabled
        await pilot.press("escape", "q")


async def test_overview_node_selection_keeps_raw_emoji_group_and_node_names(dashboard, demo):
    app, pilot = dashboard
    group = "✈\ufe0f组"
    node = "☁\ufe0f节点 [red]"
    demo.proxies[node] = {**demo.proxies[demo.nodes[0]], "name": node}
    demo.proxies[group] = {"type": "Selector", "all": [demo.nodes[0], node], "now": demo.nodes[0]}
    demo.proxies["✈\ufe0e组"] = {"type": "Selector", "all": [demo.nodes[0]], "now": demo.nodes[0]}
    await app.refresh_snapshot()
    app.flush_ui()
    app.query_one("#quick-group", Select).value = group
    await pilot.pause()
    table = app.query_one("#quick-nodes", DataTable)
    table.focus()
    table.move_cursor(row=1)
    await pilot.press("enter")
    await eventually(lambda: demo.proxies[group]["now"] == node)
    await eventually(lambda: not app._mutation_busy)
    assert demo.proxies["✈\ufe0e组"]["now"] == demo.nodes[0]
    assert selected_key(table) == node


async def test_overview_close_confirmation_and_log_pause(dashboard, demo):
    app, pilot = dashboard
    table = app.query_one("#overview-connections", DataTable)
    table.focus()
    key = selected_key(table)
    await pilot.press("d")
    assert isinstance(app.screen, Confirm)
    assert app.screen.query_one("#cancel", Button).has_focus
    await pilot.press("1", "6", "m", "t", "space", "d", "ctrl+s")
    assert len(app.screen_stack) == 2
    assert app.current_page() == "overview"
    assert key in demo.connections
    await pilot.press("escape")
    panel = app.query_one(Overview)
    await pilot.press("space")
    before = app.query_one("#overview-logs", Static).render().plain
    app.state.add_log({"payload": "new paused preview event"})
    panel.render_state()
    assert panel.logs_paused
    assert app.query_one("#overview-logs", Static).render().plain == before
    await pilot.press("space")
    assert "new paused preview event" in app.query_one("#overview-logs", Static).render().plain


async def test_wide_dashboard_geometry_and_config_preview(dashboard):
    app, pilot = dashboard
    await pilot.resize_terminal(160, 49)
    await pilot.pause()
    footer = app.query_one("#dashboard-footer").region
    metrics = list(app.query(".metric"))
    assert len(metrics) == 4
    assert len({metric.region.y for metric in metrics}) == 1
    panels = list(app.query(".dashboard-panel"))
    assert len(panels) == 6
    for panel in panels:
        assert panel.region.bottom <= footer.y
        assert panel.region.x > 0
        assert panel.region.right < 160
    for left, right in zip(panels[::2], panels[1::2], strict=True):
        assert left.region.y == right.region.y
        assert left.region.right < right.region.x
        assert left.region.width > right.region.width
    config = app.query_one("#overview-config", Static)
    assert config.region.height >= 5
    assert "find-process-mode" in config.render().plain
    selector = app.query_one("#quick-group SelectCurrent")
    assert selector.content_region.height == 1


@pytest.mark.parametrize("terminal_size", [(80, 24), (120, 36)])
async def test_overview_panels_are_reachable_by_scrolling(terminal_size):
    app = Dashboard(Settings(), demo=True)
    async with app.run_test(size=terminal_size) as pilot:
        await eventually(lambda: bool(app.state.configs))
        await pilot.pause()
        for identifier in (
            "#traffic-panel",
            "#routing-panel",
            "#connections-panel",
            "#rules-panel",
            "#logs-panel",
            "#config-panel",
        ):
            panel = app.query_one(identifier)
            panel.scroll_visible(animate=False, top=True)
            await pilot.pause()
            assert panel.region.y >= app.query_one("#overview-view").region.y
            assert panel.region.y < app.query_one("#dashboard-footer").region.y
        await pilot.press("ctrl+c")
