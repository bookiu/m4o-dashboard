"""Textual views. Messages describe intentions; the app owns all I/O."""

from __future__ import annotations

import json
from collections.abc import Iterable

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, Label, RichLog, Select, Sparkline, Static

from .models import DashboardState, clean, size


class Command(Message):
    def __init__(self, action: str, *args: str) -> None:
        self.action = action
        self.args = args
        super().__init__()


class VimTable(DataTable):
    BINDINGS = [
        Binding("j", "cursor_down", show=False),
        Binding("k", "cursor_up", show=False),
        Binding("g", "scroll_top", show=False),
        Binding("G", "scroll_bottom", show=False),
    ]


def selected_key(table: DataTable) -> str | None:
    if table.row_count:
        return str(table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value)
    return None


def sync_table(table: DataTable, rows: Iterable[tuple[str, tuple[str, ...]]]) -> None:
    """Patch keyed rows instead of rebuilding, preserving cursor and scroll.

    Every table's identity column contains its full unique key (the column can
    visually truncate it). Sorting uses that key, not a row's changing position.
    """
    rows = list(rows)
    chosen = selected_key(table)
    wanted = {key for key, _ in rows}
    for key in list(table.rows):
        if key.value not in wanted:
            table.remove_row(key)
    columns = list(table.columns)
    for key, cells in rows:
        rendered = tuple(Text(clean(cell)) for cell in cells)
        if key not in table.rows:
            table.add_row(*rendered, key=key)
        else:
            for column, old, new in zip(columns, table.get_row(key), rendered, strict=True):
                if old != new:
                    table.update_cell(key, column, new)
    if rows:
        rank = {clean(key): index for index, (key, _) in enumerate(rows)}
        table.sort("identity", key=lambda cell: rank.get(cell.plain, len(rank)))
        if chosen in wanted:
            table.move_cursor(row=table.get_row_index(chosen), animate=False, scroll=False)


def add_columns(table: DataTable, columns: list[tuple[str, int | None]]) -> None:
    for index, (label, width) in enumerate(columns):
        table.add_column(label, key="identity" if index == 0 else str(index), width=width)


class Overview(VerticalScroll, can_focus=True):
    def compose(self) -> ComposeResult:
        yield Label("LIVE TRAFFIC  ·  流量总览", classes="section-title")
        with Horizontal(id="rates"):
            with Vertical(classes="metric"):
                yield Label("↓ DOWNLOAD", classes="muted")
                yield Static("—", id="download-rate", classes="metric-value")
                yield Sparkline([0], id="download-chart")
            with Vertical(classes="metric"):
                yield Label("↑ UPLOAD", classes="muted")
                yield Static("—", id="upload-rate", classes="metric-value")
                yield Sparkline([0], id="upload-chart")
        yield Static("Waiting for controller…", id="totals", classes="card")
        yield Label("CONTROLLER  ·  控制器", classes="section-title")
        yield Static("Connecting…", id="controller-info", classes="card")
        with Horizontal(classes="toolbar"):
            yield Label("运行模式", classes="toolbar-label")
            yield Select(
                [("Rule · 规则", "rule"), ("Global · 全局", "global"), ("Direct · 直连", "direct")],
                value="rule",
                allow_blank=False,
                id="mode",
            )
            yield Button("应用模式", id="apply-mode", variant="primary")
        yield Static(
            "[b]1[/b] 总览   [b]2[/b] 代理   [b]3[/b] 连接   [b]4[/b] 日志\n"
            "[b]Tab[/b] 切换焦点 · [b]↑↓ / j k[/b] 导航 · [b]/[/b] 搜索 · [b]r[/b] 刷新\n"
            "[b]?[/b] 帮助 · [b]q / Ctrl+C[/b] 退出\n\n"
            "速率由内核推送；曲线保存最近 180 个样本。总量是内核统计，不是本次界面会话累计。\n"
            "代理切换不迁移已有连接；需要时在连接页显式关闭。",
            classes="card help-card",
        )

    def on_mount(self) -> None:
        self._mode_seen: str | None = None

    def render_state(self, state: DashboardState, endpoint: str) -> None:
        self.query_one("#download-rate", Static).update(size(state.down, rate=True))
        self.query_one("#upload-rate", Static).update(size(state.up, rate=True))
        self.query_one("#download-chart", Sparkline).data = list(state.down_history)
        self.query_one("#upload-chart", Sparkline).data = list(state.up_history)
        downloaded = size(state.download_total) if state.download_total is not None else "—"
        uploaded = size(state.upload_total) if state.upload_total is not None else "—"
        memory = size(state.memory) if state.memory is not None else "—"
        self.query_one("#totals", Static).update(
            Text(
                f"累计下载  {downloaded}     累计上传  {uploaded}\n"
                f"活跃连接  {len(state.connections)}     内存  {memory}"
            )
        )
        mode = str(state.configs.get("mode", "—")).lower()
        self.query_one("#controller-info", Static).update(
            Text(
                clean(
                    f"{endpoint}\nMihomo {state.version}   ·   当前模式 {mode}"
                    f"   ·   代理组 {len(state.groups)}   ·   节点/组 {len(state.proxies)}"
                )
            )
        )
        if mode != self._mode_seen and mode in {"rule", "global", "direct"}:
            self.query_one("#mode", Select).value = mode
            self._mode_seen = mode

    @on(Button.Pressed, "#apply-mode")
    def apply_mode(self) -> None:
        self.post_message(Command("mode", str(self.query_one("#mode", Select).value)))


