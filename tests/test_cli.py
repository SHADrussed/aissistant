import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_cli_synthetic_no_network_and_restart(tmp_path):
    # Socket monkeypatch in the child makes accidental external access fail the test.
    script = """
import socket

def forbidden(*args, **kwargs):
    raise AssertionError('Network access forbidden in synthetic mode')

socket.create_connection = forbidden
socket.socket.connect = forbidden
from aissistant.main import main
main()
"""
    process_env = {**os.environ, "SYNTHETIC_DATABASE_URL": f"sqlite:///{tmp_path}/cli.db"}
    for expected in ("sent=5", "sent=0"):
        result = subprocess.run(
            [sys.executable, "-c", script, "--once"],
            cwd=ROOT,
            env=process_env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert expected in result.stderr
        assert "Синтетический клиент" not in result.stderr + result.stdout
