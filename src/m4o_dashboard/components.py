"""Small view helpers, with all controller-provided text rendered literally."""

from datetime import datetime

from rich.text import Text
from textual.containers import Vertical
from textual.widgets import Label, Select, Static

from .ui import display_text


def stat_card(title: str, identifier: str) -> Vertical:
    return Vertical(
        Label(title, classes="metric-label"),
        Static("—", id=identifier, classes="metric-value"),
        classes="stat-card",
    )


def literal(value: object, style: str = "") -> Text:
    return Text(display_text(str(value)), style=style)


def fields(values: list[tuple[str, object]]) -> Text:
    result = Text()
    for index, (label, value) in enumerate(values):
        if index:
            result.append("\n")
        result.append(f"{label}  ", style="#9ebbd3")
        result.append(display_text(str(value)))
    return result


def filter_options(selector: Select, values: list[str]) -> None:
    options = tuple(dict.fromkeys(values))
    if getattr(selector, "filter_values", None) == options:
        return
    selector.filter_values = options
    current = selector.value
    selector.set_options([("全部", "")] + [(literal(value), value) for value in options])
    selector.value = current if current in options else ""


def clock_label(value: object) -> str:
    text = str(value or "—")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return (
            parsed.astimezone().strftime("%H:%M:%S")
            if parsed.tzinfo
            else parsed.strftime("%H:%M:%S")
        )
    except ValueError:
        return text
