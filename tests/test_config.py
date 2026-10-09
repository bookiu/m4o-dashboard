from pathlib import Path

import pytest

from m4o_dashboard.cli import main
from m4o_dashboard.config import Settings, default_config_path, load_settings


def test_defaults_and_secret_repr(tmp_path):
    settings = load_settings(environ={"XDG_CONFIG_HOME": str(tmp_path)})
    assert settings.url == "http://127.0.0.1:9090"
    assert settings.verify_tls
    assert "private-value" not in repr(Settings(secret="private-value"))
    assert default_config_path({"XDG_CONFIG_HOME": "/tmp/test"}) == Path(
        "/tmp/test/m4o/config.toml"
    )


def test_precedence_and_empty_secret_override(tmp_path):
    file = tmp_path / "config.toml"
    file.write_text('url = "http://localhost:9091"\nsecret = "file"\nmax_logs = 200\n')
    settings = load_settings(
        file,
        {"url": "https://localhost:9093/", "secret": ""},
        {
            "MIHOMO_URL": "http://localhost:9092",
            "MIHOMO_SECRET": "env",
        },
    )
    assert settings.url == "https://localhost:9093"
    assert settings.secret == ""
    assert settings.max_logs == 200
    assert load_settings(file, environ={"MIHOMO_SECRET": "env"}).secret == "env"


@pytest.mark.parametrize(
    "values",
    [
        {"url": "ftp://localhost"},
        {"url": "http://"},
        {"url": "http://localhost:bad"},
        {"url": "http://username:secret@host"},
        {"url": "http://host?secret=value"},
        {"url": "http://host/#fragment"},
        {"url": "http://bad host"},
        {"url": "http://host\n"},
        {"url": 123},
        {"interval": 0},
        {"interval": 1.5},
        {"interval": True},
        {"max_logs": 99},
        {"timeout": float("nan")},
        {"timeout": float("inf")},
        {"timeout": "bad"},
        {"refresh_interval": -1},
        {"verify_tls": "false"},
        {"secret": "unsafe\r\nheader"},
        {"proxy": "socks5://localhost:9"},
    ],
)
def test_validation(values):
    with pytest.raises(ValueError):
        Settings(**values)


def test_config_errors_do_not_echo_secrets(tmp_path):
    file = tmp_path / "bad.toml"
    file.write_text('secret = "DO-NOT-ECHO')
    with pytest.raises(ValueError) as error:
        load_settings(file, environ={})
    assert "DO-NOT-ECHO" not in str(error.value)
    file.write_text("typo = 10")
    with pytest.raises(ValueError, match="Unknown configuration"):
        load_settings(file, environ={})
    with pytest.raises(ValueError, match="Cannot read"):
        load_settings(tmp_path / "missing", environ={})


def test_help_and_version_without_terminal(capsys):
    with pytest.raises(SystemExit) as result:
        main(["--version"])
    assert result.value.code == 0
    assert "m4o 0.1.0" in capsys.readouterr().out
    with pytest.raises(SystemExit) as result:
        main(["--help"])
    assert result.value.code == 0
    assert "--demo" in capsys.readouterr().out


def test_cli_rejects_invalid_args(capsys):
    with pytest.raises(SystemExit) as result:
        main(["--url", "ftp://localhost"])
    assert result.value.code == 2
    assert "HTTP(S) URL" in capsys.readouterr().err