class Proxies(Vertical):
    def __init__(self, state: DashboardState, **kwargs) -> None:
        self.state = state
        self.group: str | None = None
        super().__init__(**kwargs)

    def compose(self) -> ComposeResult:
        with Horizontal(id="proxy-columns"):
            with Vertical(id="group-pane"):
                yield Label("GROUPS · 策略组", classes="section-title")
                yield VimTable(id="groups", cursor_type="row", zebra_stripes=True)
            with Vertical(id="node-pane"):
                yield Static("选择一个策略组", id="group-info")
                yield Input(placeholder="/ 搜索节点名称、类型", id="proxy-search")
                yield VimTable(id="nodes", cursor_type="row", zebra_stripes=True)
        with Horizontal(classes="toolbar"):
            yield Button("使用节点", id="use-node", variant="primary")
            yield Button("测延迟 (t)", id="test-node")
            yield Button("恢复自动", id="unpin-node")
        yield Static(
            "选择组 → 选择节点 → Enter / 点击已选中行 / 使用节点切换。",
            classes="hint",
        )

    def on_mount(self) -> None:
        add_columns(self.query_one("#groups", DataTable), [("策略组", 22), ("当前", 22)])
        add_columns(
            self.query_one("#nodes", DataTable),
            [
                ("节点", 22),
                ("状态", 6),
                ("延迟", 10),
                ("类型", 12),
            ],
        )

    def render_state(self) -> None:
        groups = self.state.groups
        if self.group not in groups:
            self.group = next(iter(groups), None)
        sync_table(
            self.query_one("#groups", DataTable),
            ((name, (name, str(value.get("now", "自动分配")))) for name, value in groups.items()),
        )
        self.render_nodes()

    def render_nodes(self) -> None:
        group = self.state.groups.get(self.group or "", {})
        kind = str(group.get("type", ""))
        current = str(group.get("now", "—"))
        fixed = str(group.get("fixed") or "")
        self.query_one("#group-info", Static).update(
            Text(
                clean(
                    f"{self.group or '暂无策略组'}  ·  {kind}\n当前: {current}"
                    + (f"  ·  固定: {fixed}" if fixed else "")
                )
            )
        )
        query = self.query_one("#proxy-search", Input).value.casefold()
        rows = []
        for name in dict.fromkeys(n for n in group.get("all", []) if isinstance(n, str)):
            node = self.state.proxies.get(name, {})
            if query not in f"{name} {node.get('type', '')}".casefold():
                continue
            rows.append(
                (
                    name,
                    (
                        name,
                        "● 当前" if name == current else "",
                        self.state.delay_label(name),
                        str(node.get("type", "—")),
                    ),
                )
            )
        sync_table(self.query_one("#nodes", DataTable), rows)
        selectable = kind.lower() in {"selector", "urltest", "fallback"}
        self.query_one("#use-node", Button).disabled = not selectable or not rows
        self.query_one("#test-node", Button).disabled = not rows
        self.query_one("#unpin-node", Button).disabled = kind.lower() not in {"urltest", "fallback"}

    @on(DataTable.RowHighlighted, "#groups")
    def group_highlighted(self, event: DataTable.RowHighlighted) -> None:
        name = str(event.row_key.value)
        if name in self.state.groups and name != self.group:
            self.group = name
            self.render_nodes()

    @on(DataTable.RowSelected, "#groups")
    def group_selected(self) -> None:
        self.query_one("#nodes", DataTable).focus()

    @on(Input.Changed, "#proxy-search")
    def search_changed(self) -> None:
        self.render_nodes()

    @on(Input.Submitted, "#proxy-search")
    def search_submitted(self) -> None:
        self.query_one("#nodes", DataTable).focus()

    @on(DataTable.RowSelected, "#nodes")
    @on(Button.Pressed, "#use-node")
    def use_node(self) -> None:
        node = selected_key(self.query_one("#nodes", DataTable))
        if node and self.group and not self.query_one("#use-node", Button).disabled:
            self.post_message(Command("select", self.group, node))

    @on(Button.Pressed, "#test-node")
    def test_node(self) -> None:
        node = selected_key(self.query_one("#nodes", DataTable))
        if node:
            self.post_message(Command("delay", node))

    @on(Button.Pressed, "#unpin-node")
    def unpin(self) -> None:
        if self.group and not self.query_one("#unpin-node", Button).disabled:
            self.post_message(Command("unpin", self.group))


