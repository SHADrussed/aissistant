"""Offline smoke tests for an already-built local image, with disposable synthetic state."""

import argparse
import json
import subprocess
import time
import uuid

DOCKER = ["docker", "--host=unix:///var/run/docker.sock"]


def docker(*args, check=True):
    result = subprocess.run([*DOCKER, *args], capture_output=True, text=True, timeout=60)
    if check and result.returncode:
        raise RuntimeError(f"Docker smoke {args[0]} failed: {result.stderr[:400]}")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default="aissistant:railway-safe")
    image = parser.parse_args().image
    volume = "aissistant-smoke-" + uuid.uuid4().hex[:12]
    container = None
    docker("volume", "create", volume)
    inline = 'mode="synthetic"\npoll_seconds=15'
    base = [
        "run",
        "--network",
        "none",
        "-v",
        f"{volume}:/data",
        "-e",
        f"ASSISTANT_CONFIG_TOML={inline}",
    ]
    try:
        for expected in ("sent=5", "sent=0"):
            result = docker(*base, "--rm", image, "/app/.venv/bin/aissistant", "--once")
            assert expected in result.stderr
            assert "revision=0001_initial" in result.stderr
            assert "Синтетический клиент" not in result.stderr + result.stdout
        inspect_code = """
import importlib.util, os
from pathlib import Path
assert os.getuid() == 10001
assert not Path("/run/secrets/build_ca").exists()
assert not Path("/app/tools").exists()
assert importlib.util.find_spec("google_auth_oauthlib") is None
assert os.stat("/data/synthetic.db").st_mode & 0o777 == 0o600
assert not list(Path("/data").glob("*.session*"))
print("nonroot_secret_exclusion_and_permissions: ok")
"""
        result = docker(*base, "--rm", image, "/app/.venv/bin/python", "-c", inspect_code)
        print(result.stdout.strip())
        result = docker(
            *base,
            "--rm",
            "-e",
            "MIGRATION_DATABASE_URL=sqlite:////data/synthetic.db",
            image,
            "/app/.venv/bin/aissistant-db",
            "check",
        )
        assert json.loads(result.stdout)["revision"] == "0001_initial"
        # Notification probe is explicitly invoked; missing bot secrets fail safely offline.
        probe = docker(
            *base, "--rm", image, "/app/.venv/bin/aissistant", "--test-notification", check=False
        )
        assert probe.returncode == 1
        assert "test_notification_failed_check_bot_configuration" in probe.stderr
        assert "Traceback" not in probe.stderr
        container = docker(*base, "-d", image).stdout.strip()
        until = time.monotonic() + 20
        while time.monotonic() < until:
            logs = docker("logs", container)
            if "cycle_complete" in logs.stderr + logs.stdout:
                break
            time.sleep(0.25)
        else:
            raise AssertionError("Background worker never completed a cycle")
        second = docker(*base, "--rm", image, "/app/.venv/bin/aissistant", "--once", check=False)
        assert second.returncode == 1
        assert "startup_or_storage_failed" in second.stderr
        assert "Traceback" not in second.stderr
        docker("stop", "--time", "20", container)
        code = docker("inspect", "--format", "{{.State.ExitCode}}", container).stdout.strip()
        assert code == "0"
        final = docker(*base, "--rm", image, "/app/.venv/bin/aissistant", "--once")
        assert "sent=0" in final.stderr
        print("offline_cycles_migration_lock_and_SIGTERM: ok")
    finally:
        if container:
            docker("rm", "-f", container, check=False)
        docker("volume", "rm", volume, check=False)


if __name__ == "__main__":
    main()
