"""UI-independent, bounded dashboard state and tolerant API normalization."""

from __future__ import annotations

import math
import re
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
LEVELS = {"debug": 0, "info": 1, "warning": 2, "error": 3, "silent": 4}


def clean(value: Any) -> str:
    """Treat controller strings as text, never Rich markup or terminal escapes."""
    return CONTROL.sub("", str(value))


def number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    try:
        result = float(value)
    except OverflowError:
        return 0.0
    return max(0.0, result) if math.isfinite(result) else 0.0


def size(value: Any, *, rate: bool = False) -> str:
    amount = number(value)
    unit = "B"
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if amount < 1024 or unit == "PiB":
            break
        amount /= 1024
    text = f"{amount:.0f}" if unit == "B" else f"{amount:.1f}"
    return f"{text} {unit}" + ("/s" if rate else "")


def mapping(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


@dataclass
class Connection:
    id: str
    raw: dict
    host: str
    process: str
    network: str
    source: str
    chains: str
    rule: str
    upload: float
    download: float
    up_rate: float = 0
    down_rate: float = 0

    @classmethod
    def parse(cls, raw: dict) -> Connection:
        meta = mapping(raw.get("metadata"))
        destination = meta.get("host") or meta.get("destinationIP") or "?"
        port = meta.get("destinationPort", "")
        chain = raw.get("chains")
        return cls(
            id=str(raw["id"]),
            raw=raw,
            host=f"{destination}:{port}" if port else str(destination),
            process=str(meta.get("process") or meta.get("processPath") or "—"),
            network=str(meta.get("network", "?")),
            source=f"{meta.get('sourceIP', '?')}:{meta.get('sourcePort', '')}",
            chains=" → ".join(str(item) for item in chain) if isinstance(chain, list) else "—",
            rule=f"{raw.get('rule', '')} {raw.get('rulePayload', '')}".strip(),
            upload=number(raw.get("upload")),
            download=number(raw.get("download")),
        )

    def matches(self, query: str) -> bool:
        return (
            query.casefold()
            in " ".join(
                (
                    self.id,
                    self.host,
                    self.process,
                    self.source,
                    self.chains,
                    self.rule,
                    self.network,
                )
            ).casefold()
        )


@dataclass(frozen=True)
class LogEntry:
    time: str
    level: str
    message: str

    @classmethod
    def parse(cls, raw: dict, secret: str = "") -> LogEntry:
        level = str(raw.get("level", raw.get("type", "info"))).lower()
        if level == "warn":
            level = "warning"
        message = str(raw.get("message", raw.get("payload", "")))
        if raw.get("fields"):
            message += " " + str(raw["fields"])
        if secret:
            message = message.replace(secret, "[redacted]")
        return cls(
            clean(raw.get("time") or datetime.now().strftime("%H:%M:%S")),
            clean(level),
            clean(message),
        )

    def matches(self, query: str, minimum: str) -> bool:
        return (
            LEVELS.get(self.level, 1) >= LEVELS.get(minimum, 0)
            and query.casefold() in self.message.casefold()
        )


@dataclass
class DashboardState:
    max_logs: int = 5000
    version: str = "—"
    configs: dict = field(default_factory=dict)
    proxies: dict[str, dict] = field(default_factory=dict)
    connections: dict[str, Connection] = field(default_factory=dict)
    up: float = 0
    down: float = 0
    upload_total: float | None = None
    download_total: float | None = None
    memory: float | None = None
    up_history: deque[float] = field(default_factory=lambda: deque([0], maxlen=180))
    down_history: deque[float] = field(default_factory=lambda: deque([0], maxlen=180))
    logs: deque[LogEntry] = field(init=False)
    logs_seen: int = 0
    last_connection_time: float | None = None
    delays: dict[str, int | None] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.logs = deque(maxlen=self.max_logs)

    def update_snapshot(self, version: dict, configs: dict, proxies: dict) -> None:
        self.version = str(version.get("version", "unknown"))
        self.configs = configs
        self.proxies = {name: value for name, value in proxies.items() if isinstance(value, dict)}

    @property
    def groups(self) -> dict[str, dict]:
        return {
            name: value
            for name, value in self.proxies.items()
            if isinstance(value.get("all"), list)
        }

    def update_traffic(self, value: dict) -> None:
        if "up" not in value or "down" not in value:
            raise ValueError("Missing traffic counters")
        self.up, self.down = number(value["up"]), number(value["down"])
        self.up_history.append(self.up)
        self.down_history.append(self.down)
        # Older kernels only have up/down; /connections supplies cumulative totals.
        if "upTotal" in value:
            self.upload_total = number(value["upTotal"])
        if "downTotal" in value:
            self.download_total = number(value["downTotal"])

    def update_connections(self, value: dict, now: float | None = None) -> None:
        if "connections" not in value or not isinstance(value["connections"], (list, type(None))):
            raise ValueError("Missing connection list")
        now = time.monotonic() if now is None else now
        elapsed = now - self.last_connection_time if self.last_connection_time is not None else 0
        updated = {}
        for raw in value["connections"] or []:
            if not isinstance(raw, dict) or not raw.get("id"):
                continue
            entry = Connection.parse(raw)
            old = self.connections.get(entry.id)
            if old and elapsed > 0:
                entry.up_rate = max(0, entry.upload - old.upload) / elapsed
                entry.down_rate = max(0, entry.download - old.download) / elapsed
            updated[entry.id] = entry
        self.connections = updated
        self.last_connection_time = now
        if "uploadTotal" in value:
            self.upload_total = number(value["uploadTotal"])
        if "downloadTotal" in value:
            self.download_total = number(value["downloadTotal"])
        if "memory" in value:
            self.memory = number(value["memory"])

    def add_log(self, value: dict, secret: str = "") -> None:
        self.logs.append(LogEntry.parse(value, secret))
        self.logs_seen += 1

    def delay_label(self, node: str) -> str:
        if node in self.delays:
            result = self.delays[node]
            return f"{result} ms" if result else "timeout"
        history = self.proxies.get(node, {}).get("history")
        if isinstance(history, list) and history and isinstance(history[-1], dict):
            result = number(history[-1].get("delay"))
            return f"{result:g} ms" if result else "timeout"
        return "—"
