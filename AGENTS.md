# Repository Guidelines

## Project Structure & Module Organization

`src/m4o_dashboard/` contains the Python 3.11+ Textual application. `app.py` coordinates asynchronous tasks and state; `ui.py` defines views and dialogs; `dashboard.tcss` holds terminal styles. `api.py` implements HTTP/WebSocket communication, `models.py` normalizes data, `config.py` validates settings, `cli.py` provides entry points, and `demo.py` supplies the local simulator. Tests live in `tests/`, with shared fixtures in `conftest.py`. `.github/workflows/ci.yml` defines CI; `config.example.toml` documents configuration.

## Build, Test, and Development Commands

Use uv 0.12.23+ and the committed `uv.lock`:

- `uv sync --locked --group dev`: install runtime and development dependencies.
- `uv run --locked m4o --demo`: launch the interactive dashboard with simulated data.
- `uv run --locked ruff check .`: lint Python code and imports.
- `uv run --locked ruff format --check .`: check formatting; omit `--check` to apply it.
- `uv run --locked pytest -q`: run the default test suite.
- `uv build`: produce wheel and source distributions in `dist/`.

Manage dependencies with `uv add` and `uv remove`; commit `pyproject.toml` and `uv.lock` together. Do not edit the lockfile manually.

## Coding Style & Naming Conventions

Use four-space indentation, double-quoted strings, and a 100-character line limit. Follow Ruff's Python 3.11 rules and import ordering. Use `snake_case` for modules, functions, and variables, and `PascalCase` for classes. Follow existing type annotations and asynchronous patterns; keep API communication independent of Textual widgets.

## Testing Guidelines

Tests use pytest and pytest-asyncio with automatic asyncio mode. Name files `test_*.py` and functions `test_<behavior>`. Reuse local aiohttp fixtures and Textual's headless Pilot; terminal smoke tests require POSIX. Default tests need no external controller or public network.

For isolated kernel integration testing, install `mihomo` on PATH and run `M4O_TEST_MIHOMO=1 uv run --locked pytest -q tests/test_real_mihomo.py`. No numeric coverage threshold is configured. Add focused regression tests for behavior changes. CI checks Python 3.11–3.14.

## Commit & Pull Request Guidelines

History currently contains one `feat:` commit. Follow that prefix style with concise `type: summary` messages. PRs should describe the behavior change, link relevant issues, and report validation results. Include terminal screenshots for visible UI changes. Run the CI checks above before requesting review.

## Security & Configuration Tips

Configuration precedence is CLI > environment > TOML > defaults. Prefer `MIHOMO_SECRET` for credentials and keep local settings in ignored `config.local.toml`. Preserve secret redaction, TLS verification, and confirmation dialogs for closing connections.
