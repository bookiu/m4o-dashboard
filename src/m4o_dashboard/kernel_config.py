"""Configuration categories, staged changes and independent apply/save actions."""

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Checkbox, DataTable, Input, Label, Select, Static

from .components import fields, literal
from .models import CONFIG_CHOICES, CONFIG_PORTS, DashboardState
from .theme import CYAN, GREEN
from .ui import Command, VimTable, add_columns

LABELS = {
    "mode": "运行模式",
    "allow-lan": "允许局域网访问",
    "ipv6": "IPv6",
    "log-level": "日志级别",
    "find-process-mode": "进程匹配",
    "mixed-port": "混合代理端口",
    "port": "HTTP 代理端口",
    "socks-port": "SOCKS 代理端口",
    "redir-port": "Redir 代理端口",
    "tproxy-port": "TProxy 代理端口",
}
CATEGORIES = [
    ("常规", "general"),
    ("入站端口", "ports"),
    ("DNS", "dns"),
    ("TUN", "tun"),
    ("嗅探", "sniffer"),
    ("控制接口", "controller"),
    ("配置文件", "file"),
]


class KernelConfig(VerticalScroll):
    BINDINGS = [
        Binding("ctrl+enter", "apply", "应用", show=False),
        Binding("ctrl+s", "save", "保存", show=False),
        Binding("escape", "reset", "还原", show=False),
    ]

    def __init__(self, state: DashboardState, **kwargs) -> None:
        self.state = state
        self._baseline: dict | None = None
        self._saved: dict | None = None
        self._applied_file_changes: dict = {}
        self._saved_once = False
        self.category = "general"
        super().__init__(**kwargs)

    def compose(self) -> ComposeResult:
        with Horizontal(id="config-layout"):
            with Vertical(id="config-sidebar", classes="page-panel"):
                yield Label("配置分类", classes="panel-heading")
                yield VimTable(id="config-categories", cursor_type="row", show_header=False)
                yield Label("当前配置", classes="panel-heading")
                yield Static(id="config-file-name")
                yield Static(id="config-connection-status")
            with VerticalScroll(id="config-editor", classes="page-panel"):
                yield Static("全局配置 / 常规", id="config-heading", classes="panel-heading")
                with Vertical(id="general-fields"):
                    yield Label("常规设置", classes="panel-heading")
                    for name in ("mode", "allow-lan", "ipv6", "log-level", "find-process-mode"):
                        yield from self.field(name)
                with Vertical(id="port-fields"):
                    yield Label("入站端口", classes="panel-heading")
                    for name in CONFIG_PORTS:
                        yield from self.field(name)
                    yield Static("端口 0 表示关闭该独立入口。", classes="panel-hint")
                with Vertical(id="config-other"):
                    yield Static(id="config-category-detail")
                yield Static(id="config-status", classes="panel-hint")
            with Vertical(id="config-review"):
                with VerticalScroll(id="config-preview-panel", classes="page-panel"):
                    yield Label("修改预览", classes="panel-heading")
                    yield Static(id="config-preview")
                with VerticalScroll(id="config-actions", classes="page-panel"):
                    yield Label("应用与保存", classes="panel-heading")
                    yield Static(id="config-apply-status")
                    yield Static("应用与保存分别执行。", classes="config-explanation")
                    yield Button(
                        "Ctrl+Enter 应用到运行配置",
                        id="apply-config",
                        classes="wide-action",
                        variant="primary",
                    )
                    yield Button("Ctrl+S 保存到配置文件", id="save-config", classes="wide-action")
                    yield Button("Esc 放弃本次修改", id="reset-config", classes="wide-action")
                    yield Static(id="config-save-help", classes="config-explanation")

    def field(self, name: str) -> ComposeResult:
        with Horizontal(classes="config-row", id=f"row-{name}"):
            yield Label(f"{LABELS[name]}\n{name}")
            if name in CONFIG_PORTS:
                yield Input(placeholder="0–65535", id=f"config-{name}")
            elif name in {"allow-lan", "ipv6"}:
                yield Checkbox("true", id=f"config-{name}")
            else:
                yield Select(
                    [(value, value) for value in CONFIG_CHOICES[name]],
                    id=f"config-{name}",
                    compact=True,
                )
            yield Static("", id=f"changed-{name}", classes="changed-marker")

    def on_mount(self) -> None:
        table = self.query_one("#config-categories", DataTable)
        add_columns(table, [("分类", None)])
        for title, key in CATEGORIES:
            table.add_row(title, key=key)
        self.show_category()

    def form_values(self) -> dict:
        return {
            **{name: self.query_one(f"#config-{name}", Input).value for name in CONFIG_PORTS},
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
        if not self.is_mounted:
            return
        if not self.dirty:
            self.restore()
        self.render_preview()
        self.show_category()

    def restore(self) -> None:
        configs = self.state.configs
        for name in CONFIG_PORTS:
            widget = self.query_one(f"#config-{name}", Input)
            widget.value = str(configs.get(name, ""))
            widget.disabled = name not in configs
        for name in ("allow-lan", "ipv6"):
            widget = self.query_one(f"#config-{name}", Checkbox)
            widget.value = bool(configs.get(name, False))
            widget.label = "true" if widget.value else "false"
            widget.disabled = name not in configs
        for name, choices in CONFIG_CHOICES.items():
            widget = self.query_one(f"#config-{name}", Select)
            value = configs.get(name)
            widget.value = value if value in choices else Select.NULL
            widget.disabled = value not in choices
        self._baseline = self.form_values() if configs else None
        if self._saved is None and self._baseline is not None:
            self._saved = dict(self._baseline)
        self.query_one("#apply-config", Button).disabled = not configs
        self.query_one("#config-status", Static).update(
            "已同步内核配置 · 不支持的字段不可编辑" if configs else "等待控制器配置…"
        )
        self.render_preview()

    def changes(self, baseline: dict | None = None) -> dict:
        baseline = self._baseline if baseline is None else baseline
        if baseline is None:
            return {}
        values = self.form_values()
        return {
            name: value
            for name, value in values.items()
            if not self.query_one(f"#config-{name}").disabled and value != baseline.get(name)
        }

    def validated(self, changes: dict) -> dict | None:
        try:
            for name in CONFIG_PORTS:
                if name in changes:
                    changes[name] = int(changes[name])
                    if not 0 <= changes[name] <= 65535:
                        raise ValueError
        except ValueError:
            self.notify("端口必须是 0–65535 的整数", severity="error", markup=False)
            return None
        return changes

    @on(Input.Changed)
    @on(Checkbox.Changed)
    @on(Select.Changed)
    def form_changed(self) -> None:
        if self.is_mounted:
            self.render_preview()

    def render_preview(self) -> None:
        changes = self.changes()
        preview = Text()
        for index, (name, value) in enumerate(changes.items(), 1):
            preview.append(f"{index}. {name}\n", style=CYAN)
            preview.append(f"   {str(self._baseline[name]).lower()}  →  ")
            preview.append(f"{str(value).lower()}\n\n", style="yellow")
        if not changes:
            preview.append("暂无运行配置修改", style=GREEN)
        self.query_one("#config-preview", Static).update(preview)
        for name in LABELS:
            self.query_one(f"#changed-{name}", Static).update("*" if name in changes else "")
        for name in ("allow-lan", "ipv6"):
            checkbox = self.query_one(f"#config-{name}", Checkbox)
            checkbox.label = "true" if checkbox.value else "false"
        can_save = bool(getattr(self.app, "kernel_file", None)) or self.app.demo
        file_dirty = bool(self.file_changes())
        self.query_one("#save-config", Button).disabled = not can_save or not self.state.configs
        status = fields(
            [
                (
                    "运行配置",
                    "等待控制器"
                    if self._baseline is None
                    else f"{len(changes)} 项待应用"
                    if changes
                    else "已同步",
                ),
                (
                    "配置文件",
                    "未关联"
                    if not can_save
                    else "未保存"
                    if file_dirty
                    else "本次修改已保存"
                    if self._saved_once
                    else "无待保存修改",
                ),
            ]
        )
        self.query_one("#config-apply-status", Static).update(status)
        self.query_one("#config-file-name", Static).update(literal(self.state.config_name))
        self.query_one("#config-connection-status", Static).update(
            literal(
                "运行状态  " + ("已连接" if self.app.statuses["http"][0] else "连接中 / 断流"),
                GREEN,
            )
        )
        self.query_one("#config-save-help", Static).update(
            literal(
                "应用：更新当前运行设置。\n保存：写入 "
                + self.state.config_name
                + "。"
                + (
                    "\n演示模式仅模拟保存。"
                    if self.app.demo
                    else "\n只保存本次编辑的字段，保留其余内容。"
                )
                if can_save
                else "保存需要启动时指定\n--kernel-config /path/config.yaml\n"
                "仅支持此电脑可访问的 YAML。"
            )
        )

    @on(DataTable.RowHighlighted, "#config-categories")
    def category_changed(self, event: DataTable.RowHighlighted) -> None:
        self.category = str(event.row_key.value)
        self.show_category()

    @on(DataTable.RowSelected, "#config-categories")
    def category_selected(self) -> None:
        target = "#config-mode" if self.category == "general" else "#config-mixed-port"
        if self.category in {"general", "ports"}:
            self.query_one(target).focus()

    def show_category(self) -> None:
        title = dict((key, title) for title, key in CATEGORIES)[self.category]
        self.query_one("#config-heading", Static).update(f"全局配置 / {title}")
        self.query_one("#general-fields").display = self.category == "general"
        self.query_one("#port-fields").display = self.category in {"general", "ports"}
        for name in ("redir-port", "tproxy-port"):
            self.query_one(f"#row-{name}").display = self.category == "ports"
        self.query_one("#config-other").display = self.category not in {"general", "ports"}
        if self.category in {"general", "ports"}:
            return
        if self.category == "file":
            content = (
                f"当前配置：{self.state.config_name}\n\n应用和保存彼此独立。\n"
                "保存前自动创建备份，外部修改会阻止覆盖。"
            )
        elif self.category == "controller":
            content = (
                f"控制器：{self.app.settings.url}\n"
                f"TLS 验证：{self.app.settings.verify_tls}\n"
                "\n凭据不显示在界面中。"
            )
        else:
            data = self.state.configs.get(self.category)
            content = (
                "\n".join(
                    f"{key}: {value}"
                    for key, value in data.items()
                    if key not in {"secret", "password"}
                )
                if isinstance(data, dict)
                else "当前控制器未返回此分类的配置。"
            )
            content += "\n\n此分类为只读预览；请在内核 YAML 中编辑。"
        if self.app.settings.secret:
            content = content.replace(self.app.settings.secret, "[redacted]")
        self.query_one("#config-category-detail", Static).update(literal(content))

    @on(Button.Pressed, "#reset-config")
    def action_reset(self) -> None:
        self.restore()
        self.query_one("#config-categories").focus()

    @on(Button.Pressed, "#apply-config")
    def action_apply(self) -> None:
        changes = self.validated(self.changes())
        if changes:
            self.post_message(Command("config", changes=changes))
        elif changes is not None:
            self.notify("没有需要应用的修改", markup=False)

    @on(Button.Pressed, "#save-config")
    def action_save(self) -> None:
        if self.query_one("#save-config", Button).disabled:
            self.notify("请先使用 --kernel-config 关联本地 YAML", severity="warning", markup=False)
            return
        changes = self.validated(self.file_changes())
        if changes:
            self.post_message(Command("config-save", changes=changes))
        elif changes is not None:
            self.notify("没有需要保存的修改", markup=False)

    def file_changes(self) -> dict:
        pending = {**self._applied_file_changes, **self.changes()}
        return {
            name: value for name, value in pending.items() if value != (self._saved or {}).get(name)
        }

    def applied(self, changes: dict) -> None:
        # Only user-edited fields may be persisted; polling changes are not edits.
        self._applied_file_changes.update(
            {name: str(value) if name in CONFIG_PORTS else value for name, value in changes.items()}
        )

    def saved(self) -> None:
        self._saved_once = True
        self._saved = dict(self.form_values())
        self._applied_file_changes.clear()
        self.render_preview()
