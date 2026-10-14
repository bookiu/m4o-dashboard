"""Connection filtering, live inspection and per-connection traffic history."""

import time
from datetime import UTC, datetime

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, DataTable, Input, Label, Select, Static

from .charts import TrafficChart
from .components import fields, filter_options, literal, stat_card
from .models import DashboardState, mapping, size
from .theme import CYAN, GREEN, PURPLE
from .ui import Command, VimTable, add_columns, selected_key, sync_table


class Connections(VerticalScroll):
    def __init__(self, state: DashboardState, **kwargs) -> None:
        self.state = state
        self.paused = False
        self.paused_at: datetime | None = None
        self.entries = {}
        self.histories = {}
        super().__init__(**kwargs)

    def compose(self) -> ComposeResult:
        with Horizontal(classes="stat-strip"):
            for title, identifier in (
                ("活动连接", "conn-active"),
                ("下载速率", "conn-down"),
                ("上传速率", "conn-up"),
                ("内核累计", "conn-totals"),
            ):
                yield stat_card(title, identifier)
        with Horizontal(id="connections-layout"):
            with Vertical(id="connections-main"):
                with Horizontal(classes="compact-toolbar page-panel", id="connection-filters"):
                    yield Input(placeholder="/ 搜索域名、IP 或进程", id="connection-search")
                    yield Select(
                        [("全部网络", ""), ("TCP", "tcp"), ("UDP", "udp")],
                        value="",
                        allow_blank=False,
                        id="connection-network",
                        compact=True,
                    )
                    yield Select(
                        [("全部出站", "")],
                        value="",
                        allow_blank=False,
                        id="connection-outbound",
                        compact=True,
                    )
                    yield Select(
                        [
                            ("↓ 速率", "down_rate"),
                            ("↑ 速率", "up_rate"),
                            ("↓ 总量", "download"),
                            ("↑ 总量", "upload"),
                            ("连接 ID", "id"),
                        ],
                        value="down_rate",
                        allow_blank=False,
                        id="connection-sort",
                        compact=True,
                    )
                with Vertical(id="connection-list-panel", classes="page-panel"):
                    yield Label("活动连接", classes="panel-heading")
                    yield VimTable(id="connections-table", cursor_type="row")
                    yield Static(id="connection-count", classes="panel-hint")
                with Vertical(id="connection-traffic-panel", classes="page-panel"):
                    yield Static(
                        "所选连接流量 · 最近 60 秒",
                        id="connection-traffic-title",
                        classes="panel-heading",
                    )
                    with Horizontal(id="connection-traffic-body"):
                        yield Static(id="connection-traffic-stats")
                        yield TrafficChart(self.state, connection=True, id="connection-chart")
            with VerticalScroll(id="connection-inspector", classes="page-panel"):
                yield Label("连接详情", classes="panel-heading")
                yield Static(id="connection-details")
                yield Label("命中规则", classes="panel-heading")
                yield Static(id="connection-rule")
                yield Label("出站链路", classes="panel-heading")
                yield Static(id="connection-chain")
                yield Label("操作", classes="panel-heading")
                yield Button("Enter 原始详情", id="connection-detail", classes="wide-action")
                yield Button("d 关闭此连接", id="close-connection", classes="wide-action")
                yield Button("关闭全部连接", id="close-all", classes="wide-action")
                yield Button("Space 暂停刷新", id="pause-connections", classes="wide-action")
                yield Static("速率按采样差值计算", classes="panel-hint")

    def on_mount(self) -> None:
        add_columns(
            self.query_one(DataTable),
            [
                ("进程", 13),
                ("目标地址", 31),
                ("网络", 6),
                ("出站", 16),
                ("↓ 速率", 13),
                ("↑ 速率", 13),
            ],
        )

    @staticmethod
    def outbound(entry) -> str:
        chain = entry.raw.get("chains")
        return str(chain[0]) if isinstance(chain, list) and chain else "—"

    def render_state(self) -> None:
        if not self.is_mounted:
            return
        if not self.paused:
            self.entries = dict(self.state.connections)
            self.histories = {
                key: list(samples) for key, samples in self.state.connection_traffic.items()
            }
            tcp = sum(entry.network.lower() == "tcp" for entry in self.entries.values())
            self.query_one("#conn-active", Static).update(
                literal(f"{len(self.entries)}   TCP {tcp} · UDP {len(self.entries) - tcp}", GREEN)
            )
            self.query_one("#conn-down", Static).update(
                literal("↓ " + size(self.state.down, rate=True), CYAN)
            )
            self.query_one("#conn-up", Static).update(
                literal("↑ " + size(self.state.up, rate=True), PURPLE)
            )
            self.query_one("#conn-totals", Static).update(
                Text.assemble(
                    ("↓ " + size(self.state.download_total) + "  ", CYAN),
                    ("↑ " + size(self.state.upload_total), PURPLE),
                )
            )
        filter_options(
            self.query_one("#connection-outbound", Select),
            sorted({self.outbound(entry) for entry in self.entries.values()}),
        )
        self.render_entries()

    def render_entries(self) -> None:
        query = self.query_one(Input).value
        network = self.query_one("#connection-network", Select).value
        outbound = self.query_one("#connection-outbound", Select).value
        entries = [
            entry
            for entry in self.entries.values()
            if entry.matches(query)
            and (not network or entry.network.lower() == network)
            and (not outbound or self.outbound(entry) == outbound)
        ]
        sort = str(self.query_one("#connection-sort", Select).value)
        entries.sort(key=lambda entry: getattr(entry, sort, entry.id), reverse=sort != "id")
        sync_table(
            self.query_one(DataTable),
            (
                (
                    entry.id,
                    (
                        entry.process,
                        entry.host,
                        entry.network.upper(),
                        self.outbound(entry),
                        literal(size(entry.down_rate, rate=True), CYAN),
                        literal(size(entry.up_rate, rate=True), PURPLE),
                    ),
                )
                for entry in entries
            ),
        )
        self.query_one("#connection-count", Static).update(
            f"显示 {len(entries)} / {len(self.entries)}   "
            + ("已暂停刷新 · 后台仍接收" if self.paused else "自动刷新已开启")
        )
        self.query_one("#close-all", Button).disabled = not self.state.connections
        for identifier in ("#connection-detail", "#close-connection"):
            self.query_one(identifier, Button).disabled = not entries
        self.render_detail()

    @on(DataTable.RowHighlighted, "#connections-table")
    def render_detail(self) -> None:
        key = selected_key(self.query_one(DataTable))
        entry = self.entries.get(key)
        chart = self.query_one(TrafficChart)
        chart.samples = self.histories.get(key, [])
        chart.refresh()
        if not entry:
            for identifier in (
                "connection-details",
                "connection-rule",
                "connection-chain",
                "connection-traffic-stats",
            ):
                self.query_one(f"#{identifier}", Static).update("暂无所选连接")
            self.query_one("#connection-traffic-title", Static).update("所选连接流量 · 最近 60 秒")
            return
        meta = mapping(entry.raw.get("metadata"))
        started = str(entry.raw.get("start", "—"))
        duration = "—"
        try:
            date = datetime.fromisoformat(started.replace("Z", "+00:00"))
            if date.tzinfo:
                seconds = max(
                    0, int(((self.paused_at or datetime.now(UTC)) - date).total_seconds())
                )
                duration = f"{seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}"
                started = date.astimezone().strftime("%H:%M:%S")
        except ValueError:
            pass
        self.query_one("#connection-details", Static).update(
            fields(
                [
                    ("ID", entry.id),
                    ("进程", entry.process),
                    ("网络", entry.network.upper()),
                    ("目标域名", meta.get("host") or meta.get("destinationIP", "—")),
                    ("目标端口", meta.get("destinationPort", "—")),
                    ("源地址", meta.get("sourceIP", "—")),
                    ("源端口", meta.get("sourcePort", "—")),
                    ("开始时间", started),
                    ("持续时间", duration),
                ]
            )
        )
        self.query_one("#connection-rule", Static).update(literal(entry.rule))
        chain = entry.raw.get("chains")
        self.query_one("#connection-chain", Static).update(
            literal(
                " → ".join(str(part) for part in reversed(chain))
                if isinstance(chain, list)
                else "—"
            )
        )
        self.query_one("#connection-traffic-title", Static).update(
            literal(f"所选连接流量   {entry.host}   最近 60 秒")
        )
        self.query_one("#connection-traffic-stats", Static).update(
            Text.assemble(
                ("累计下载\n", ""),
                (size(entry.download) + "\n\n", CYAN),
                ("累计上传\n", ""),
                (size(entry.upload) + "\n\n", PURPLE),
                ("当前 ↓ " + size(entry.down_rate, rate=True) + "\n", CYAN),
                ("当前 ↑ " + size(entry.up_rate, rate=True), PURPLE),
            )
        )

    @on(Input.Changed, "#connection-search")
    @on(Select.Changed)
    def filters_changed(self) -> None:
        if self.is_mounted:
            self.render_entries()

    @on(Input.Submitted, "#connection-search")
    def search_submitted(self) -> None:
        self.query_one(DataTable).focus()

    @on(DataTable.RowSelected, "#connections-table")
    @on(Button.Pressed, "#connection-detail")
    def detail(self) -> None:
        key = selected_key(self.query_one(DataTable))
        if key:
            from .ui import Information

            entry = self.entries.get(key)
            if entry:
                self.app.push_screen(Information("连接详情", entry.raw))

    @on(Button.Pressed, "#close-connection")
    def close_selected(self) -> None:
        key = selected_key(self.query_one(DataTable))
        if key:
            self.post_message(Command("close", key))

    @on(Button.Pressed, "#close-all")
    def close_all(self) -> None:
        self.post_message(Command("close-all"))

    @on(Button.Pressed, "#pause-connections")
    def toggle_pause(self) -> None:
        self.paused = not self.paused
        self.paused_at = datetime.now(UTC) if self.paused else None
        self.query_one(TrafficChart).frozen_at = time.monotonic() if self.paused else None
        self.query_one("#pause-connections", Button).label = (
            "Space 恢复刷新" if self.paused else "Space 暂停刷新"
        )
        self.render_state()
