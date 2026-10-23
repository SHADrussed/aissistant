"""Run tests with a disposable PostgreSQL 17; never uses a personal database."""

import os
import secrets
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKER = ["docker", "--host=unix:///var/run/docker.sock"]


def docker(*args, check=True, environment=None):
    result = subprocess.run(
        [*DOCKER, *args], capture_output=True, text=True, env=environment, check=False, timeout=120
    )
    if check and result.returncode:
        raise RuntimeError(f"Disposable Docker {args[0]} failed: {result.stderr[:500]}")
    return result.stdout.strip()


def ready(container):
    until = time.monotonic() + 30
    while time.monotonic() < until:
        result = subprocess.run(
            [*DOCKER, "exec", container, "pg_isready", "-U", "aissistant_test"],
            capture_output=True,
            timeout=5,
        )
        if result.returncode == 0:
            return
        time.sleep(0.25)
    raise RuntimeError("Disposable PostgreSQL did not become ready")


def main():
    suffix = uuid.uuid4().hex[:12]
    network, name = f"aissistant-test-{suffix}", f"aissistant-pg-{suffix}"
    docker("network", "create", network)
    try:
        password = secrets.token_hex(24)
        docker(
            "run",
            "-d",
            "--name",
            name,
            "--network",
            network,
            "-p",
            "127.0.0.1::5432",
            "-e",
            "POSTGRES_PASSWORD",
            "-e",
            "POSTGRES_USER=aissistant_test",
            "-e",
            "POSTGRES_DB=aissistant_test",
            "postgres:17",
            environment={**os.environ, "POSTGRES_PASSWORD": password},
        )
        ready(name)
        port = docker(
            "inspect",
            "--format",
            '{{(index (index .NetworkSettings.Ports "5432/tcp") 0).HostPort}}',
            name,
        )
        url = f"postgresql+psycopg://aissistant_test:{password}@127.0.0.1:{port}/aissistant_test"
        ready(name)
        environment = {**os.environ, "TEST_POSTGRES_URL": url}
        suite = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=ROOT, env=environment)
        if suite.returncode:
            return suite.returncode
        # Separate processes plus server restart prove durable pending delivery.
        environment["SYNTHETIC_DATABASE_URL"] = url
        seed = """
import asyncio
from aissistant.config import Settings
from aissistant.main import build, setup_logging
setup_logging()
async def seed():
    worker = build(Settings())
    async def fail(text):
        raise ValueError("synthetic delivery failure")
    worker.notifier.send = fail
    assert not await worker.cycle()
    assert len(worker.store.pending()) == 5
    worker.store.set_cursor("restart:probe", "123")
    await worker.close()
asyncio.run(seed())
"""
        subprocess.run([sys.executable, "-c", seed], cwd=ROOT, env=environment, check=True)
        docker("restart", name)
        ready(name)
        # Docker can reassign an ephemeral published port on restart.
        port = docker(
            "inspect",
            "--format",
            '{{(index (index .NetworkSettings.Ports "5432/tcp") 0).HostPort}}',
            name,
        )
        environment["SYNTHETIC_DATABASE_URL"] = (
            f"postgresql+psycopg://aissistant_test:{password}@127.0.0.1:{port}/aissistant_test"
        )
        verify = """
import asyncio
from aissistant.config import Settings
from aissistant.main import build, setup_logging
setup_logging()
async def verify():
    worker = build(Settings())
    assert worker.store.cursor("restart:probe") == "123"
    assert len(worker.store.pending()) == 5
    assert await worker.cycle()
    assert worker.store.pending() == []
    assert await worker.cycle()  # No repeated summaries after replay.
    await worker.close()
asyncio.run(verify())
"""
        subprocess.run([sys.executable, "-c", verify], cwd=ROOT, env=environment, check=True)
        print("postgres_server_restart_and_pending_outbox: ok")
        return 0
    finally:
        docker("rm", "-fv", name, check=False)
        docker("network", "rm", network, check=False)


if __name__ == "__main__":
    raise SystemExit(main())
