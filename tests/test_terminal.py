"""Real POSIX pseudo-terminal smoke test, beyond the headless Pilot."""

import os
import select
import subprocess
import sys
import time

import pytest


@pytest.mark.skipif(os.name != "posix", reason="POSIX PTY only; Pilot tests are cross-platform")
def test_cli_in_real_terminal_restores_terminal_settings():
    import fcntl
    import pty
    import struct
    import termios

    master, slave = pty.openpty()
    original = termios.tcgetattr(slave)
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 36, 120, 0, 0))
    process = None
    output = bytearray()
    try:
        process = subprocess.Popen(
            [sys.executable, "-m", "m4o_dashboard", "--demo"],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            env={**os.environ, "TERM": "xterm-256color"},
            start_new_session=True,
        )
        started = time.monotonic()
        sent = False
        while process.poll() is None and time.monotonic() - started < 12:
            if not sent and time.monotonic() - started > 2:
                os.write(master, b"2341q")
                sent = True
            ready, _, _ = select.select([master], [], [], 0.1)
            if ready:
                output.extend(os.read(master, 65536))
        assert process.wait(timeout=2) == 0, output.decode(errors="replace")[-2000:]
        assert b"M4O" in output
        assert b"Traceback" not in output
        assert termios.tcgetattr(slave) == original
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        os.close(master)
        os.close(slave)
