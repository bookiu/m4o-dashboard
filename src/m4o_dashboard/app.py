"""Application coordinator: async network tasks, shared state, and UI commands."""

from __future__ import annotations

import asyncio
import time
from contextlib import AsyncExitStack
from dataclasses import replace

from rich.text import Text
from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widget import Widget
from textual.widgets import DataTable, Input, Static, TabbedContent, TabPane
from textual.worker import Worker, WorkerState

from .api import APIError, MihomoClient
from .config import Settings
from .models import DashboardState, size
from .overview import Overview, RouteControl
from .panels import KernelConfig, Rules
from .theme import CYAN, DASHBOARD_THEME, FOREGROUND, GREEN, MUTED
from .ui import Command, Confirm, Connections, Information, Logs, Proxies, display_text

HELP = """M4O · Mihomo terminal dashboard

1 / 2 / 3 / 4 / 5 / 6    概览 / 代理组 / 规则 / 连接 / 日志 / 全局配置
Tab / Shift+Tab 切换焦点
↑ ↓ / j k       表格导航；← → / 滚轮：滚动
Enter           打开组的节点列表 / 使用选中节点 / 查看连接详情
/               聚焦当前页面搜索框
Esc             离开搜索框 / 取消弹窗
r               刷新配置、版本、代理和规则列表
t               概览 / 代理页：测试选中节点延迟（非带宽测速）
m               概览 / 代理页：切换 rule / global / direct 模式
Delete / d      连接表格：确认后关闭选中连接
Space           概览 / 日志页：暂停 / 恢复日志绘制（后台仍接收）
Ctrl+S          全局配置：应用更改；Esc 还原未提交的修改
End             日志输出获得焦点时：滚到最新日志
?               本帮助
q / Ctrl+C      退出

鼠标：点击标签切换页面，点击表格选择行，按钮执行操作。
代理表格中 Enter 或点击已选中的行会切换节点。
所有关闭连接操作均需确认；默认焦点为取消。

搜索框内的普通字母和数字用于输入，不触发全局快捷键。
自动策略组可固定节点 / 恢复自动（需内核支持）；LoadBalance 不支持手选。
切换代理或模式通常只影响新连接，不会自动中断已有连接。
内核断开时保留最后数据，状态栏标记断流，后台指数退避重连。
日志按本地级别/关键词过滤；缓冲有上限，非持久化日志存储。
"""


