"""Personal-device-only authorization. Never run in Codex, CI, or Railway.

Secrets are shown only in a local masked GUI, never stdout, argv, files or logs.
This file is deliberately excluded from the application Docker image.
"""

import argparse
import asyncio
import getpass
import json
import logging
import os
import sys

READONLY = "https://www.googleapis.com/auth/gmail.readonly"


def require_personal_device(confirmed, environment, interactive):
    managed = any(k.startswith(("CODEX_", "RAILWAY_")) for k in environment)
    if not confirmed or managed or environment.get("CI") or not interactive:
        raise RuntimeError("Use a normal interactive terminal on your personal device only")


def reveal_locally(root, values):
    import tkinter as tk
    from tkinter import ttk

    root.title("Локальная авторизация — не показывайте это окно другим")
    root.deiconify()
    copied = [None]

    def clear():
        try:
            if copied[0] is not None and root.clipboard_get() == copied[0]:
                root.clipboard_clear()
        except tk.TclError:
            pass
        copied[0] = None

    def copy(value):
        clear()
        root.clipboard_clear()
        root.clipboard_append(value)
        root.update_idletasks()
        copied[0] = value
        root.after(30000, clear)

    def close():
        clear()
        root.destroy()

    ttk.Label(
        root,
        text="Только для Railway Variables в вашем браузере.\n"
        "Отключите историю и синхронизацию буфера. Он очищается через 30 секунд.",
    ).grid(row=0, column=0, columnspan=3, padx=12, pady=12)
    for row, (name, value) in enumerate(values.items(), start=1):
        value = str(value)
        ttk.Label(root, text=name).grid(row=row, column=0, padx=8, pady=6, sticky="w")
        entry = ttk.Entry(root, show="*", width=45)
        entry.insert(0, value)
        entry.configure(state="readonly")
        entry.grid(row=row, column=1, padx=8)
        ttk.Button(root, text="Копировать", command=lambda v=value: copy(v)).grid(row=row, column=2)
    root.protocol("WM_DELETE_WINDOW", close)
    root.after(600000, close)
    root.mainloop()


async def telegram():
    from telethon import TelegramClient, utils
    from telethon.errors import SessionPasswordNeededError
    from telethon.sessions import StringSession
    from telethon.tl.types import InputPeerChannel, InputPeerChat, InputPeerUser

    api_id = int(getpass.getpass("TELEGRAM_API_ID (локально): "))
    api_hash = getpass.getpass("TELEGRAM_API_HASH (локально): ")
    client = TelegramClient(StringSession(), api_id, api_hash, receive_updates=False)
    await client.connect()
    try:
        phone = getpass.getpass("Номер телефона (только на этом устройстве): ")
        sent = await client.send_code_request(phone)
        code = getpass.getpass("Код Telegram (не передавайте в Codex): ")
        try:
            await client.sign_in(phone=phone, code=code, phone_code_hash=sent.phone_code_hash)
        except SessionPasswordNeededError:
            await client.sign_in(password=getpass.getpass("Локальный пароль 2FA: "))
        # No list_dialogs/get_messages: resolve only targets explicitly named by the owner.
        targets = input("Разрешённые @username или числовые chat IDs, через запятую: ")
        ids, hashes = [], {}
        for target in filter(None, (s.strip() for s in targets.split(","))):
            if target.lstrip("-").isdigit():
                chat_id = int(target)
                if chat_id == 0:
                    raise ValueError("Invalid ID")
                if chat_id > 0 or chat_id <= -1000000000000:
                    access_hash = int(getpass.getpass(f"Access hash выбранного ID {chat_id}: "))
                    peer = (
                        InputPeerUser(chat_id, access_hash)
                        if chat_id > 0
                        else InputPeerChannel(-chat_id - 1000000000000, access_hash)
                    )
                else:
                    peer = InputPeerChat(-chat_id)
            else:
                if not target.startswith("@"):
                    raise ValueError("Use explicit @username or numeric ID, not invite URLs")
                peer = await client.get_input_entity(target)
                chat_id = utils.get_peer_id(peer)
            ids.append(chat_id)
            if hasattr(peer, "access_hash"):
                hashes[str(chat_id)] = peer.access_hash
        if not ids:
            raise ValueError("At least one explicit chat is required")
        owner = await client.get_me()
        return {
            "TELEGRAM_API_ID": api_id,
            "TELEGRAM_API_HASH": api_hash,
            "TELEGRAM_SESSION": client.session.save(),
            "TELEGRAM_PEER_HASHES": json.dumps(hashes),
            "TELEGRAM_OWNER_CHAT_ID": owner.id,
            "telegram.allowed_chat_ids (TOML)": json.dumps(list(dict.fromkeys(ids))),
        }
    finally:
        await client.disconnect()