class Connections(Vertical):
    def __init__(self, state: DashboardState, **kwargs) -> None:
        self.state = state
        super().__init__(**kwargs)

    def compose(self) -> ComposeResult:
        with Horizontal(classes="toolbar"):
            yield Input(
                placeholder="/ 搜索域名、进程、规则、代理链、源地址", id="connection-search"
            )
            yield Select(
                [
                    ("按连接 ID", "id"),
                    ("下载总量 ↓", "download"),
                    ("上传总量 ↓", "upload"),
                    ("下载速度 ↓", "down_rate"),
                    ("上传速度 ↓", "up_rate"),
                ],
                value="id",
                allow_blank=False,
                id="connection-sort",
            )
        yield VimTable(id="connections-table", cursor_type="row", zebra_stripes=True)
        yield Static("等待连接信息…", id="connection-count", classes="hint")
        with Horizontal(classes="toolbar"):
            yield Button("详情 (Enter)", id="connection-detail")
            yield Button("关闭选中 (Del)", id="close-connection", variant="warning")
            yield Button("关闭全部", id="close-all", variant="error")

    def on_mount(self) -> None:
        add_columns(
            self.query_one(DataTable),
            [
                ("ID", 9),
                ("目标", 28),
                ("进程", 16),
                ("协议", 6),
                ("↓ 速度", 12),
                ("↑ 速度", 12),
                ("↓ 总量", 12),
                ("↑ 总量", 12),
                ("代理链", 30),
                ("规则", 24),
            ],
        )

    def render_state(self) -> None:
        query = self.query_one("#connection-search", Input).value
        entries = [entry for entry in self.state.connections.values() if entry.matches(query)]
        sort = str(self.query_one("#connection-sort", Select).value)
        entries.sort(key=lambda entry: getattr(entry, sort, entry.id), reverse=sort != "id")
        sync_table(
            self.query_one(DataTable),
            (
                (
                    entry.id,
                    (
                        entry.id,
                        entry.host,
                        entry.process,
                        entry.network,
                        size(entry.down_rate, rate=True),
                        size(entry.up_rate, rate=True),
                        size(entry.download),
                        size(entry.upload),
                        entry.chains,
                        entry.rule,
                    ),
                )
                for entry in entries
            ),
        )
        self.query_one("#connection-count", Static).update(
            f"显示 {len(entries)} / {len(self.state.connections)} 条连接 · 横向滚动查看更多列"
        )
        self.query_one("#close-all", Button).disabled = not self.state.connections
        for identifier in ("#connection-detail", "#close-connection"):
            self.query_one(identifier, Button).disabled = not entries

    @on(Input.Changed, "#connection-search")
    @on(Select.Changed, "#connection-sort")
    def filters_changed(self) -> None:
        self.render_state()

    @on(Input.Submitted, "#connection-search")
    def search_submitted(self) -> None:
        self.query_one(DataTable).focus()

    @on(DataTable.RowSelected, "#connections-table")
    @on(Button.Pressed, "#connection-detail")
    def detail(self) -> None:
        key = selected_key(self.query_one(DataTable))
        if key:
            self.post_message(Command("detail", key))

    @on(Button.Pressed, "#close-connection")
    def close_selected(self) -> None:
        key = selected_key(self.query_one(DataTable))
        if key:
            self.post_message(Command("close", key))

    @on(Button.Pressed, "#close-all")
    def close_all(self) -> None:
        self.post_message(Command("close-all"))


