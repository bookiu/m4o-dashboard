import asyncio

import pytest
from conftest import eventually
from textual.widgets import Checkbox, DataTable, Input, Select, Static

from m4o_dashboard.app import Dashboard
from m4o_dashboard.config import Settings
from m4o_dashboard.connections import Connections
from m4o_dashboard.kernel_config import KernelConfig
from m4o_dashboard.kernel_file import KernelFile, KernelFileError
from m4o_dashboard.logs import Logs
from m4o_dashboard.models import DashboardState
from m4o_dashboard.ui import Information, selected_key


async def test_rule_filters_keep_original_order_and_provider_updates(dashboard, demo):
    app, pilot = dashboard
    await pilot.press("3")
    app.query_one("#rule-type", Select).value = "RULE-SET"
    app.query_one("#rule-policy", Select).value = "REJECT"
    await pilot.pause()
    table = app.query_one("#rules-table", DataTable)
    assert table.row_count == 1
    assert table.get_row_at(0)[0].plain == "002"
    assert "RULE-SET,ads,REJECT" in app.query_one("#raw-rule", Static).render().plain
    table.focus()
    await pilot.press("enter")
    providers = app.query_one("#providers-table", DataTable)
    assert providers.has_focus
    assert selected_key(providers) == "ads"
    await pilot.press("r")
    await eventually(lambda: ("PUT", "/providers/rules/ads", None) in demo.calls)
    await eventually(lambda: not app._mutation_busy)
    await pilot.press("escape")
    assert table.row_count == len(demo.rules)


async def test_group_probe_keeps_fixed_selection_and_encoded_names(client, demo):
    demo.proxies["自动选择"]["fixed"] = demo.nodes[1]
    result = await client.test_group("自动选择")
    assert set(result) == set(demo.nodes[:3])
    assert all(value > 0 for value in result.values())
    assert demo.proxies["自动选择"]["fixed"] == demo.nodes[1]
    assert not any(path.startswith("/group/") for _, path, _ in demo.calls)
    name = "规则 / [red]"
    demo.rule_providers[name] = {"name": name}
    await client.update_rule_provider(name)
    assert "%2F" in demo.calls[-1][1]
    assert name in await client.rule_providers()


async def test_proxy_candidate_is_not_applied_until_enter_and_escape_cancels(dashboard, demo):
    app, pilot = dashboard
    await pilot.press("2", "enter", "down")
    assert "尚未应用" in app.query_one("#switch-preview", Static).render().plain
    assert demo.proxies[demo.group]["now"] == demo.nodes[0]
    await pilot.press("escape")
    assert selected_key(app.query_one("#nodes", DataTable)) == demo.nodes[0]
    assert not any(method == "PUT" for method, _, _ in demo.calls)


async def test_connection_filters_pause_history_and_empty_details(dashboard, demo):
    app, pilot = dashboard
    await pilot.press("4")
    app.query_one("#connection-network", Select).value = "udp"
    await pilot.pause()
    table = app.query_one("#connections-table", DataTable)
    assert 0 < table.row_count < len(app.state.connections)
    assert all(table.get_row_at(i)[2].plain == "UDP" for i in range(table.row_count))
    table.focus()
    await pilot.press("space")
    panel = app.query_one(Connections)
    assert panel.paused
    key = selected_key(table)
    demo.connections.clear()
    await eventually(lambda: not app.state.connections)
    panel.render_state()
    assert selected_key(table) == key
    assert panel.histories[key]
    await pilot.press("space")
    assert table.row_count == 0
    assert "暂无" in app.query_one("#connection-details", Static).render().plain


def test_connection_histories_are_bounded_and_removed_with_closed_connections():
    state = DashboardState()
    for tick in range(400):
        state.update_connections({"connections": [{"id": "one", "download": tick * 100}]}, now=tick)
    assert len(state.connection_traffic["one"]) == 180
    assert state.connection_traffic["one"][-1].down == 100
    state.update_connections({"connections": []}, now=400)
    assert state.connection_traffic == {}


async def test_logs_selection_pause_copy_raw_and_clear(dashboard, monkeypatch):
    app, pilot = dashboard
    await pilot.press("5")
    table = app.query_one("#log-output", DataTable)
    await eventually(lambda: table.row_count > 1)
    await pilot.press("up")
    logs = app.query_one(Logs)
    assert logs.paused
    key = selected_key(table)
    original = logs.displayed[key].message
    app.state.add_log({"type": "warning", "payload": "a newer message"})
    logs.render_state()
    assert selected_key(table) == key
    assert "新增" in app.query_one("#log-paused-status", Static).render().plain
    copied = []
    monkeypatch.setattr(app, "copy_to_clipboard", copied.append)
    await pilot.press("y", "enter")
    assert copied == [original]
    assert isinstance(app.screen, Information)
    await pilot.press("escape", "ctrl+l")
    assert table.row_count == 0
    assert "暂无日志" in app.query_one("#log-details", Static).render().plain
    await pilot.press("end")
    assert not logs.paused


