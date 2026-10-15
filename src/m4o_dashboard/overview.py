"""Single-screen dashboard composed from live state and command-only controls."""

from __future__ import annotations

from collections import Counter
from itertools import islice

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, DataTable, Label, Select, Static

from .charts import TrafficChart
from .models import DashboardState, size
from .theme import CYAN, FOREGROUND, GREEN, MUTED, PURPLE
from .ui import Command, VimTable, add_columns, display_text, selected_key, sync_table


class RouteControl(Vertical):
    def __init__(self, state: DashboardState, **kwargs) -> None:
        self.state = state
        self._groups: tuple[str, ...] = ()
        self._active_group = ""
        self._mode_seen = ""
        super().__init__(**kwargs)

    def compose(self) -> ComposeResult:
        yield Label("路由控制", classes="panel-heading")
        yield Static("运行模式", classes="route-label")
        with Horizontal(id="mode-options"):
            for mode in ("rule", "global", "direct"):
                yield Button(f"( ) {mode}", id=f"mode-{mode}", classes="mode-button")
        yield Select(
            [("等待控制器…", "")], value="", allow_blank=False, compact=True, id="quick-group"
        )
        yield VimTable(id="quick-nodes", cursor_type="row", show_header=False)
        yield Static("Enter 选择   t 测延迟   m 切换模式", classes="panel-hint")

    def on_mount(self) -> None:
        add_columns(self.query_one(DataTable), [("节点", 24), ("延迟", 9), ("状态", 4)])

    def render_state(self) -> None:
        mode = str(self.state.configs.get("mode", ""))
        if mode != self._mode_seen:
            for name in ("rule", "global", "direct"):
                button = self.query_one(f"#mode-{name}", Button)
                button.label = f"({'●' if name == mode else ' '}) {name}"
                button.set_class(name == mode, "selected-mode")
            self._mode_seen = mode
        selector = self.query_one(Select)
        names = tuple(self.state.groups)
        if names != self._groups:
            current = selector.value
            self._groups = names
            selector.set_options(
                [(Text(display_text(name)), name) for name in names] or [("暂无策略组", "")]
            )
            selector.value = current if current in names else next(iter(names), "")
        selector.disabled = not names
        group_name = str(selector.value)
        group = self.state.groups.get(group_name, {})
        current = str(group.get("now", ""))
        nodes = list(dict.fromkeys(name for name in group.get("all", []) if isinstance(name, str)))
        table = self.query_one(DataTable)
        sync_table(
            table,
            (
                (name, (name, self.state.delay_label(name), "当前" if name == current else ""))
                for name in nodes
            ),
        )
        if group_name != self._active_group:
            table.move_cursor(row=nodes.index(current) if current in nodes else 0, animate=False)
            self._active_group = group_name

    @on(Select.Changed, "#quick-group")
    def group_changed(self) -> None:
        if self.is_mounted:
            self.render_state()

    @on(Button.Pressed, ".mode-button")
    def mode_pressed(self, event: Button.Pressed) -> None:
        self.post_message(Command("mode", event.button.id.removeprefix("mode-")))

    @on(DataTable.RowSelected, "#quick-nodes")
    def use_node(self) -> None:
        group = str(self.query_one(Select).value)
        policy = self.state.groups.get(group, {})
        if str(policy.get("type", "")).lower() not in {"selector", "urltest", "fallback"}:
            self.notify("该策略组不支持手动选择", severity="warning", markup=False)
            return
        node = selected_key(self.query_one(DataTable))
        if node:
            self.post_message(Command("select", group, node))

    def test_node(self) -> None:
        node = selected_key(self.query_one(DataTable))
        if node:
            self.post_message(Command("delay", node))


