"""Command-line entry point. No I/O occurs until main() is called."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .config import load_settings


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="m4o",
        description="Mihomo terminal dashboard · 键盘 / 鼠标操作的终端控制台",
        epilog="Configuration precedence: CLI > MIHOMO_URL/MIHOMO_SECRET > TOML > defaults.",
    )
    result.add_argument("--version", action="version", version=f"m4o {__version__}")
    result.add_argument(
        "--config", type=Path, help="TOML config (default: ~/.config/m4o/config.toml)"
    )
    result.add_argument("--url", "--controller", dest="url", help="Controller HTTP(S) URL")
    result.add_argument("--secret", help="API secret; prefer MIHOMO_SECRET to avoid shell history")
    result.add_argument("--proxy", help="Explicit HTTP(S) proxy; environment proxies are ignored")
    result.add_argument(
        "--insecure",
        dest="verify_tls",
        action="store_false",
        default=None,
        help="Disable TLS verification (unsafe; for debugging only)",
    )
    result.add_argument("--timeout", type=float, help="HTTP request timeout in seconds (1–120)")
    result.add_argument("--interval", type=int, help="Connection stream interval in ms (250–60000)")
    result.add_argument(
        "--refresh-interval", type=float, help="Config/proxy refresh seconds (1–300)"
    )
    result.add_argument("--max-logs", type=int, help="Maximum buffered log entries (100–100000)")
    result.add_argument("--test-url", help="Latency probe URL requested by mihomo, not this client")
    result.add_argument(
        "--demo", action="store_true", help="Run a local simulation; no real mihomo"
    )
    return result


def main(argv: list[str] | None = None) -> None:
    arguments = parser()
    values = vars(arguments.parse_args(argv))
    config = values.pop("config")
    demo = values.pop("demo")
    try:
        settings = load_settings(config, values)
    except ValueError as exc:
        arguments.error(str(exc))
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        arguments.error("An interactive terminal is required (do not pipe output). Try m4o --help.")
    # Keep --help/--version lightweight and usable without initializing a TUI.
    from .app import Dashboard

    Dashboard(settings, demo=demo).run()