class Dashboard(App[None]):
    TITLE = "M4O · Mihomo"
    CSS_PATH = "dashboard.tcss"
    AUTO_FOCUS = "#overview-view"
    BINDINGS = [
        Binding("1", "page('overview')", "概览"),
        Binding("2", "page('proxies')", "代理组"),
        Binding("3", "page('rules')", "规则"),
        Binding("4", "page('connections')", "连接"),
        Binding("5", "page('logs')", "日志"),
        Binding("6", "page('config')", "全局配置"),
        Binding("slash", "search", "搜索", key_display="/"),
        Binding("r", "refresh", "刷新"),
        Binding("t", "test_delay", show=False),
        Binding("delete,d", "close_connection", show=False),
        Binding("m", "cycle_mode", show=False),
        Binding("space", "pause_logs", show=False),
        Binding("escape", "escape_search", show=False),
        Binding("question_mark", "help", "帮助", key_display="?"),
        Binding("q", "quit", "退出"),
    ]

    def __init__(self, settings: Settings, *, demo: bool = False) -> None:
        super().__init__()
        self.register_theme(DASHBOARD_THEME)
        self.theme = "m4o"
        self._started = time.monotonic()
        self._header_second = -1
        self._config_focus: Widget | None = None
        self.settings = settings
        self.demo = demo
        self.state = DashboardState(max_logs=settings.max_logs)
        self.client: MihomoClient | None = None
        self.statuses = {
            name: (False, "connecting…") for name in ("http", "traffic", "connections", "logs")
        }
        self._dirty = {"overview", "proxies", "rules", "connections", "logs", "config", "status"}
        self._refresh_event = asyncio.Event()
        self._refresh_lock = asyncio.Lock()
        self._mutation_busy = False
        self._activity = ""

    def compose(self) -> ComposeResult:
        yield Static("mihomo / dashboard · 正在连接…", id="network-status", markup=False)
        with TabbedContent(initial="overview", id="pages"):
            with TabPane("1 概览", id="overview"):
                yield Overview(self.state, id="overview-view")
            with TabPane("2 代理组", id="proxies"):
                yield Proxies(self.state, id="proxies-view")
            with TabPane("3 规则", id="rules"):
                yield Rules(self.state, id="rules-view")
            with TabPane("4 连接", id="connections"):
                yield Connections(self.state, id="connections-view")
            with TabPane("5 日志", id="logs"):
                yield Logs(self.state, id="logs-view")
            with TabPane("6 全局配置", id="config"):
                yield KernelConfig(self.state, id="config-view")
        yield Static(id="dashboard-footer", markup=False)

    def on_mount(self) -> None:
        self.screen.set_class(self.size.width < 100, "narrow")
        self.set_interval(0.25, self.flush_ui)
        self.network()

    def on_resize(self, event: events.Resize) -> None:
        if self.screen_stack:
            self.screen_stack[0].set_class(event.size.width < 100, "narrow")
            self._dirty.update(("overview", "status"))

    def set_status(self, name: str, connected: bool, detail: str) -> None:
        value = (connected, detail)
        if self.statuses.get(name) != value:
            self.statuses[name] = value
            self._dirty.add("status")
        if name == "connections" and not connected:
            # Do not divide a counter delta by a disconnected interval.
            self.state.last_connection_time = None

    @work(group="network", exclusive=True, exit_on_error=False)
    async def network(self) -> None:
        async with AsyncExitStack() as stack:
            if self.demo:
                from .demo import DemoServer

                server = await stack.enter_async_context(DemoServer())
                self.settings = replace(
                    self.settings,
                    url=server.url,
                    secret=server.secret,
                    proxy="",
                    refresh_interval=2,
                )
            self.sub_title = ("DEMO · " if self.demo else "") + self.settings.url
            self.client = await stack.enter_async_context(
                MihomoClient(self.settings, self.set_status)
            )
            self._dirty.add("overview")
            tasks = [asyncio.create_task(self.poll())]
            tasks.extend(
                asyncio.create_task(self.consume(name))
                for name in ("traffic", "connections", "logs")
            )
            try:
                await asyncio.gather(*tasks)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                self.client = None

    async def refresh_snapshot(self) -> None:
        if self.client is None:
            raise APIError("Controller is starting; try again shortly")
        async with self._refresh_lock:
            version, configs, proxies = await self.client.snapshot()
            self.state.update_snapshot(version, configs, proxies)
            self.set_status("http", True, "live")
            self._dirty.update(("overview", "proxies", "config"))
            try:
                self.state.update_rules(await self.client.rules())
            except APIError as exc:
                self.state.rules_error = str(exc)
            self._dirty.update(("overview", "rules"))

    async def poll(self) -> None:
        while True:
            self._refresh_event.clear()
            try:
                await self.refresh_snapshot()
            except APIError as exc:
                self.set_status("http", False, str(exc))
                if exc.status is None and self.client is not None:
                    await self.client.reconnect_streams()
            try:
                await asyncio.wait_for(self._refresh_event.wait(), self.settings.refresh_interval)
            except TimeoutError:
                pass

    async def consume(self, name: str) -> None:
        assert self.client is not None
        params = (
            {"interval": self.settings.interval}
            if name == "connections"
            else {"level": "debug"}
            if name == "logs"
            else None
        )
        frames = 0
        async for value in self.client.stream(name, params):
            try:
                if name == "traffic":
                    self.state.update_traffic(value)
                    self._dirty.add("overview")
                elif name == "connections":
                    self.state.update_connections(value)
                    self._dirty.update(("overview", "connections"))
                else:
                    self.state.add_log(value, self.settings.secret)
                    self._dirty.update(("overview", "logs"))
            except (ValueError, TypeError, KeyError):
                self.set_status(name, False, "invalid data; waiting for next update")
            frames += 1
            if frames % 100 == 0:
                # A burst of buffered WebSocket frames must not starve keyboard input.
                await asyncio.sleep(0)

    def flush_ui(self) -> None:
        # Timers may fire while Textual is unmounting children during shutdown.
        if not self.is_running or not self.screen_stack:
            return
        # ModalScreen's query scope differs; query the underlying default screen.
        screen = self.screen_stack[0]
        pages = screen.query_one("#pages", TabbedContent)
        elapsed = int(time.monotonic() - self._started)
        if "status" in self._dirty or elapsed != self._header_second:
            self._header_second = elapsed
            width = max(1, self.size.width - 6)
            connected = all(status[0] for status in self.statuses.values())
            line = Text("mihomo", style=f"bold {CYAN}")
            line.append(" / dashboard  │  ", style=FOREGROUND)
            line.append(
                "● 已连接" if connected else "○ 连接中 / 断流",
                style=GREEN if connected else "yellow",
            )
            endpoint = self.settings.url.removeprefix("http://").removeprefix("https://")
            line.append(f"  {display_text(endpoint)}", style=FOREGROUND)
            if width >= 100:
                line.append(
                    f"  运行 {elapsed // 3600:02}:{elapsed // 60 % 60:02}:{elapsed % 60:02}",
                    style=FOREGROUND,
                )
            right = Text(
                f"内核 {display_text(self.state.version)}   "
                + ("DEMO DATA" if self.demo else "LIVE DATA"),
                style=MUTED,
            )
            if line.cell_len + right.cell_len + 2 < width:
                line.append(" " * (width - line.cell_len - right.cell_len))
                line.append(right)
            line.truncate(width, overflow="ellipsis")
            failed = [f"{name}: {detail}" for name, (ok, detail) in self.statuses.items() if not ok]
            if failed:
                line.append(
                    "\n" + display_text(failed[0]) + " · 保留最后数据 / 自动重试", style="yellow"
                )
            if self._activity:
                line.append("\n" + self._activity, style=CYAN)
            if not self.settings.verify_tls:
                line.append("\nTLS verification DISABLED", style="bold red")
            header = screen.query_one("#network-status", Static)
            header.update(line)
            header.tooltip = Text(
                "\n".join(
                    display_text(f"{name}: {detail}") for name, (_, detail) in self.statuses.items()
                )
            )
            left = "Tab 切换区域   ↑↓ 选择   Enter 确认   / 搜索   ? 帮助   q 退出"
            if self.size.width < 100:
                left = "1–6 页面  Tab 焦点  / 搜索  ? 帮助  q 退出"
            footer = Text(left, style=FOREGROUND)
            memory = size(self.state.memory) if self.state.memory is not None else "—"
            right = Text(f"{self.settings.interval / 1000:g}s 连接刷新 · {memory}", style=MUTED)
            if width > footer.cell_len + right.cell_len + 2:
                footer.append(" " * (width - footer.cell_len - right.cell_len))
                footer.append(right)
            screen.query_one("#dashboard-footer", Static).update(footer)
            self._dirty.discard("status")
        active = pages.active
        if active in self._dirty:
            view = {
                "overview": Overview,
                "proxies": Proxies,
                "rules": Rules,
                "connections": Connections,
                "config": KernelConfig,
            }.get(active)
            if view:
                screen.query_one(view).render_state()
            self._dirty.discard(active)
        if active == "logs":
            # Also check while manually paused/scrolled back, so End can resume.
            screen.query_one(Logs).render_state()

    @on(TabbedContent.TabActivated, "#pages")
    def page_activated(self, event: TabbedContent.TabActivated) -> None:
        self._dirty.add(event.pane.id or "overview")
        self.call_after_refresh(self.flush_ui)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        # App-level shortcuts must never reach the obscured page behind a dialog.
        if len(self.screen_stack) > 1 and action in {
            "page",
            "search",
            "refresh",
            "test_delay",
            "cycle_mode",
            "close_connection",
            "pause_logs",
            "escape_search",
            "help",
        }:
            return False
        return True

    def current_page(self) -> str:
        return self.screen_stack[0].query_one("#pages", TabbedContent).active

    def action_page(self, page: str) -> None:
        if isinstance(self.screen, (Confirm, Information)):
            return
        self.query_one("#pages", TabbedContent).active = page
        self._dirty.add(page)
        self.flush_ui()
        target = {
            "overview": "#overview-view",
            "proxies": "#groups",
            "rules": "#rules-table",
            "connections": "#connections-table",
            "logs": "#log-output",
            "config": "#config-mixed-port",
        }[page]
        self.query_one(target).focus()
        if page == "overview":
            self.query_one(Overview).scroll_home(animate=False)

    def action_search(self) -> None:
        page = self.current_page()
        if page == "overview":
            focused_id = self.focused.id if self.focused else ""
            page = {"overview-connections": "connections", "overview-rules": "rules"}.get(
                focused_id, "proxies"
            )
            self.action_page(page)
        if page == "config":
            return
        identifier = {
            "proxies": "proxy-search",
            "rules": "rule-search",
            "connections": "connection-search",
            "logs": "log-search",
        }[page]
        self.query_one(f"#{identifier}", Input).focus()

    def action_escape_search(self) -> None:
        if isinstance(self.focused, Input):
            target = {
                "proxies": "#nodes",
                "rules": "#rules-table",
                "connections": "#connections-table",
                "logs": "#log-output",
            }.get(self.current_page())
            if target:
                self.query_one(target).focus()

    def action_refresh(self) -> None:
        self._refresh_event.set()

    def action_test_delay(self) -> None:
        if self.current_page() == "proxies":
            self.query_one(Proxies).test_node()
        elif self.current_page() == "overview":
            self.query_one(RouteControl).test_node()

    def action_cycle_mode(self) -> None:
        if self.current_page() in {"overview", "proxies"}:
            modes = ("rule", "global", "direct")
            current = self.state.configs.get("mode", "rule")
            index = modes.index(current) if current in modes else -1
            self.start_mutation("mode", (modes[(index + 1) % len(modes)],))

    def action_close_connection(self) -> None:
        if self.current_page() == "connections" and isinstance(self.focused, DataTable):
            self.query_one(Connections).close_selected()
        elif self.current_page() == "overview" and self.focused is self.query_one(
            "#overview-connections"
        ):
            self.query_one(Overview).close_selected()

    def action_pause_logs(self) -> None:
        if self.current_page() == "logs":
            self.query_one(Logs).toggle_pause()
        elif self.current_page() == "overview":
            self.query_one(Overview).toggle_logs()

    def action_help(self) -> None:
        self.push_screen(Information("键盘 / 鼠标操作", HELP))

    @on(Command)
    def command(self, event: Command) -> None:
        action, args = event.action, event.args
        if action == "page":
            self.action_page(args[0])
        elif action == "search":
            self.action_page(args[0])
            self.action_search()
        elif action == "refresh":
            self.action_refresh()
        elif action == "detail":
            connection = self.state.connections.get(args[0])
            if connection:
                self.push_screen(Information("连接详情", connection.raw))
        elif action in {"close", "close-all"}:
            if action == "close":
                connection = self.state.connections.get(args[0])
                if connection is None:
                    self.notify("该连接已结束")
                    return
                message = f"关闭连接 {connection.host}？\n这会中断此连接的数据传输。"
            else:
                message = (
                    f"关闭全部 {len(self.state.connections)} 条连接？\n"
                    "此操作会影响所有应用，不仅仅是搜索结果中的连接。"
                )
            self.push_screen(
                Confirm(message),
                lambda confirmed: self.start_mutation(action, args) if confirmed else None,
            )
        else:
            self.start_mutation(action, args, changes=event.changes)

    def start_mutation(
        self,
        action: str,
        args: tuple[str, ...],
        *,
        changes: dict[str, str | int | bool] | None = None,
    ) -> None:
        if self._mutation_busy:
            self.notify("已有操作进行中，请稍候", severity="warning")
            return
        if self.client is None:
            self.notify("控制器尚未连接", severity="warning")
            return
        self._mutation_busy = True
        self._activity = f"正在执行 {action}…"
        self._dirty.add("status")
        if action == "config":
            self._config_focus = self.focused
            self.screen_stack[0].query_one(KernelConfig).disabled = True
        self.mutate(action, args, changes=changes)

    @work(group="mutations", exit_on_error=False)
    async def mutate(
        self,
        action: str,
        args: tuple[str, ...],
        *,
        changes: dict[str, str | int | bool] | None = None,
    ) -> None:
        try:
            client = self.client
            if client is None:
                raise APIError("Controller is disconnected")
            if action == "select":
                group, node = args
                policy = self.state.groups.get(group, {})
                if str(policy.get("type", "")).lower() not in {"selector", "urltest", "fallback"}:
                    raise APIError("This group does not support manual selection")
                if node not in policy.get("all", []):
                    raise APIError("The node is no longer in this group; refresh and try again")
                await client.select_proxy(group, node)
                message = f"已切换至 {node}（已有连接保持不变）"
            elif action == "unpin":
                await client.unpin_proxy(args[0])
                message = "已恢复自动选择"
            elif action == "delay":
                delay = await client.test_delay(args[0])
                self.state.delays[args[0]] = delay
                message = f"{args[0]}: {delay} ms"
            elif action == "mode":
                await client.set_mode(args[0])
                message = f"模式已更新为 {args[0]}"
            elif action == "config":
                await client.update_configs(changes or {})
                message = "全局配置已更新"
            elif action in {"close", "close-all"}:
                await client.close_connection(args[0] if action == "close" else None)
                message = "已请求关闭连接"
                # Use the authoritative snapshot, never optimistically delete a row.
                try:
                    self.state.update_connections(await client.request("GET", "/connections"))
                except (APIError, ValueError):
                    pass  # The next WS snapshot will reconcile the list.
                self._dirty.update(("connections", "overview"))
            else:
                raise APIError("Unknown operation")
            self.notify(display_text(message), title="完成", markup=False)
            try:
                await self.refresh_snapshot()
                if action == "config":
                    self.screen_stack[0].query_one(KernelConfig).restore()
            except APIError as exc:
                self.set_status("http", False, str(exc))
                self.notify("操作已提交；刷新暂时失败，等待重试", severity="warning")
                self._refresh_event.set()
        except APIError as exc:
            if action == "delay":
                self.state.delays[args[0]] = None
            self.notify(
                display_text(str(exc)), title="操作失败", severity="error", timeout=7, markup=False
            )
        finally:
            self._mutation_busy = False
            self._activity = ""
            if action == "config":
                self.screen_stack[0].query_one(KernelConfig).disabled = False
                if self.current_page() == "config" and len(self.screen_stack) == 1:
                    if self._config_focus is not None:
                        self._config_focus.focus()
                self._config_focus = None
            self._dirty.update(("overview", "proxies", "config", "status"))

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.state == WorkerState.ERROR:
            error = event.worker.error
            self.notify(f"Background task failed: {type(error).__name__}", severity="error")
            if event.worker.group == "network":
                for name in self.statuses:
                    self.set_status(name, False, "network task stopped; restart dashboard")
