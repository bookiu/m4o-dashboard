"""Bounded log table with stable selection, scroll pause and raw detail."""

from collections import Counter

from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Checkbox, DataTable, Input, Label, Select, Static

from .components import fields, literal, stat_card
from .models import DashboardState
from .theme import CYAN, GREEN
from .ui import Information, VimTable, add_columns, selected_key, sync_table


class LogTable(VimTable):
    """Pause before navigation, so arriving logs cannot move the row under the user."""

    BINDINGS = [Binding("end", "resume", show=False)]

    def on_key(self, event: events.Key) -> None:
        if event.key in {"up", "down", "k", "j", "pageup", "pagedown", "home", "g", "G"}:
            self.app.query_one(Logs).pause()

    def on_mouse_scroll_up(self) -> None:
        self.app.query_one(Logs).pause()

    def on_mouse_down(self) -> None:
        self.app.query_one(Logs).pause()

    def action_resume(self) -> None:
        panel = self.app.query_one(Logs)
        panel.set_paused(False)


class Logs(VerticalScroll):
    BINDINGS = [
        Binding("ctrl+l", "clear", show=False),
        Binding("y", "copy", show=False),
        Binding("l", "level", show=False),
    ]

    def __init__(self, state: DashboardState, **kwargs) -> None:
        self.state = state
        self.paused = False
        self.last_seen = 0
        self.rebuild = True
        self.skipped = 0
        self.displayed = {}
        self._rendered_filter = None
        super().__init__(**kwargs)

    def compose(self) -> ComposeResult:
        with Horizontal(classes="stat-strip"):
            for title, identifier in (
                ("缓存日志", "logs-total"),
                ("INFO", "logs-info"),
                ("WARN", "logs-warn"),
                ("ERROR", "logs-error"),
            ):
                yield stat_card(title, identifier)
        with Horizontal(classes="compact-toolbar page-panel", id="log-toolbar"):
            yield Select(
                [
                    ("DEBUG 及以上", "debug"),
                    ("INFO 及以上", "info"),
                    ("WARN 及以上", "warning"),
                    ("ERROR", "error"),
                ],
                value="info",
                allow_blank=False,
                id="log-level",
                compact=True,
            )
            yield Input(placeholder="/ 搜索日志内容", id="log-search")
            yield Checkbox("自动滚动", value=True, id="log-autoscroll")
            yield Button("暂停 (Space)", id="pause-logs", classes="panel-action")
            yield Button("清空", id="clear-logs", classes="panel-action")
        with Horizontal(id="log-layout"):
            with Vertical(id="log-list-panel", classes="page-panel"):
                yield Label("日志流", classes="panel-heading")
                yield LogTable(id="log-output", cursor_type="row")
                yield Static(id="log-count", classes="panel-hint")
            with Vertical(id="log-sidebar"):
                with VerticalScroll(id="log-detail-panel", classes="page-panel"):
                    yield Label("日志详情", classes="panel-heading")
                    yield Static(id="log-details")
                    yield Label("原始内容", classes="panel-heading")
                    yield Static(id="log-message")
                    yield Button("Enter 展开原文", id="expand-log", classes="wide-action")
                    yield Button("y 复制内容", id="copy-log", classes="wide-action")
                with Vertical(id="log-controls", classes="page-panel"):
                    yield Label("显示控制", classes="panel-heading")
                    yield Static("Space 暂停 / 恢复滚动\nCtrl+L 清空本地显示\nEnd 回到最新日志")
                    yield Static(id="log-paused-status")

    def on_mount(self) -> None:
        add_columns(self.query_one(DataTable), [("接收时间", 10), ("级别", 8), ("日志内容", None)])

    def render_state(self) -> None:
        if not self.is_mounted:
            return
        counts = Counter(entry.level for entry in self.state.logs)
        for identifier, value, color in (
            ("total", len(self.state.logs), CYAN),
            ("info", counts["info"], GREEN),
            ("warn", counts["warning"], "yellow"),
            ("error", counts["error"], "red"),
        ):
            self.query_one(f"#logs-{identifier}", Static).update(literal(f"{value:,}", color))
        query = self.query_one(Input).value
        level = str(self.query_one(Select).value)
        filters = (query, level)
        if self.paused and not self.rebuild:
            self.update_status()
            return
        if (
            self.last_seen == self.state.logs_seen
            and not self.rebuild
            and filters == self._rendered_filter
        ):
            self.update_status()
            return
        entries = list(self.state.logs)
        start = self.state.logs_seen - len(entries)
        delta = self.state.logs_seen - self.last_seen
        self.skipped += max(0, delta - len(entries))
        self.displayed = {
            str(start + index): entry
            for index, entry in enumerate(entries)
            if entry.matches(query, level)
        }
        table = self.query_one(DataTable)
        sync_table(
            table,
            (
                (
                    key,
                    (
                        entry.time,
                        literal(
                            "WARN" if entry.level == "warning" else entry.level.upper(),
                            {"warning": "yellow", "error": "red", "info": GREEN}.get(
                                entry.level, "dim"
                            ),
                        ),
                        entry.message.replace("\n", " ↵ "),
                    ),
                )
                for key, entry in self.displayed.items()
            ),
        )
        if not self.paused and table.row_count:
            table.move_cursor(row=table.row_count - 1, animate=False)
            table.scroll_end(animate=False, x_axis=False)
        self.last_seen = self.state.logs_seen
        self.rebuild = False
        self._rendered_filter = filters
        self.render_detail()
        self.update_status()

    def update_status(self) -> None:
        pending = max(0, self.state.logs_seen - self.last_seen)
        self.query_one("#log-count", Static).update(
            f"显示 {len(self.displayed):,} / 缓冲 {len(self.state.logs):,}"
            f" · 保留最近 {self.state.max_logs:,} 条"
        )
        self.query_one("#log-paused-status", Static).update(
            literal(
                f"滚动已暂停 · 新增 {pending:,} 条" if self.paused else "自动滚动中",
                "yellow" if self.paused else GREEN,
            )
        )

    @on(DataTable.RowHighlighted, "#log-output")
    def render_detail(self) -> None:
        entry = self.displayed.get(selected_key(self.query_one(DataTable)))
        self.query_one("#log-details", Static).update(
            fields(
                [
                    ("级别", entry.level.upper()),
                    ("时间", entry.time),
                    ("时间来源", entry.time_source),
                ]
            )
            if entry
            else "暂无日志"
        )
        self.query_one("#log-message", Static).update(literal(entry.message if entry else "—"))

    @on(Input.Changed, "#log-search")
    @on(Select.Changed, "#log-level")
    def filters_changed(self) -> None:
        self.rebuild = True
        self.render_state()

    @on(Input.Submitted, "#log-search")
    def search_submitted(self) -> None:
        self.query_one(DataTable).focus()

    def pause(self) -> None:
        if not self.paused:
            self.set_paused(True)

    def set_paused(self, value: bool) -> None:
        self.paused = value
        self.query_one("#log-autoscroll", Checkbox).value = not value
        self.query_one("#pause-logs", Button).label = "继续 (Space)" if value else "暂停 (Space)"
        if not value:
            self.rebuild = True
        self.render_state()

    @on(Checkbox.Changed, "#log-autoscroll")
    def autoscroll_changed(self, event: Checkbox.Changed) -> None:
        if self.paused == event.value:
            self.set_paused(not event.value)

    @on(Button.Pressed, "#pause-logs")
    def toggle_pause(self) -> None:
        self.set_paused(not self.paused)

    @on(Button.Pressed, "#clear-logs")
    def action_clear(self) -> None:
        self.state.logs.clear()
        self.displayed.clear()
        self.last_seen = self.state.logs_seen
        self.skipped = 0
        self.rebuild = True
        self.render_state()

    @on(DataTable.RowSelected, "#log-output")
    @on(Button.Pressed, "#expand-log")
    def expand(self) -> None:
        entry = self.displayed.get(selected_key(self.query_one(DataTable)))
        if entry:
            self.app.push_screen(Information("日志原文", entry.message))

    @on(Button.Pressed, "#copy-log")
    def action_copy(self) -> None:
        entry = self.displayed.get(selected_key(self.query_one(DataTable)))
        if entry:
            self.app.copy_to_clipboard(entry.message)
            self.notify("已发送到终端剪贴板", markup=False)

    def action_level(self) -> None:
        self.query_one(Select).focus()