def test_yaml_save_preserves_comments_unrelated_fields_and_permissions(tmp_path):
    path = tmp_path / "kernel.yaml"
    original = '# keep me\nsecret: "private"\nmode: rule\nport: 0 # disabled\n'
    path.write_text(original)
    path.chmod(0o600)
    document = KernelFile(str(path))
    document.load()
    document.save({"port": 8080})
    assert "# keep me" in path.read_text()
    assert 'secret: "private"' in path.read_text()
    assert "port: 8080 # disabled" in path.read_text()
    assert path.stat().st_mode & 0o777 == 0o600
    backups = list(tmp_path.glob("*.bak"))
    assert len(backups) == 1 and backups[0].read_text() == original
    path.write_text("mode: direct\n")
    with pytest.raises(KernelFileError, match="外部修改"):
        document.save({"mode": "global"})
    assert path.read_text() == "mode: direct\n"


def test_yaml_parse_error_never_echoes_credentials(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("secret: [DO-NOT-LEAK\n")
    with pytest.raises(KernelFileError) as error:
        KernelFile(str(path)).load()
    assert "DO-NOT-LEAK" not in str(error.value)


async def test_config_apply_save_independent_and_only_user_changes_persist(demo, tmp_path):
    path = tmp_path / "daily.yaml"
    path.write_text("mixed-port: 7890\nipv6: true\nsecret: keep\n")
    app = Dashboard(Settings(url=demo.url, secret=demo.secret, kernel_config=str(path)))
    async with app.run_test(size=(160, 49)) as pilot:
        await eventually(lambda: bool(app.state.configs))
        await pilot.press("6")
        port = app.query_one("#config-mixed-port", Input)
        port.value = "7891"
        await pilot.pause()
        await pilot.press("ctrl+s")
        await eventually(lambda: "7891" in path.read_text())
        await eventually(lambda: not app._mutation_busy)
        assert demo.config_values["mixed-port"] == 7890
        assert app.query_one(KernelConfig).dirty
        await pilot.press("ctrl+enter")
        await eventually(lambda: demo.config_values["mixed-port"] == 7891)
        await eventually(lambda: not app._mutation_busy)
        assert not app.query_one(KernelConfig).dirty
        # A poll changes IPv6, but it must never be included in the user's next save.
        demo.config_values["ipv6"] = False
        await app.refresh_snapshot()
        app.flush_ui()
        port.value = "7892"
        await pilot.pause()
        await pilot.press("ctrl+enter")
        await eventually(lambda: demo.config_values["mixed-port"] == 7892)
        await eventually(lambda: not app._mutation_busy)
        assert "7891" in path.read_text()
        await pilot.press("ctrl+s")
        await eventually(lambda: "7892" in path.read_text())
        assert "ipv6: true" in path.read_text()
        assert "secret: keep" in path.read_text()
        await pilot.press("ctrl+c")


async def test_config_preview_includes_booleans_and_port_validation(dashboard, demo):
    app, pilot = dashboard
    await pilot.press("6")
    app.query_one("#config-allow-lan", Checkbox).value = True
    app.query_one("#config-ipv6", Checkbox).value = False
    app.query_one("#config-log-level", Select).value = "debug"
    await pilot.pause()
    preview = app.query_one("#config-preview", Static).render().plain
    assert "allow-lan" in preview and "false  →  true" in preview
    assert "ipv6" in preview and "log-level" in preview
    app.query_one("#config-port", Input).value = "65536"
    before = list(demo.calls)
    await pilot.press("ctrl+enter")
    await asyncio.sleep(0.1)
    assert demo.calls == before
    await pilot.press("escape")
    assert not app.query_one(KernelConfig).dirty


@pytest.mark.parametrize("page", ["proxies", "rules", "connections", "logs", "config"])
async def test_reference_layout_panels_fit_wide_terminal(dashboard, page):
    app, pilot = dashboard
    await pilot.resize_terminal(160, 49)
    app.action_page(page)
    await pilot.pause()
    footer = app.query_one("#dashboard-footer").region
    for panel in app.query(f"#{page}-view .page-panel"):
        if not panel.display:
            continue
        assert panel.region.right < 160
        assert panel.region.bottom <= footer.y
    toolbar = app.query(f"#{page}-view .compact-toolbar")
    for container in toolbar:
        for widget in container.query("Input, Select"):
            assert widget.region.y >= container.region.y
            assert widget.region.bottom <= container.region.bottom
