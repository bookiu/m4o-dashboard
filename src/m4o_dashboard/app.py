"""Application coordinator: async network tasks, shared state, and UI commands."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from dataclasses import replace

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import DataTable, Footer, Header, Input, Static, TabbedContent, TabPane
from textual.worker import Worker, WorkerState

from .api import APIError, MihomoClient
from .config import Settings
from .models import DashboardState, clean
from .ui import Command, Confirm, Connections, Information, Logs, Overview, Proxies

HELP = """M4O · Mihomo terminal dashboard

1 / 2 / 3 / 4    总览 / 代理 / 连接 / 日志
Tab / Shift+Tab 切换焦点
↑ ↓ / j k       表格导航；← → / 滚轮：滚动
Enter           打开组的节点列表 / 使用选中节点 / 查看连接详情
/               聚焦当前页面搜索框
Esc             离开搜索框 / 取消弹窗
r               刷新配置、版本、代理列表
t               代理页：测试选中节点延迟（非带宽测速）
Delete          连接页：确认后关闭选中连接
Space           日志页：暂停 / 恢复绘制（后台仍接收）
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
        Binding("1", "page('overview')", "总览"),
        Binding("2", "page('proxies')", "代理"),
        Binding("3", "page('connections')", "连接"),
        Binding("4", "page('logs')", "日志"),
        Binding("slash", "search", "搜索", key_display="/"),
        Binding("r", "refresh", "刷新"),
        Binding("t", "test_delay", show=False),
        Binding("delete", "close_connection", show=False),
        Binding("space", "pause_logs", show=False),
        Binding("escape", "escape_search", show=False),
        Binding("question_mark", "help", "帮助", key_display="?"),
        Binding("q", "quit", "退出"),
    ]

    def __init__(self, settings: Settings, *, demo: bool = False) -> None:
        super().__init__()
        self.settings = settings
        self.demo = demo
        self.state = DashboardState(max_logs=settings.max_logs)
        self.client: MihomoClient | None = None
        self.statuses = {
            name: (False, "connecting…") for name in ("http", "traffic", "connections", "logs")
        }
        self._dirty = {"overview", "proxies", "connections", "logs", "status"}
        self._refresh_event = asyncio.Event()
        self._refresh_lock = asyncio.Lock()
        self._mutation_busy = False
        self._activity = ""

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Connecting to mihomo…", id="network-status", markup=False)
        with TabbedContent(initial="overview", id="pages"):
            with TabPane("1 总览", id="overview"):
                yield Overview(id="overview-view")
            with TabPane("2 代理", id="proxies"):
                yield Proxies(self.state, id="proxies-view")
            with TabPane("3 连接", id="connections"):
                yield Connections(self.state, id="connections-view")
            with TabPane("4 日志", id="logs"):
                yield Logs(self.state, id="logs-view")
        yield Footer()

    def on_mount(self) -> None:
        self.theme = "textual-dark"
        self.set_interval(0.25, self.flush_ui)
        self.network()

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
            self._dirty.update(("overview", "proxies"))

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
                    self._dirty.add("logs")
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
        if "status" in self._dirty:
            line = Text(" DEMO  " if self.demo else " ", style="bold cyan")
            failed = []
            for name, (connected, detail) in self.statuses.items():
                line.append(
                    f"{'●' if connected else '○'} {name.upper()}  ",
                    style="green" if connected else "yellow",
                )
                if not connected:
                    failed.append(f"{name}: {detail}")
            if self._activity:
                line.append(f"  {self._activity}", style="cyan")
            if failed:
                line.append("\n " + clean(failed[0]) + " · 保留最后数据 / 自动重试", style="yellow")
            if not self.settings.verify_tls:
                line.append("\n TLS verification DISABLED", style="bold red")
            screen.query_one("#network-status", Static).update(line)
            self._dirty.discard("status")
        active = pages.active
        if active in self._dirty:
            if active == "overview":
                screen.query_one(Overview).render_state(self.state, self.settings.url)
            elif active == "proxies":
                screen.query_one(Proxies).render_state()
            elif active == "connections":
                screen.query_one(Connections).render_state()
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
            "connections": "#connections-table",
            "logs": "#log-output",
        }[page]
        self.query_one(target).focus()
        if page == "overview":
            self.query_one(Overview).scroll_home(animate=False)

    def action_search(self) -> None:
        page = self.current_page()
        if page == "overview":
            self.action_page("proxies")
            page = "proxies"
        identifier = {
            "proxies": "proxy-search",
            "connections": "connection-search",
            "logs": "log-search",
        }[page]
        self.query_one(f"#{identifier}", Input).focus()

    def action_escape_search(self) -> None:
        if isinstance(self.focused, Input):
            target = {
                "proxies": "#nodes",
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

    def action_close_connection(self) -> None:
        if self.current_page() == "connections" and isinstance(self.focused, DataTable):
            self.query_one(Connections).close_selected()

    def action_pause_logs(self) -> None:
        if self.current_page() == "logs":
            self.query_one(Logs).toggle_pause()

    def action_help(self) -> None:
        self.push_screen(Information("键盘 / 鼠标操作", HELP))

    @on(Command)
    def command(self, event: Command) -> None:
        action, args = event.action, event.args
        if action == "detail":
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
            self.start_mutation(action, args)

    def start_mutation(self, action: str, args: tuple[str, ...]) -> None:
        if self._mutation_busy:
            self.notify("已有操作进行中，请稍候", severity="warning")
            return
        if self.client is None:
            self.notify("控制器尚未连接", severity="warning")
            return
        self._mutation_busy = True
        self._activity = f"正在执行 {action}…"
        self._dirty.add("status")
        self.mutate(action, args)

    @work(group="mutations", exit_on_error=False)
    async def mutate(self, action: str, args: tuple[str, ...]) -> None:
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
            self.notify(Text(clean(message)), title="完成")
            try:
                await self.refresh_snapshot()
            except APIError as exc:
                self.set_status("http", False, str(exc))
                self.notify("操作已提交；刷新暂时失败，等待重试", severity="warning")
                self._refresh_event.set()
        except APIError as exc:
            if action == "delay":
                self.state.delays[args[0]] = None
            self.notify(Text(str(exc)), title="操作失败", severity="error", timeout=7)
        finally:
            self._mutation_busy = False
            self._activity = ""
            self._dirty.update(("proxies", "status"))

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.state == WorkerState.ERROR:
            error = event.worker.error
            self.notify(f"Background task failed: {type(error).__name__}", severity="error")
            if event.worker.group == "network":
                for name in self.statuses:
                    self.set_status(name, False, "network task stopped; restart dashboard")
