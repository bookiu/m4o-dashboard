"""Validated configuration. CLI > environment > TOML > defaults."""

from __future__ import annotations

import math
import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Settings:
    url: str = "http://127.0.0.1:9090"
    secret: str = field(default="", repr=False)
    proxy: str = ""
    verify_tls: bool = True
    timeout: float = 10.0
    interval: int = 1000
    refresh_interval: float = 5.0
    max_logs: int = 5000
    test_url: str = "https://www.gstatic.com/generate_204"

    def __post_init__(self) -> None:
        for name in ("url", "test_url", "proxy"):
            value = getattr(self, name)
            if name == "proxy" and value == "":
                continue
            if not isinstance(value, str):
                raise ValueError(f"{name} must be an HTTP(S) URL")
            try:
                parsed = urlsplit(value)
                _ = parsed.port
            except ValueError:
                raise ValueError(f"{name} must be a valid HTTP(S) URL") from None
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.fragment
                or any(char.isspace() or ord(char) < 32 for char in value)
                or (name != "test_url" and parsed.query)
            ):
                raise ValueError(f"{name} must be an HTTP(S) URL without credentials or fragment")
        if not isinstance(self.secret, str) or any(ord(c) < 32 for c in self.secret):
            raise ValueError("secret must be a string without control characters")
        if not isinstance(self.verify_tls, bool):
            raise ValueError("verify_tls must be a boolean")
        for name, low, high, integer in (
            ("timeout", 1, 120, False),
            ("interval", 250, 60000, True),
            ("refresh_interval", 1, 300, False),
            ("max_logs", 100, 100000, True),
        ):
            value = getattr(self, name)
            kind = int if integer else (int, float)
            if (
                isinstance(value, bool)
                or not isinstance(value, kind)
                or not math.isfinite(value)
                or not low <= value <= high
            ):
                raise ValueError(
                    f"{name} must be {'an integer' if integer else 'a number'} "
                    f"between {low} and {high}"
                )
        object.__setattr__(self, "url", self.url.rstrip("/"))


def default_config_path(environ: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    return Path(env.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "m4o" / "config.toml"


def load_settings(
    path: Path | None = None,
    overrides: Mapping[str, Any] | None = None,
    environ: Mapping[str, str] | None = None,
) -> Settings:
    env = os.environ if environ is None else environ
    config_path = path if path is not None else default_config_path(env)
    values: dict[str, Any] = {}
    if path is not None or config_path.exists():
        try:
            with config_path.open("rb") as file:
                values = tomllib.load(file)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            # Do not echo TOML contents: a malformed line may contain a secret.
            raise ValueError(
                f"Cannot read configuration {config_path}: {type(exc).__name__}"
            ) from None
        unknown = values.keys() - {item.name for item in fields(Settings)}
        if unknown:
            raise ValueError(f"Unknown configuration keys: {', '.join(sorted(unknown))}")
    for variable, key in (("MIHOMO_URL", "url"), ("MIHOMO_SECRET", "secret")):
        if variable in env:
            values[key] = env[variable]
    values.update({key: value for key, value in (overrides or {}).items() if value is not None})
    return Settings(**values)
