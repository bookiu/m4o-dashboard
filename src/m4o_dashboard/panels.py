"""Ordered rule browsing with rule-provider status and updates."""

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, DataTable, Input, Label, Select, Static

from .components import clock_label, fields, filter_options, literal, stat_card
from .models import DashboardState
from .theme import CYAN, GREEN, PURPLE
from .ui import Command, Information, VimTable, add_columns, selected_key, sync_table


class Rules(VerticalScroll):
    BINDINGS = [Binding("escape", "clear_search", show=False), Binding("r", "refresh", show=False)]

    def __init__(self, state: DashboardState, **kwargs) -> None:
        self.state = state
        super().__init__(**kwargs)

    def compose(self) -> ComposeResult:
        with Horizontal(classes="stat-strip rules-stats"):
            yield stat_card("路由规则", "rules-total")
            yield stat_card("规则集", "providers-total")
            yield stat_card("配置", "rules-config-name")
        with Horizontal(id="rules-layout"):
            with Vertical(id="rules-main"):
                with Vertical(id="rules-list-panel", classes="page-panel"):
                    yield Label("规则列表", classes="panel-heading")
                    with Horizontal(classes="compact-toolbar"):
                        yield Input(placeholder="/ 搜索规则类型、匹配内容或策略", id="rule-search")
                        yield Select(
                            [("全部类型", "")],
                            value="",
                            allow_blank=False,
                            id="rule-type",
                            compact=True,
                        )
                        yield Select(
                            [("全部策略", "")],
                            value="",
                            allow_blank=False,
                            id="rule-policy",
                            compact=True,
                        )
                    yield Static("按配置顺序匹配 · 高亮行用于查看详情", classes="panel-hint")
                    yield VimTable(id="rules-table", cursor_type="row")
                    yield Static(id="rules-status", classes="panel-hint")
                with Vertical(id="raw-rule-panel", classes="page-panel"):
                    yield Label("规则表达式", classes="panel-heading")
                    yield Static(id="raw-rule")
            with Vertical(id="rules-sidebar"):
                with VerticalScroll(id="rule-detail-panel", classes="page-panel"):
                    yield Label("规则详情", classes="panel-heading")
                    yield Static(id="rule-details")
                    yield Label("规则集详情", classes="panel-heading")
                    yield Static(id="rule-provider-details")
                with Vertical(id="providers-panel", classes="page-panel"):
                    yield Label("规则集", classes="panel-heading")
                    yield VimTable(id="providers-table", cursor_type="row")
                    yield Static(id="providers-status", classes="panel-hint")
                    yield Button("r 更新选中规则集", id="refresh-provider", classes="wide-action")
                    yield Button("刷新列表", id="refresh-rules", classes="wide-action")

    def on_mount(self) -> None:
        add_columns(
            self.query_one("#rules-table", DataTable),
            [("#", 5), ("类型", 20), ("匹配内容", 32), ("目标策略", 23)],
        )
        add_columns(
            self.query_one("#providers-table", DataTable),
            [("名称", 14), ("状态", 8), ("更新于", 12)],
        )

    def render_state(self) -> None:
        if not self.is_mounted:
            return
        rules = self.state.rules or []
        filter_options(self.query_one("#rule-type", Select), sorted({rule.kind for rule in rules}))
        filter_options(
            self.query_one("#rule-policy", Select), sorted({rule.proxy for rule in rules})
        )
        self.query_one("#rules-total", Static).update(literal(f"{len(rules):,}", CYAN))
        self.query_one("#providers-total", Static).update(
            literal(str(len(self.state.rule_providers)), PURPLE)
        )
        self.query_one("#rules-config-name", Static).update(literal(self.state.config_name, GREEN))
        query = self.query_one(Input).value
        kind = self.query_one("#rule-type", Select).value
        policy = self.query_one("#rule-policy", Select).value
        visible = [
            rule
            for rule in rules
            if rule.matches(query)
            and (not kind or rule.kind == kind)
            and (not policy or rule.proxy == policy)
        ]
        sync_table(
            self.query_one("#rules-table", DataTable),
            (
                (str(rule.index), (f"{rule.index:03}", rule.kind, rule.payload, rule.proxy))
                for rule in visible
            ),
        )
        status = f"显示 {len(visible)} / {len(rules)} · 保留原始序号   Enter 详情   g/G 首尾"
        if self.state.rules_error:
            status = f"规则读取失败，保留最后数据 · {self.state.rules_error}"
        elif self.state.rules is None:
            status = "等待规则信息…"
        self.query_one("#rules-status", Static).update(literal(status))
        sync_table(
            self.query_one("#providers-table", DataTable),
            (
                (
                    name,
                    (
                        name,
                        literal(self.provider_status(provider), GREEN),
                        clock_label(provider.get("updatedAt")),
                    ),
                )
                for name, provider in self.state.rule_providers.items()
            ),
        )
        self.query_one("#providers-status", Static).update(
            literal(self.state.providers_error or "Enter 查看详情")
        )
        self.query_one("#refresh-provider", Button).disabled = not self.state.rule_providers
        self.render_detail()

    @staticmethod
    def provider_status(provider: dict) -> str:
        if provider.get("vehicleType") == "Inline":
            return "内联"
        return "已加载" if provider.get("updatedAt") else "未知"

    def selected_rule(self):
        key = selected_key(self.query_one("#rules-table", DataTable))
        return next((rule for rule in self.state.rules or [] if str(rule.index) == key), None)

    @on(DataTable.RowHighlighted, "#rules-table")
    def render_detail(self) -> None:
        rule = self.selected_rule()
        if not rule:
            self.query_one("#rule-details", Static).update("暂无匹配规则")
            self.query_one("#raw-rule", Static).update("—")
            self.query_one("#rule-provider-details", Static).update("—")
            return
        self.query_one("#rule-details", Static).update(
            fields(
                [
                    ("序号", f"{rule.index:03}"),
                    ("类型", rule.kind),
                    ("匹配内容", rule.payload),
                    ("目标策略", rule.proxy),
                    ("匹配来源", "规则集" if rule.kind == "RULE-SET" else "配置规则"),
                ]
            )
        )
        # /rules does not expose flags such as no-resolve; label this as an expression.
        expression = (
            f"MATCH,{rule.proxy}"
            if rule.kind == "MATCH"
            else f"{rule.kind},{rule.payload},{rule.proxy}"
        )
        self.query_one("#raw-rule", Static).update(literal(expression))
        self.render_provider(rule.payload if rule.kind == "RULE-SET" else "")

    def render_provider(self, name: str) -> None:
        provider = self.state.rule_providers.get(name)
        self.query_one("#rule-provider-details", Static).update(
            fields(
                [
                    ("名称", name),
                    ("behavior", provider.get("behavior", "—")),
                    ("format", provider.get("format", "—")),
                    ("状态", self.provider_status(provider)),
                    ("规则数量", provider.get("ruleCount", "—")),
                    ("更新时间", clock_label(provider.get("updatedAt"))),
                ]
            )
            if provider
            else "此规则无关联规则集"
            if not name
            else "规则集信息不可用"
        )

    @on(DataTable.RowHighlighted, "#providers-table")
    def provider_highlighted(self) -> None:
        self.render_provider(selected_key(self.query_one("#providers-table", DataTable)) or "")

    @on(Input.Changed, "#rule-search")
    @on(Select.Changed)
    def search_changed(self) -> None:
        self.render_state()

    @on(Input.Submitted, "#rule-search")
    def search_submitted(self) -> None:
        self.query_one("#rules-table", DataTable).focus()

    @on(Button.Pressed, "#refresh-rules")
    def refresh_rules(self) -> None:
        self.post_message(Command("refresh"))

    @on(Button.Pressed, "#refresh-provider")
    def update_provider(self) -> None:
        name = selected_key(self.query_one("#providers-table", DataTable))
        if name:
            self.post_message(Command("provider-update", name))

    def action_refresh(self) -> None:
        if self.query_one("#providers-table").has_focus:
            self.update_provider()
        else:
            self.refresh_rules()

    def action_clear_search(self) -> None:
        self.query_one(Input).value = ""
        for selector in self.query(Select):
            selector.value = ""
        self.query_one("#rules-table", DataTable).focus()

    @on(DataTable.RowSelected, "#rules-table")
    def detail(self) -> None:
        rule = self.selected_rule()
        if rule:
            if rule.kind == "RULE-SET" and rule.payload in self.state.rule_providers:
                table = self.query_one("#providers-table", DataTable)
                table.move_cursor(row=table.get_row_index(rule.payload))
                table.focus()
            else:
                self.app.push_screen(
                    Information(
                        "规则详情",
                        {
                            "序号": rule.index,
                            "类型": rule.kind,
                            "匹配内容": rule.payload,
                            "策略": rule.proxy,
                        },
                    )
                )

    @on(DataTable.RowSelected, "#providers-table")
    def provider_detail(self) -> None:
        name = selected_key(self.query_one("#providers-table", DataTable))
        if name:
            self.app.push_screen(Information(f"规则集 {name}", self.state.rule_providers[name]))