class Overview(VerticalScroll, can_focus=True):
    def __init__(self, state: DashboardState, **kwargs) -> None:
        self.state = state
        self.logs_paused = False
        super().__init__(**kwargs)

    def compose(self) -> ComposeResult:
        with Horizontal(id="metrics"):
            for title, identifier in (
                ("下载速率", "download-rate"),
                ("上传速率", "upload-rate"),
                ("活动连接", "active-connections"),
                ("内核累计流量", "totals"),
            ):
                with Vertical(classes="metric"):
                    yield Label(title, classes="metric-label")
                    yield Static("—", id=identifier, classes="metric-value")
        with Vertical(id="overview-grid"):
            with Vertical(id="traffic-panel", classes="dashboard-panel"):
                with Horizontal(classes="panel-heading"):
                    yield Label("实时流量 · 最近 60 秒", classes="panel-title")
                    yield Static(
                        Text.assemble(("↓ 下载   ", CYAN), ("↑ 上传", PURPLE)), id="traffic-legend"
                    )
                yield TrafficChart(self.state, id="traffic-chart")
            yield RouteControl(self.state, id="routing-panel", classes="dashboard-panel")
            with Vertical(id="connections-panel", classes="dashboard-panel"):
                with Horizontal(classes="panel-heading"):
                    yield Label("活动连接", classes="panel-title")
                    yield Button(
                        Text("[/ 搜索]"), id="overview-search-connections", classes="panel-action"
                    )
                    yield Button(Text("[d 关闭连接]"), id="overview-close", classes="panel-action")
                yield VimTable(id="overview-connections", cursor_type="row", zebra_stripes=True)
                yield Static("等待连接…", id="overview-connection-count", classes="panel-hint")
            with Vertical(id="rules-panel", classes="dashboard-panel"):
                with Horizontal(classes="panel-heading"):
                    yield Label("路由规则", id="overview-rule-count", classes="panel-title")
                    yield Button(
                        Text("[/ 搜索]"), id="overview-search-rules", classes="panel-action"
                    )
                yield VimTable(id="overview-rules", cursor_type="row", zebra_stripes=True)
                yield Static(
                    "按顺序匹配   r 刷新规则", id="overview-rule-status", classes="panel-hint"
                )
            with Vertical(id="logs-panel", classes="dashboard-panel"):
                with Horizontal(classes="panel-heading"):
                    yield Label("实时日志 · info", classes="panel-title")
                    yield Button(Text("[Space 暂停]"), id="overview-pause", classes="panel-action")
                yield Static("等待日志…", id="overview-logs")
            with Vertical(id="config-panel", classes="dashboard-panel"):
                with Horizontal(classes="panel-heading"):
                    yield Label("全局配置", classes="panel-title")
                    yield Button(
                        Text("[6 编辑]"), id="overview-edit-config", classes="panel-action"
                    )
                yield Static("等待配置…", id="overview-config")
                yield Static("只读预览 · 进入配置页应用 / 还原修改", classes="panel-hint")

    def on_mount(self) -> None:
        add_columns(
            self.query_one("#overview-connections", DataTable),
            [("进程", 10), ("目标地址", 25), ("命中规则", 18), ("出站", 13), ("下载速率", 12)],
        )
        add_columns(
            self.query_one("#overview-rules", DataTable),
            [("#", 3), ("类型", 14), ("匹配内容", 18), ("策略", 14)],
        )

    def render_state(self) -> None:
        state = self.state
        self.query_one("#download-rate", Static).update(
            Text(f"↓ {size(state.down, rate=True)}", style=f"bold {CYAN}")
        )
        self.query_one("#upload-rate", Static).update(
            Text(f"↑ {size(state.up, rate=True)}", style=f"bold {PURPLE}")
        )
        protocols = Counter(entry.network.lower() for entry in state.connections.values())
        count = Text(f"{len(state.connections)}", style=f"bold {GREEN}")
        count.append(f"   TCP {protocols['tcp']} · UDP {protocols['udp']}", style=FOREGROUND)
        self.query_one("#active-connections", Static).update(count)
        totals = Text()
        totals.append(
            f"↓ {size(state.download_total) if state.download_total is not None else '—'}",
            style=f"bold {CYAN}",
        )
        totals.append(
            f"  ↑ {size(state.upload_total) if state.upload_total is not None else '—'}",
            style=f"bold {PURPLE}",
        )
        self.query_one("#totals", Static).update(totals)
        self.query_one(TrafficChart).refresh()
        self.query_one(RouteControl).render_state()
        entries = sorted(
            state.connections.values(), key=lambda entry: entry.down_rate, reverse=True
        )[:5]
        sync_table(
            self.query_one("#overview-connections", DataTable),
            (
                (
                    entry.id,
                    (
                        entry.process,
                        entry.host,
                        str(entry.raw.get("rule", "—")),
                        str((entry.raw.get("chains") or ["—"])[0]),
                        size(entry.down_rate, rate=True),
                    ),
                )
                for entry in entries
            ),
        )
        self.query_one("#overview-connection-count", Static).update(
            f"显示 {len(entries)} / {len(state.connections)}   Enter 详情   ↑↓ 选择"
        )
        rules = state.rules or []
        sync_table(
            self.query_one("#overview-rules", DataTable),
            (
                (str(rule.index), (str(rule.index), rule.kind, rule.payload, rule.proxy))
                for rule in rules[:5]
            ),
        )
        rule_count = f"{len(rules):,}" if state.rules is not None else "…"
        self.query_one("#overview-rule-count", Label).update(f"路由规则 · {rule_count}")
        self.query_one("#overview-rule-status", Static).update(
            Text(
                display_text(
                    "读取失败 · " + state.rules_error
                    if state.rules_error
                    else "按顺序匹配   r 刷新规则"
                )
            )
        )
        if not self.logs_paused:
            visible = list(
                islice((entry for entry in reversed(state.logs) if entry.matches("", "info")), 5)
            )
            lines = Text(no_wrap=True, overflow="ellipsis")
            for index, entry in enumerate(reversed(visible)):
                if index:
                    lines.append("\n")
                lines.append(f"{entry.time}  ", style=MUTED)
                level = "WARN" if entry.level == "warning" else entry.level.upper()
                style = {"error": "#ff777e", "warning": "#f4cf60"}.get(entry.level, GREEN)
                lines.append(f"{level:5}  ", style=style)
                lines.append(display_text(entry.message), style=FOREGROUND)
            self.query_one("#overview-logs", Static).update(lines if visible else "等待日志…")
        config = Text(no_wrap=True, overflow="ellipsis")
        for index, name in enumerate(
            ("mixed-port", "allow-lan", "ipv6", "log-level", "find-process-mode")
        ):
            if index:
                config.append("\n")
            value = state.configs.get(name, "—")
            value = "x" if value is True else " " if value is False else str(value)
            config.append(f"{name:20} ", style=FOREGROUND)
            config.append(f"[ {display_text(value)} ]", style=CYAN)
        self.query_one("#overview-config", Static).update(config)

    @on(Button.Pressed, "#overview-search-connections")
    def search_connections(self) -> None:
        self.post_message(Command("search", "connections"))

    @on(Button.Pressed, "#overview-search-rules")
    def search_rules(self) -> None:
        self.post_message(Command("search", "rules"))

    @on(Button.Pressed, "#overview-edit-config")
    def edit_config(self) -> None:
        self.post_message(Command("page", "config"))

    @on(DataTable.RowSelected, "#overview-connections")
    def connection_detail(self) -> None:
        key = selected_key(self.query_one("#overview-connections", DataTable))
        if key:
            self.post_message(Command("detail", key))

    @on(Button.Pressed, "#overview-close")
    def close_selected(self) -> None:
        key = selected_key(self.query_one("#overview-connections", DataTable))
        if key:
            self.post_message(Command("close", key))

    @on(DataTable.RowSelected, "#overview-rules")
    def open_rules(self) -> None:
        self.post_message(Command("page", "rules"))

    @on(Button.Pressed, "#overview-pause")
    def toggle_logs(self) -> None:
        self.logs_paused = not self.logs_paused
        self.query_one("#overview-pause", Button).label = Text(
            "[Space 继续]" if self.logs_paused else "[Space 暂停]"
        )
        self.render_state()
