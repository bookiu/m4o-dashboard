"""Proxy groups, candidate inspection and explicit selection preview."""

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, DataTable, Input, Label, Static

from .components import clock_label, fields, literal
from .models import DashboardState
from .theme import CYAN, GREEN
from .ui import Command, VimTable, add_columns, selected_key, sync_table

KINDS = {"selector": "select", "urltest": "url-test", "fallback": "fallback"}


class Proxies(VerticalScroll):
    BINDINGS = [Binding("escape", "cancel_candidate", show=False)]

    def __init__(self, state: DashboardState, **kwargs) -> None:
        self.state = state
        self.group: str | None = None
        super().__init__(**kwargs)

    def compose(self) -> ComposeResult:
        with Horizontal(id="proxy-columns"):
            with Vertical(id="group-pane", classes="page-panel"):
                yield Label("代理组", classes="panel-heading")
                yield VimTable(id="groups", cursor_type="row", show_header=False)
                yield Static(id="group-help", classes="group-help")
                yield Button("恢复自动", id="unpin-node", classes="wide-action")
            with Vertical(id="proxy-main"):
                with Horizontal(id="proxy-toolbar", classes="page-panel compact-toolbar"):
                    yield Static("代理组", id="proxy-summary")
                    yield Input(placeholder="/ 搜索代理组或节点", id="proxy-search")
                    for mode in ("rule", "global", "direct"):
                        yield Button(mode, id=f"proxy-mode-{mode}", classes="proxy-mode")
                with Vertical(id="node-pane", classes="page-panel"):
                    yield Static("选择一个策略组", id="group-info", classes="panel-heading")
                    with Horizontal(classes="node-current"):
                        yield Static(id="current-node")
                        yield Button("t 测速当前组", id="test-node", classes="panel-action")
                        yield Button("r 刷新", id="proxy-refresh", classes="panel-action")
                    yield VimTable(id="nodes", cursor_type="row")
                    yield Static(id="node-count", classes="panel-hint")
                with Horizontal(id="proxy-bottom"):
                    with Vertical(classes="page-panel", id="node-detail-panel"):
                        yield Label("候选节点详情", classes="panel-heading")
                        yield Static(id="node-detail")
                    with Vertical(classes="page-panel", id="switch-panel"):
                        yield Label("切换预览", classes="panel-heading")
                        yield Static(id="switch-preview")
                        yield Button("Enter 应用选择", id="use-node", classes="wide-action")
                        yield Button("Esc 取消选择", id="cancel-node", classes="wide-action")

    def on_mount(self) -> None:
        add_columns(self.query_one("#groups", DataTable), [("代理组", None)])
        add_columns(
            self.query_one("#nodes", DataTable),
            [("节点", 27), ("类型", 17), ("UDP", 5), ("延迟", 12), ("状态", 8)],
        )

    def render_state(self) -> None:
        if not self.is_mounted:
            return
        groups = self.state.groups
        query = self.query_one(Input).value.casefold()
        visible = {
            name: group
            for name, group in groups.items()
            if not query
            or query in name.casefold()
            or any(
                query in f"{node} {self.state.proxies.get(node, {}).get('type', '')}".casefold()
                for node in group.get("all", [])
                if isinstance(node, str)
            )
        }
        if self.group not in visible:
            self.group = next(iter(visible), None)
        sync_table(
            self.query_one("#groups", DataTable),
            (
                (
                    name,
                    (
                        literal(
                            f"{index}  {name}\n   类型  "
                            f"{KINDS.get(str(group.get('type', '')).lower(), 'load-balance')}"
                            f"\n   当前  {group.get('now', '自动分配')}\n",
                            CYAN,
                        ),
                    ),
                )
                for index, (name, group) in enumerate(visible.items(), 1)
            ),
            row_height=4,
        )
        for mode in ("rule", "global", "direct"):
            button = self.query_one(f"#proxy-mode-{mode}", Button)
            active = mode == self.state.configs.get("mode")
            button.label = f"({'●' if active else ' '}) {mode}"
            button.set_class(active, "selected-mode")
        self.render_nodes()

    def render_nodes(self) -> None:
        group = self.state.groups.get(self.group or "", {})
        kind = str(group.get("type", "")).lower()
        current = str(group.get("now", "—"))
        self.query_one("#group-info", Static).update(
            literal(f"{self.group or '暂无策略组'} / {KINDS.get(kind, kind)}", CYAN)
        )
        self.query_one("#current-node", Static).update(literal(f"● 当前节点: {current}", GREEN))
        query = self.query_one(Input).value.casefold()
        names = list(dict.fromkeys(n for n in group.get("all", []) if isinstance(n, str)))

        # Keep unmeasured/direct nodes last; failed probes never appear as zero latency.
        def latency(name: str) -> float:
            label = self.state.delay_label(name)
            return float(label.split()[0]) if label.endswith(" ms") else float("inf")

        names.sort(key=latency)
        visible = [
            name
            for name in names
            if query in (self.group or "").casefold()
            or query in f"{name} {self.state.proxies.get(name, {}).get('type', '')}".casefold()
        ]
        rows = []
        for name in visible:
            node = self.state.proxies.get(name, {})
            delay = self.state.delay_label(name)
            status = (
                "当前"
                if name == current
                else "直连"
                if name == "DIRECT"
                else ("超时" if delay == "timeout" else "可用" if node.get("alive") else "未知")
            )
            rows.append(
                (
                    name,
                    (
                        literal(
                            ("● " if name == current else "  ") + name,
                            GREEN if name == current else "",
                        ),
                        str(node.get("type", "—")),
                        "是" if node.get("udp") else "否",
                        literal(
                            "—" if delay == "timeout" else delay,
                            "yellow" if delay == "timeout" else GREEN,
                        ),
                        literal(status, "yellow" if status == "超时" else GREEN),
                    ),
                )
            )
        sync_table(self.query_one("#nodes", DataTable), rows)
        self.query_one("#proxy-summary", Static).update(f"代理组  {len(self.state.groups)} 个组")
        self.query_one("#node-count", Static).update(
            f"显示 {len(rows)} / {len(names)}   按延迟排序"
        )
        selectable = kind in {"selector", "urltest", "fallback"}
        self.query_one("#use-node", Button).disabled = not selectable or not rows
        self.query_one("#test-node", Button).disabled = not names
        self.query_one("#unpin-node", Button).disabled = kind not in {"urltest", "fallback"}
        self.query_one("#group-help", Static).update(
            "手动选择组\n\nEnter 应用候选节点"
            if kind == "selector"
            else "自动策略组\n\n可固定节点 / 恢复自动"
            if selectable
            else "此策略组不支持手选"
        )
        self.render_detail()

    @on(DataTable.RowHighlighted, "#nodes")
    def render_detail(self) -> None:
        name = selected_key(self.query_one("#nodes", DataTable))
        node = self.state.proxies.get(name or "", {})
        current = self.state.groups.get(self.group or "", {}).get("now", "—")
        history = node.get("history") or []
        latest = history[-1] if history and isinstance(history[-1], dict) else {}
        self.query_one("#node-detail", Static).update(
            fields(
                [
                    ("名称", name or "—"),
                    ("类型", node.get("type", "—")),
                    ("UDP", "支持" if node.get("udp") else "不支持"),
                    (
                        "状态",
                        "超时"
                        if self.state.delay_label(name or "") == "timeout"
                        else "可用"
                        if node.get("alive")
                        else "未知",
                    ),
                    ("最近延迟", self.state.delay_label(name or "")),
                    ("来源", node.get("provider-name", "—")),
                    ("最近测速", clock_label(latest.get("time"))),
                ]
            )
        )
        preview = fields([("代理组", self.group or "—"), ("当前", current), ("待选", name or "—")])
        preview.append(
            "\n"
            + ("请选择候选节点" if not name else "尚未应用" if name != current else "当前已生效"),
            style="yellow" if name != current else GREEN,
        )
        self.query_one("#switch-preview", Static).update(preview)

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
        self.render_state()

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
        if self.group:
            self.post_message(Command("group-delay", self.group))

    @on(Button.Pressed, "#unpin-node")
    def unpin(self) -> None:
        if self.group and not self.query_one("#unpin-node", Button).disabled:
            self.post_message(Command("unpin", self.group))

    @on(Button.Pressed, "#cancel-node")
    def action_cancel_candidate(self) -> None:
        self.query_one(Input).value = ""
        current = self.state.groups.get(self.group or "", {}).get("now")
        table = self.query_one("#nodes", DataTable)
        if current in table.rows:
            table.move_cursor(row=table.get_row_index(current), animate=False)
        table.focus()

    @on(Button.Pressed, "#proxy-refresh")
    def refresh_proxy(self) -> None:
        self.post_message(Command("refresh"))

    @on(Button.Pressed, ".proxy-mode")
    def mode_changed(self, event: Button.Pressed) -> None:
        self.post_message(Command("mode", event.button.id.removeprefix("proxy-mode-")))
