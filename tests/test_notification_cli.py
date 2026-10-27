import json
import socket
import sys

import httpx
import pytest

from aissistant import main
from aissistant.notifier import BotNotifier


@pytest.mark.parametrize("outcome", ["ok", "group", "http_error"])
def test_notification_is_owner_only_and_independent(monkeypatch, capsys, outcome):
    canary = "SECRET_BOT_TOKEN_CANARY"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", canary)
    monkeypatch.setenv("TELEGRAM_OWNER_CHAT_ID", "123")
    monkeypatch.setenv("ALLOW_LIVE_READS", "false")
    monkeypatch.setenv("ALLOW_AI_PROCESSING", "false")
    monkeypatch.setenv("ASSISTANT_CONFIG_TOML", "invalid config must not be loaded")
    monkeypatch.setattr(sys, "argv", ["aissistant", "--test-notification"])

    def forbidden(*args, **kwargs):
        raise AssertionError("Worker or real network must not be used")

    monkeypatch.setattr(main, "build", forbidden)
    monkeypatch.setattr(main, "load", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    calls = []
    clients = []

    def handle(request):
        method = request.url.path.rsplit("/", 1)[-1]
        body = json.loads(request.content)
        calls.append((method, body))
        assert body["chat_id"] == 123
        if outcome == "http_error":
            return httpx.Response(500, text=canary)
        if method == "getChat":
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "result": {"id": 123, "type": "group" if outcome == "group" else "private"},
                },
            )
        assert method == "sendMessage"
        assert body["text"] == (
            "Тест ИИ-секретаря: доставка уведомлений владельцу работает. "
            "Переписка не читалась, OpenAI не вызывался."
        )
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    def factory():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        clients.append(client)
        return BotNotifier(client=client)

    monkeypatch.setattr(main, "BotNotifier", factory)
    with pytest.raises(SystemExit) as result:
        main.main()
    assert result.value.code == (0 if outcome == "ok" else 1)
    assert [method for method, _ in calls] == (
        ["getChat", "sendMessage"] if outcome == "ok" else ["getChat"]
    )
    assert clients[0].is_closed
    output = capsys.readouterr()
    assert (
        "test_notification_sent" if outcome == "ok" else "test_notification_failed"
    ) in output.err
    assert canary not in output.out + output.err
    assert "Traceback" not in output.err


def test_notification_missing_token_fails_without_network(monkeypatch, capsys):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setattr(sys, "argv", ["aissistant", "--test-notification"])
    with pytest.raises(SystemExit) as result:
        main.main()
    assert result.value.code == 1
    assert "test_notification_failed" in capsys.readouterr().err
