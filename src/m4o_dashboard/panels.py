"""Rule browsing and explicit, allowlisted kernel configuration editing."""

from __future__ import annotations

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Checkbox, DataTable, Input, Label, Select, Static

from .models import CONFIG_CHOICES, DashboardState
from .ui import Command, Information, VimTable, add_columns, display_text, selected_key, sync_table


class Rules(Vertical):
    def __init__(self, state: DashboardState, **kwargs) -> None:
        self.state = state
        super().__init__(**kwargs)

    def compose(self) -> ComposeResult:
        yield Label("路由规则", classes="section-title")
        with Horizontal(classes="toolbar"):
            yield Input(placeholder="/ 搜索规则类型、匹配内容、策略", id="rule-search")
            yield Button("刷新 (r)", id="refresh-rules")
        yield VimTable(id="rules-table", cursor_type="row", zebra_stripes=True)
        yield Static("等待规则信息…", id="rules-status", classes="hint")

    def on_mount(self) -> None:
        add_columns(
            self.query_one(DataTable), [("#", 6), ("类型", 20), ("匹配内容", 42), ("策略", 26)]
        )

    def render_state(self) -> None:
        query = self.query_one(Input).value
        rules = self.state.rules or []
        visible = [rule for rule in rules if rule.matches(query)]
        sync_table(
            self.query_one(DataTable),
            (
                (str(rule.index), (str(rule.index), rule.kind, rule.payload, rule.proxy))
                for rule in visible
            ),
        )
        status = f"显示 {len(visible)} / {len(rules)} 条规则 · 按内核顺序匹配 · Enter 查看详情"
        if self.state.rules_error:
            status = f"规则读取失败，保留最后数据 · {self.state.rules_error}"
        elif self.state.rules is None:
            status = "等待规则信息…"
        self.query_one("#rules-status", Static).update(Text(display_text(status)))

    @on(Input.Changed, "#rule-search")
    def search_changed(self) -> None:
        self.render_state()

    @on(Input.Submitted, "#rule-search")
    def search_submitted(self) -> None:
        self.query_one(DataTable).focus()

    @on(Button.Pressed, "#refresh-rules")
    def refresh_rules(self) -> None:
        self.post_message(Command("refresh"))

    @on(DataTable.RowSelected, "#rules-table")
    def detail(self) -> None:
        key = selected_key(self.query_one(DataTable))
        rule = next((rule for rule in self.state.rules or [] if str(rule.index) == key), None)
        if rule:
            self.app.push_screen(
                Information(
                    "规则详情", {"类型": rule.kind, "匹配内容": rule.payload, "策略": rule.proxy}
                )
            )


class KernelConfig(VerticalScroll):
    BINDINGS = [
        Binding("ctrl+s", "apply", "应用", show=False),
        Binding("escape", "reset", "还原", show=False),
    ]

    def __init__(self, state: DashboardState, **kwargs) -> None:
        self.state = state
        self._baseline: dict | None = None
        super().__init__(**kwargs)

    def compose(self) -> ComposeResult:
        yield Label("全局配置 · 编辑", classes="section-title")
        yield Static("修改内核运行配置，不编辑本地 YAML 文件。", classes="hint")
        with Vertical(classes="config-form"):
            with Horizontal(classes="config-row"):
                yield Label("mixed-port")
                yield Input(placeholder="0–65535，0 为关闭", id="config-mixed-port")
            for name in ("allow-lan", "ipv6"):
                with Horizontal(classes="config-row"):
                    yield Label(name)
                    yield Checkbox("启用", id=f"config-{name}")
            for name, choices in CONFIG_CHOICES.items():
                with Horizontal(classes="config-row"):
                    yield Label(name)
                    yield Select([(value, value) for value in choices], id=f"config-{name}")
        yield Static(
            "allow-lan 会开放局域网访问；修改端口可能影响客户端。\n"
            "轮询不会覆盖未提交的修改；只提交更改过的字段。",
            classes="hint",
        )
        with Horizontal(classes="toolbar"):
            yield Button("应用 (Ctrl+S)", id="apply-config", variant="primary")
            yield Button("还原 (Esc)", id="reset-config")
        yield Static("等待控制器配置…", id="config-status", classes="hint")

    def form_values(self) -> dict:
        return {
            "mixed-port": self.query_one("#config-mixed-port", Input).value,
            **{
                name: self.query_one(f"#config-{name}", Checkbox).value
                for name in ("allow-lan", "ipv6")
            },
            **{name: self.query_one(f"#config-{name}", Select).value for name in CONFIG_CHOICES},
        }

    @property
    def dirty(self) -> bool:
        return self._baseline is not None and self.form_values() != self._baseline

    def render_state(self) -> None:
        if not self.dirty:
            self.restore()

    @on(Button.Pressed, "#reset-config")
    def action_reset(self) -> None:
        self.restore()
        self.query_one("#reset-config", Button).focus()

    def restore(self) -> None:
        configs = self.state.configs
        port = self.query_one("#config-mixed-port", Input)
        port.value = str(configs.get("mixed-port", ""))
        port.disabled = "mixed-port" not in configs
        for name in ("allow-lan", "ipv6"):
            checkbox = self.query_one(f"#config-{name}", Checkbox)
            checkbox.value = bool(configs.get(name, False))
            checkbox.disabled = name not in configs
        for name, choices in CONFIG_CHOICES.items():
            selector = self.query_one(f"#config-{name}", Select)
            value = configs.get(name)
            selector.value = value if value in choices else Select.NULL
            selector.disabled = value not in choices
        self._baseline = self.form_values() if configs else None
        self.query_one("#apply-config", Button).disabled = not configs
        self.query_one("#config-status", Static).update(
            "已同步内核配置 · 不支持的字段不可编辑" if configs else "等待控制器配置…"
        )

    @on(Button.Pressed, "#apply-config")
    def action_apply(self) -> None:
        if self._baseline is None:
            return
        current = self.form_values()
        changes = {
            name: value
            for name, value in current.items()
            if not self.query_one(f"#config-{name}").disabled and value != self._baseline[name]
        }
        if "mixed-port" in changes:
            try:
                changes["mixed-port"] = int(changes["mixed-port"])
            except ValueError:
                self.notify("mixed-port 必须是 0–65535 的整数", severity="error", markup=False)
                return
        if not changes:
            self.notify("没有需要应用的修改", markup=False)
            return
        self.post_message(Command("config", changes=changes))
