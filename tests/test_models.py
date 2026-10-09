import pytest

from m4o_dashboard.models import DashboardState, LogEntry, clean, number, size


def snapshot(download, upload=0, identifier="id"):
    return {
        "connections": [
            {
                "id": identifier,
                "download": download,
                "upload": upload,
                "metadata": {"host": "example.org", "destinationPort": "443"},
            }
        ]
    }


def test_rates_use_elapsed_time_and_counter_reset():
    state = DashboardState()
    state.update_connections(snapshot(100, 50), now=10)
    assert state.connections["id"].down_rate == 0
    state.update_connections(snapshot(600, 250), now=12.5)
    entry = state.connections["id"]
    assert entry.down_rate == 200
    assert entry.up_rate == 80
    assert entry.host == "example.org:443"
    state.update_connections(snapshot(5, 2), now=13)
    assert state.connections["id"].down_rate == 0
    state.last_connection_time = None
    state.update_connections(snapshot(5000), now=15)
    assert state.connections["id"].down_rate == 0


def test_old_traffic_totals_null_connections_and_bounds():
    state = DashboardState()
    state.update_connections({"connections": None, "uploadTotal": 30, "downloadTotal": 99})
    state.update_traffic({"up": 1, "down": 2})
    assert not state.connections
    assert state.upload_total == 30
    assert state.download_total == 99
    for i in range(300):
        state.update_traffic({"up": i, "down": i * 2})
    assert len(state.up_history) == 180
    state.update_traffic({"up": 0, "down": 0, "upTotal": 1, "downTotal": 2})
    assert state.upload_total == 1  # Kernel restart can reset cumulative counters.
    with pytest.raises(ValueError):
        state.update_connections({"connections": "bad"})
    with pytest.raises(ValueError):
        state.update_traffic({})


def test_log_formats_filters_safety_and_bounded_buffer():
    standard = LogEntry.parse({"type": "warning", "payload": "host [red] secret\x1b[31m"}, "secret")
    assert "[red]" in standard.message  # Kept as literal text, never parsed as markup.
    assert "secret" not in standard.message
    assert "\x1b" not in standard.message
    assert standard.matches("HOST", "info")
    assert not standard.matches("host", "error")
    structured = LogEntry.parse(
        {"time": "10:00:00", "level": "error", "message": "oh no", "fields": [{"source": "proxy"}]}
    )
    assert structured.time == "10:00:00"
    assert "proxy" in structured.message
    state = DashboardState(max_logs=3)
    for i in range(10):
        state.add_log({"payload": str(i)})
    assert [entry.message for entry in state.logs] == ["7", "8", "9"]
    assert state.logs_seen == 10
    assert clean("\x07test\x1b") == "test"


def test_size_and_defensive_numbers():
    assert size(1024) == "1.0 KiB"
    assert size(1024 * 1024, rate=True) == "1.0 MiB/s"
    for value in (None, "NaN", float("nan"), float("inf"), -1, True, 10**1000):
        assert number(value) == 0


def test_group_detection_and_delays():
    state = DashboardState()
    state.update_snapshot(
        {"version": "v1"},
        {"mode": "rule"},
        {
            "group": {"type": "Selector", "all": ["a"]},
            "a": {"history": [{"delay": 42}]},
            "bad": None,
        },
    )
    assert list(state.groups) == ["group"]
    assert state.delay_label("a") == "42 ms"
    state.delays["a"] = None
    assert state.delay_label("a") == "timeout"
    assert state.delay_label("unknown") == "—"
