"""Explicit local YAML persistence, separate from controller runtime updates."""

from __future__ import annotations

import io
import os
import stat
import tempfile
from pathlib import Path

from ruamel.yaml import YAML


class KernelFileError(Exception):
    pass


class KernelFile:
    def __init__(self, path: str) -> None:
        self.path = Path(path).expanduser().resolve()
        self.original: bytes | None = None
        self.values: dict = {}

    def load(self) -> None:
        try:
            content = self.path.read_bytes()
            values = YAML().load(content.decode("utf-8"))
            if not isinstance(values, dict):
                raise KernelFileError("YAML 顶层必须是映射")
        except (OSError, UnicodeError) as exc:
            raise KernelFileError(f"无法读取配置文件 ({type(exc).__name__})") from None
        except Exception as exc:
            if isinstance(exc, KernelFileError):
                raise
            # YAML parser errors may contain credentials; never echo their text.
            raise KernelFileError("无法解析 YAML 配置文件") from None
        self.original = content
        self.values = values

    def save(self, changes: dict) -> None:
        if self.original is None:
            raise KernelFileError("配置文件尚未载入，无法保存")
        temporary = None
        try:
            if self.path.read_bytes() != self.original:
                raise KernelFileError("配置文件已被外部修改，请重新启动面板后再保存")
            yaml = YAML()
            yaml.preserve_quotes = True
            values = yaml.load(self.original.decode("utf-8"))
            for name, value in changes.items():
                values[name] = value
            output = io.StringIO()
            yaml.dump(values, output)
            content = output.getvalue().encode("utf-8")
            # A unique, private backup precedes every replace; never clobber a backup.
            backup_fd, _ = tempfile.mkstemp(
                prefix=self.path.name + ".", suffix=".bak", dir=self.path.parent
            )
            with os.fdopen(backup_fd, "wb") as backup:
                backup.write(self.original)
            fd, temporary = tempfile.mkstemp(prefix=".m4o-", suffix=".yaml", dir=self.path.parent)
            with os.fdopen(fd, "wb") as destination:
                os.fchmod(destination.fileno(), stat.S_IMODE(self.path.stat().st_mode))
                destination.write(content)
                destination.flush()
                os.fsync(destination.fileno())
            # Check again after serialization, before atomic replacement.
            if self.path.read_bytes() != self.original:
                raise KernelFileError("配置文件已被外部修改，保存已取消")
            os.replace(temporary, self.path)
            temporary = None
            self.original = content
            self.values = values
        except KernelFileError:
            raise
        except OSError as exc:
            raise KernelFileError(f"无法保存配置文件 ({type(exc).__name__})") from None
        finally:
            if temporary:
                Path(temporary).unlink(missing_ok=True)
