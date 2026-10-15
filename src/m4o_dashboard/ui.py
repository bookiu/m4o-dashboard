"""Textual views. Messages describe intentions; the app owns all I/O."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Label, Static

from .models import clean


def display_text(value: str) -> str:
    """Prefer text presentation where terminals disagree on emoji sequence width."""
    return clean(value).replace("\ufe0f", "\ufe0e")


@dataclass(frozen=True)
class TableText:
    """Keep the original cell value for identity/sorting, normalize only its display."""

    plain: str
    row_key: str
    style: str = ""

    def __rich__(self) -> Text:
        return Text(display_text(self.plain), style=self.style)


class Command(Message):
    def __init__(
        self, action: str, *args: str, changes: dict[str, str | int | bool] | None = None
    ) -> None:
        self.action = action
        self.args = args
        self.changes = changes
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
        row = min(table.cursor_row, table.row_count - 1)
        return str(table.ordered_rows[row].key.value)
    return None


def sync_table(
    table: DataTable, rows: Iterable[tuple[str, tuple[str | Text, ...]]], *, row_height: int = 1
) -> None:
    """Patch keyed rows instead of rebuilding, preserving cursor and scroll.

    The first cell carries the raw row key independently of its displayed value.
    Sorting and selection never depend on truncated or normalized labels.
    """
    rows = list(rows)
    chosen = selected_key(table)
    wanted = {key for key, _ in rows}
    for key in list(table.rows):
        if key.value not in wanted:
            table.remove_row(key)
    columns = list(table.columns)
    for key, cells in rows:
        rendered = tuple(
            TableText(cell.plain, key, str(cell.style))
            if isinstance(cell, Text)
            else TableText(cell, key)
            for cell in cells
        )
        if key not in table.rows:
            table.add_row(*rendered, key=key, height=row_height)
        else:
            for column, old, new in zip(columns, table.get_row(key), rendered, strict=True):
                if old != new:
                    table.update_cell(key, column, new)
    if rows:
        rank = {key: index for index, (key, _) in enumerate(rows)}
        table.sort("identity", key=lambda cell: rank[cell.row_key])
        if chosen in wanted:
            table.move_cursor(row=table.get_row_index(chosen), animate=False, scroll=False)


def add_columns(table: DataTable, columns: list[tuple[str, int | None]]) -> None:
    for index, (label, width) in enumerate(columns):
        table.add_column(label, key="identity" if index == 0 else str(index), width=width)


class Confirm(ModalScreen[bool]):
    BINDINGS = [Binding("escape", "cancel", "取消")]

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__()

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog confirm-dialog"):
            yield Label("确认操作", classes="section-title")
            yield Static(Text(display_text(self.message)))
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
            yield Label(Text(display_text(self.heading)), classes="section-title")
            with VerticalScroll():
                yield Static(Text(display_text(self.content)))
            yield Button("关闭 (Esc)", id="dismiss", variant="primary")

    @on(Button.Pressed, "#dismiss")
    def action_close(self) -> None:
        self.dismiss(None)
