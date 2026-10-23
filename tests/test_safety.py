import ast
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from aissistant.notifier import BotNotifier

ROOT = Path(__file__).resolve().parents[1]


def local_helper():
    spec = importlib.util.spec_from_file_location(
        "local_authorize", ROOT / "tools/local_authorize.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "confirmed,environment,interactive",
    [
        (False, {}, True),
        (True, {"CODEX_HOME": "present"}, True),
        (True, {"RAILWAY_SERVICE_ID": "present"}, True),
        (True, {"CI": "true"}, True),
        (True, {}, False),
    ],
)
def test_authorization_refuses_managed_environments(confirmed, environment, interactive):
    with pytest.raises(RuntimeError):
        local_helper().require_personal_device(confirmed, environment, interactive)


def test_worker_has_no_personal_auth_or_reply_methods():
    forbidden = {
        "start",
        "qr_login",
        "sign_in",
        "send_code_request",
        "send_message",
        "send_file",
        "forward_messages",
        "edit_message",
        "delete_messages",
        "log_out",
        "to_json",
    }
    for path in (ROOT / "src/aissistant").glob("*.py"):
        tree = ast.parse(path.read_text())
        calls = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        assert not calls.intersection(forbidden), path.name
    source = (ROOT / "src/aissistant/sources.py").read_text()
    assert "receive_updates=False" in source
    assert 'StringSession(required("TELEGRAM_SESSION"))' in source
    assert "SQLiteSession" not in source


async def test_bot_method_and_recipient_guard(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_OWNER_CHAT_ID", "123")
    requests = []

    def reject(request):
        requests.append(request)
        raise AssertionError("Unexpected request")

    async with httpx.AsyncClient(transport=httpx.MockTransport(reject)) as client:
        bot = BotNotifier(client=client)
        for method, payload in [
            ("sendMessage", {"chat_id": 456}),
            ("sendDocument", {"chat_id": 123}),
        ]:
            with pytest.raises(ValueError, match="owner notifications"):
                await bot._call(method, payload)
    assert not requests


def test_startup_failure_never_logs_secret_input(tmp_path):
    canary = "SENSITIVE_CANARY_NOT_FOR_LOGS"
    env = {
        **os.environ,
        "OPENAI_API_KEY": canary,
        "TELEGRAM_SESSION": canary,
        "TELEGRAM_BOT_TOKEN": canary,
        "GMAIL_REFRESH_TOKEN": canary,
        "ASSISTANT_CONFIG_TOML": f'mode="{canary}"',
        "ASSISTANT_CONFIG": "",
    }
    # Avoid inherited explicit path: invalid inline config must fail before any API construction.
    env.pop("ASSISTANT_CONFIG")
    result = subprocess.run(
        [sys.executable, "-m", "aissistant.main", "--once"],
        env=env,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 1
    assert "startup_or_storage_failed" in result.stderr
    assert canary not in result.stderr + result.stdout
    assert "Traceback" not in result.stderr


async def test_real_telethon_string_session_is_memory_only(tmp_path, monkeypatch):
    from telethon.crypto import AuthKey
    from telethon.sessions import StringSession

    from aissistant.config import Settings
    from aissistant.sources import TelegramSource

    # Deliberately invalid all-zero auth material: no personal account and no connect().
    session = StringSession()
    session.set_dc(2, "149.154.167.51", 443)
    session.auth_key = AuthKey(bytes(256))
    values = {
        "ALLOW_LIVE_READS": "true",
        "ALLOW_AI_PROCESSING": "true",
        "TELEGRAM_API_ID": "1",
        "TELEGRAM_API_HASH": "0" * 32,
        "TELEGRAM_SESSION": session.save(),
        "TELEGRAM_BOT_TOKEN": "fake",
        "TELEGRAM_OWNER_CHAT_ID": "123",
        "OPENAI_API_KEY": "fake",
        "LIVE_DATABASE_URL": "sqlite:///:memory:",
        "TELEGRAM_PEER_HASHES": "{}",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    monkeypatch.chdir(tmp_path)
    reader = TelegramSource(
        Settings(mode="live", telegram={"enabled": True, "allowed_chat_ids": [-123]})
    )
    assert isinstance(reader.client.session, StringSession)
    assert reader.client._no_updates
    assert list(tmp_path.iterdir()) == []
    await reader.close()


def test_sdk_debug_and_http_failures_do_not_leak_secrets(tmp_path):
    canary = "SDK_SECRET_CANARY_NOT_FOR_LOGS"
    script = """
import asyncio, logging, os, socket
from pathlib import Path
import httpx
logging.getLogger('httpx').addHandler(logging.StreamHandler())
from aissistant.main import setup_logging
setup_logging()
from openai import AsyncOpenAI
from aissistant.classifier import OpenAIClassifier
from aissistant.config import Settings
from aissistant.models import Analysis, Message
from aissistant.notifier import BotNotifier
from aissistant.sources import SyntheticSource
from aissistant.store import Store
from aissistant.worker import Worker

def forbidden(*args, **kwargs):
    raise AssertionError('No actual network in this test')
socket.socket.connect = forbidden
canary = os.environ['OPENAI_API_KEY']
def response(request):
    return httpx.Response(500, json={'error': {'message': canary}})
async def test():
    classifier = OpenAIClassifier('gpt-4o-mini')
    await classifier.client.close()
    classifier.client = AsyncOpenAI(api_key=canary, max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(response)))
    bot = BotNotifier(client=httpx.AsyncClient(transport=httpx.MockTransport(response)))
    store = Store(os.environ['SYNTHETIC_DATABASE_URL'])
    store.save(Message('synthetic', 'logging', '1', '', ''), Analysis(
        categories=['другое'], summary='test', recommendation='test', draft='', agreement=''))
    worker = Worker(Settings(), store, [SyntheticSource()], classifier, bot)
    assert not await worker.cycle()
    logging.getLogger('urllib3.connectionpool').error(canary)
    await worker.close()
asyncio.run(test())
"""
    env = {
        **os.environ,
        "OPENAI_API_KEY": canary,
        "TELEGRAM_BOT_TOKEN": canary,
        "TELEGRAM_OWNER_CHAT_ID": "123",
        "OPENAI_LOG": "debug",
        "SYNTHETIC_DATABASE_URL": f"sqlite:///{tmp_path}/debug.db",
    }
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "source_poll_failed" in result.stderr
    assert "notification_failed" in result.stderr
    assert canary not in result.stderr + result.stdout
    assert "Traceback" not in result.stderr