def gmail():
    from google_auth_oauthlib.flow import InstalledAppFlow

    client_id = getpass.getpass("Desktop OAuth CLIENT_ID (локально): ")
    client_secret = getpass.getpass("Desktop OAuth CLIENT_SECRET (локально): ")
    flow = InstalledAppFlow.from_client_config(
        {
            "installed": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost"],
            }
        },
        scopes=[READONLY],
    )
    credentials = flow.run_local_server(
        host="127.0.0.1",
        port=0,
        open_browser=True,
        authorization_prompt_message="",
        success_message="Авторизация завершена. Закройте вкладку.",
        timeout_seconds=180,
        access_type="offline",
        prompt="consent",
    )
    granted = credentials.granted_scopes or credentials.scopes
    if set(granted or []) != {READONLY} or not credentials.refresh_token:
        raise ValueError("Exactly gmail.readonly and an offline refresh token are required")
    return {
        "GMAIL_CLIENT_ID": client_id,
        "GMAIL_CLIENT_SECRET": client_secret,
        "GMAIL_REFRESH_TOKEN": credentials.refresh_token,
    }


def bot():
    import httpx

    token = getpass.getpass("Токен нового отдельного бота (локально): ")

    def get(method, **params):
        response = httpx.get(
            f"https://api.telegram.org/bot{token}/{method}", params=params, timeout=20
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise ValueError("Bot request failed")
        return data["result"]

    get("getMe")
    if get("getWebhookInfo").get("url"):
        raise ValueError("Use a dedicated bot without an existing webhook")
    updates = get("getUpdates", allowed_updates=json.dumps(["message"]), timeout=0)
    ids = {
        u["message"]["chat"]["id"]
        for u in updates
        if u.get("message", {}).get("text") == "/start"
        and u["message"]["chat"].get("type") == "private"
        and u["message"].get("from", {}).get("id") == u["message"]["chat"]["id"]
    }
    if len(ids) != 1:
        raise ValueError("Send /start from exactly one private owner to a new dedicated bot")
    return {"TELEGRAM_BOT_TOKEN": token, "TELEGRAM_OWNER_CHAT_ID": ids.pop()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("provider", choices=["telegram", "gmail", "bot"])
    parser.add_argument("--personal-device", action="store_true")
    args = parser.parse_args()
    root = None
    try:
        require_personal_device(
            args.personal_device, os.environ, sys.stdin.isatty() and sys.stdout.isatty()
        )
        logging.disable(logging.CRITICAL)
        import tkinter as tk

        root = tk.Tk()  # Fail before authorization if the local secret GUI is unavailable.
        root.withdraw()
        if args.provider == "telegram":
            values = asyncio.run(telegram())
        else:
            values = gmail() if args.provider == "gmail" else bot()
        reveal_locally(root, values)
    except (Exception, KeyboardInterrupt):
        if root is not None:
            root.destroy()
        print(
            "local_authorization_failed: проверьте локальную настройку; секреты не выводятся.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