class Logs(Vertical):
    def __init__(self, state: DashboardState, **kwargs) -> None:
        self.state = state
        self.paused = False
        self.last_seen = 0
        self.rebuild = True
        self.skipped = 0
        super().__init__(**kwargs)

    def compose(self) -> ComposeResult:
        with Horizontal(classes="toolbar"):
            yield Input(placeholder="/ 过滤日志（不区分大小写）", id="log-search")
            yield Select(
                [("DEBUG+", "debug"), ("INFO+", "info"), ("WARN+", "warning"), ("ERROR", "error")],
                value="info",
                allow_blank=False,
                id="log-level",
            )
            yield Button("暂停 (Space)", id="pause-logs")
            yield Button("清空", id="clear-logs")
        yield RichLog(
            id="log-output", max_lines=self.state.max_logs, min_width=1, wrap=True, markup=False
        )
        yield Static("", id="log-count", classes="hint")

    def render_state(self) -> None:
        if self.paused:
            self.update_status()
            return
        output = self.query_one("#log-output", RichLog)
        # Respect manual scrollback. New entries still enter the bounded buffer.
        if not self.rebuild and output.lines and not output.is_vertical_scroll_end:
            self.update_status("已离开底部，暂停绘制；滚到底部或按 End 恢复")
            return
        query = self.query_one("#log-search", Input).value
        level = str(self.query_one("#log-level", Select).value)
        entries = list(self.state.logs)
        delta = self.state.logs_seen - self.last_seen
        if self.rebuild:
            output.clear()
            self.rebuild = False
        elif delta:
            count = min(delta, len(entries), 250)
            self.skipped += max(0, delta - count)
            entries = entries[-count:] if count else []
        else:
            entries = []
        for entry in entries:
            if entry.matches(query, level):
                style = {"error": "red", "warning": "yellow", "debug": "dim"}.get(entry.level, "")
                line = Text(f"{entry.time}  {entry.level.upper():7}  ", style=style)
                line.append(entry.message)
                output.write(line, scroll_end=True, animate=False)
        self.last_seen = self.state.logs_seen
        self.update_status()

    def update_status(self, extra: str = "") -> None:
        status = "已暂停绘制（仍接收）" if self.paused else "实时"
        text = (
            f"{status} · 缓冲 {len(self.state.logs)}/{self.state.max_logs}"
            f" · 收到 {self.state.logs_seen}"
        )
        if self.skipped:
            text += f" · 跳过绘制 {self.skipped} 条（搜索重绘缓冲）"
        self.query_one("#log-count", Static).update(text + (f" · {extra}" if extra else ""))

    @on(Input.Changed, "#log-search")
    @on(Select.Changed, "#log-level")
    def filters_changed(self) -> None:
        self.rebuild = True
        self.render_state()

    @on(Button.Pressed, "#pause-logs")
    def toggle_pause(self) -> None:
        self.paused = not self.paused
        self.query_one("#pause-logs", Button).label = (
            "继续 (Space)" if self.paused else "暂停 (Space)"
        )
        if not self.paused:
            self.rebuild = True
        self.render_state()

    @on(Button.Pressed, "#clear-logs")
    def clear_logs(self) -> None:
        self.state.logs.clear()
        self.last_seen = self.state.logs_seen
        self.skipped = 0
        self.query_one("#log-output", RichLog).clear()
        self.update_status()


class Confirm(ModalScreen[bool]):
    BINDINGS = [Binding("escape", "cancel", "取消")]

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__()

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog confirm-dialog"):
            yield Label("确认操作", classes="section-title")
            yield Static(Text(clean(self.message)))
            with Horizontal(classes="toolbar"):
                yield Button("取消 (Esc)", id="cancel")
                yield Button("确认关闭", id="confirm", variant="error")

    def on_mount(self) -> None:
        self.query_one("#cancel", Button).focus()

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(False)

    @on(Button.Pressed, "#confirm")
    def confirm(self) -> None:
        self.dismiss(True)


class Information(ModalScreen[None]):
    BINDINGS = [Binding("escape,q", "close", "关闭")]

    def __init__(self, title: str, content: str | dict) -> None:
        self.heading = title
        self.content = (
            json.dumps(content, indent=2, ensure_ascii=False)
            if isinstance(content, dict)
            else content
        )
        super().__init__()

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(Text(clean(self.heading)), classes="section-title")
            with VerticalScroll():
                yield Static(Text(clean(self.content)))
            yield Button("关闭 (Esc)", id="dismiss", variant="primary")

    @on(Button.Pressed, "#dismiss")
    def action_close(self) -> None:
        self.dismiss(None)
