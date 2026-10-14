"""Time-based terminal traffic plots shared by overview and connections."""

import time

from rich.text import Text
from textual.widgets import Static

from .models import DashboardState, size
from .theme import CYAN, MUTED, PURPLE


class TrafficChart(Static):
    def __init__(self, state: DashboardState, *, connection: bool = False, **kwargs) -> None:
        self.state = state
        self.connection = connection
        self.samples = []
        self.frozen_at = None
        super().__init__(**kwargs)

    def render(self) -> Text:
        width = max(8, self.size.width - 13)
        height = max(2, self.size.height - 2)
        now = self.frozen_at or time.monotonic()
        history = self.samples if self.connection else self.state.traffic
        samples = [sample for sample in history if now - 60 <= sample.time <= now]
        peak = max((max(sample.up, sample.down) for sample in samples), default=0)
        scale = 1024.0
        while scale < peak:
            scale *= 2
        columns: dict[int, tuple[float, float]] = {}
        previous = None
        for sample in samples:
            x = min(width - 1, max(0, int((sample.time - now + 60) / 60 * (width - 1))))
            if previous is not None and sample.time - previous[1].time <= 3:
                for gap in range(previous[0] + 1, x):
                    columns[gap] = (previous[1].up, previous[1].down)
            columns[x] = (sample.up, sample.down)
            previous = (x, sample)
        output = Text(no_wrap=True, overflow="crop")
        for y in range(height):
            level = scale * (height - y) / height
            label = (
                size(scale, rate=True)
                if y == 0
                else size(scale / 2, rate=True)
                if y == height // 2
                else ""
            )
            output.append(f"{label:>11} │", style=MUTED)
            for x in range(width):
                up, down = columns.get(x, (0.0, 0.0))
                if up >= level or (up > 0 and y == height - 1):
                    output.append("⠿", style=PURPLE)
                elif down >= level or (down > 0 and y == height - 1):
                    output.append("⠿", style=CYAN)
                elif y in (0, height // 2) or x in (
                    0,
                    width // 4,
                    width // 2,
                    width * 3 // 4,
                    width - 1,
                ):
                    output.append("·", style="#29465e")
                else:
                    output.append(" ")
            output.append("\n")
        output.append("          0 └" + "─" * width + "\n", style=MUTED)
        labels = [" "] * width
        for position, label in (
            (0, "-60s"),
            (width // 4, "-45s"),
            (width // 2, "-30s"),
            (width * 3 // 4, "-15s"),
        ):
            if position + len(label) <= width - 4:
                labels[position : position + len(label)] = label
        output.append("             " + "".join(labels[:-4]) + "现在", style=MUTED)
        return output
