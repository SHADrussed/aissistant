import base64
import hashlib
import json
import logging
import os
import time
from datetime import UTC, datetime
from email.utils import parseaddr
from urllib.parse import quote

import requests
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

from .config import required
from .models import Message

READONLY = "https://www.googleapis.com/auth/gmail.readonly"


class SyntheticSource:
    async def poll(self, store, accept):
        for i, text in enumerate(
            [
                "Синтетический клиент: нужен заказ сайта, пришлите счёт.",
                "Тест: срочно согласовать сроки сегодня.",
                "Учёба: экзамен в пятницу.",
                "Семья: встреча в субботу.",
                "Тестовое объявление.",
            ]
        ):
            await accept(Message("synthetic", "fixtures-v1", str(i), text, ""))

    async def close(self):
        pass


class TelegramSource:
    def __init__(self, settings, client=None):
        # Guard before creating any Telegram client, also for direct adapter usage.
        settings.validate_live()
        if settings.mode != "live" or not settings.telegram.enabled:
            raise ValueError("Telegram disabled")
        self.hashes = json.loads(os.getenv("TELEGRAM_PEER_HASHES", "{}"))
        self.config = settings.telegram
        self.chats = tuple(dict.fromkeys(settings.telegram.allowed_chat_ids))
        token_id = required("TELEGRAM_BOT_TOKEN").split(":", 1)[0]
        self.bot_id = int(token_id) if token_id.isdecimal() else None
        if self.config.mode == "all" and self.bot_id is None:
            raise ValueError("Bot token must identify notification bot")
        self.types = {}
        self.limit = settings.telegram.batch_size
        self.max_text = settings.max_text_chars
        if client is None:
            from telethon import TelegramClient
            from telethon.sessions import StringSession

            client = TelegramClient(
                StringSession(required("TELEGRAM_SESSION")),
                int(required("TELEGRAM_API_ID")),
                required("TELEGRAM_API_HASH"),
                timeout=20,
                request_retries=2,
                connection_retries=2,
                flood_sleep_threshold=0,
                receive_updates=False,
            )
        self.client = client
        self.connected = False

    async def poll(self, store, accept):
        if not self.connected:
            await self.client.connect()
            if not await self.client.is_user_authorized():
                raise ValueError("Session not authorized; login forbidden in worker")
            self.connected = True
        from telethon.errors import FloodWaitError

        failed = False
        if self.config.mode == "all":
            retry_key = "telegram:discovery_retry"
            if float(store.cursor(retry_key) or "0") <= time.time():
                try:
                    await self._discover(store)
                except FloodWaitError as exc:
                    store.set_cursor(retry_key, str(time.time() + max(1, exc.seconds)))
                    failed = True
                except Exception:
                    failed = True
            cached = store.cached_peers()
            self.chats = tuple(int(p["key"]) for p in cached)
            for peer in cached:
                chat = int(peer["key"])
                self.hashes[str(chat)] = int(peer["access_hash"])
                self.types[chat] = peer["chat_type"]
                if store.cursor(f"telegram:{chat}") is None and int(peer["baseline"]) >= 0:
                    store.set_cursor(f"telegram:{chat}", peer["baseline"])
        # Failure in one chat must not starve all other allowed chats.
        for chat in self.chats:
            if chat == self.bot_id or chat in self.config.excluded_chat_ids:
                continue
            wait_key = f"telegram:retry:{chat}"
            if int(store.cursor(wait_key) or "0") > time.time():
                failed = True
                continue
            try:
                await self._chat(chat, store, accept)
            except FloodWaitError as exc:
                store.set_cursor(wait_key, str(int(time.time()) + max(1, exc.seconds)))
                failed = True
            except Exception:
                failed = True
        if failed:
            raise RuntimeError("Telegram chat poll failed")

    async def _discover(self, store):
        from telethon.utils import get_peer_id

        full = (
            time.time() - float(store.cursor("telegram:discovery_full") or "0")
            >= self.config.discovery_refresh_seconds
        )
        limit = self.config.discovery_max_dialogs if full else self.config.discovery_recent_limit
        # Metadata only. Bounded recent scan each poll, bounded full refresh once a day.
        async for dialog in self.client.iter_dialogs(limit=limit):
            entity = dialog.entity
            chat = get_peer_id(entity)
            if (
                getattr(entity, "is_self", False)
                or chat == self.bot_id
                or chat in self.config.excluded_chat_ids
            ):
                continue
            kind = (
                "bot"
                if getattr(entity, "bot", False)
                else "private"
                if dialog.is_user
                else "channel"
                if getattr(entity, "broadcast", False)
                else "supergroup"
                if getattr(entity, "megagroup", False)
                else "group"
            )
            if kind in self.config.excluded_chat_types:
                continue
            store.cache_peer(
                chat,
                getattr(entity, "access_hash", 0),
                kind,
                # Missing top-message metadata is not proof of an empty history.
                # Leave the cursor unset: _chat fetches only the latest ID and returns.
                getattr(getattr(dialog, "message", None), "id", -1),
            )
        if full:
            store.set_cursor("telegram:discovery_full", str(time.time()))

    async def _chat(self, chat, store, accept):
        key = f"telegram:{chat}"
        cursor = store.cursor(key)
        from telethon.tl.types import InputPeerChannel, InputPeerChat, InputPeerUser

        if chat > 0:
            entity = InputPeerUser(chat, self.hashes[str(chat)])
        elif chat <= -1000000000000:
            entity = InputPeerChannel(-chat - 1000000000000, self.hashes[str(chat)])
        else:
            entity = InputPeerChat(-chat)
        kind = self.types.get(chat)
        if kind is None:
            # Resolve only explicitly enabled peer metadata, never enumerate dialogs.
            info = await self.client.get_entity(entity)
            kind = (
                "bot"
                if getattr(info, "bot", False)
                else "private"
                if chat > 0
                else "channel"
                if getattr(info, "broadcast", False)
                else "supergroup"
                if getattr(info, "megagroup", False)
                else "group"
            )
            self.types[chat] = kind
        if kind in self.config.excluded_chat_types:
            return
        if cursor is None:
            latest = await self.client.get_messages(entity, limit=1)
            store.set_cursor(key, str(latest[0].id if latest else 0))
            return
        async for item in self.client.iter_messages(
            entity,
            min_id=int(cursor),
            reverse=True,
            limit=self.limit,
        ):
            if (
                not item.out
                and item.message
                and (self.bot_id is None or getattr(item, "sender_id", None) != self.bot_id)
            ):
                # t.me/c supports private supergroups/channels, not private user dialogs.
                link = (
                    f"https://t.me/c/{-chat - 1000000000000}/{item.id}"
                    if chat <= -1000000000000
                    else ""
                )
                await accept(
                    Message(
                        "telegram",
                        str(chat),
                        str(item.id),
                        item.message[: self.max_text],
                        link,
                        str(getattr(item, "sender_id", "") or ""),
                        kind,
                    )
                )
            store.set_cursor(key, str(item.id))

    async def close(self):
        await self.client.disconnect()


def plain_text(payload: dict, limit: int) -> str:
    """Only inline text/plain; no attachment fetches and no HTML rendering."""
    if payload.get("mimeType") == "text/plain":
        data = payload.get("body", {}).get("data", "")
        # Limit decode allocation; body returned by Google is already in memory.
        encoded = data[: ((limit * 4 + 2) // 3 + 3) * 4]
        return base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode(
            "utf-8",
            errors="replace",
        )[:limit]
    pieces = []
    for part in payload.get("parts", []):
        pieces.append(plain_text(part, limit))
        if sum(map(len, pieces)) >= limit:
            break
    return "\n".join(pieces)[:limit]


class GmailSource:
    def __init__(self, settings, session=None, credentials=None):
        settings.validate_live()
        if settings.mode != "live" or not settings.gmail.enabled:
            raise ValueError("Gmail disabled")
        self.labels = set(settings.gmail.label_ids)
        self.mode = settings.gmail.mode
        if self.mode == "all_mail":
            self.labels.discard("INBOX")
        else:
            self.labels.add("INBOX")
        self.max_text = settings.max_text_chars
        self.session = session or requests.Session()
        self.credentials = credentials or Credentials(
            token=None,
            refresh_token=required("GMAIL_REFRESH_TOKEN"),
            client_id=required("GMAIL_CLIENT_ID"),
            client_secret=required("GMAIL_CLIENT_SECRET"),
            token_uri="https://oauth2.googleapis.com/token",
            scopes=[READONLY],
        )
        self.checked_token = None

    def _headers(self):
        if not self.credentials.valid:

            class TimedRequest(Request):
                def __call__(self, *args, **kwargs):
                    kwargs["timeout"] = 20
                    return super().__call__(*args, **kwargs)

            self.credentials.refresh(TimedRequest(session=self.session))
        if self.checked_token != self.credentials.token:
            response = self.session.get(
                "https://oauth2.googleapis.com/tokeninfo",
                params={"access_token": self.credentials.token},
                timeout=20,
            )
            response.raise_for_status()
            if set(response.json().get("scope", "").split()) != {READONLY}:
                raise ValueError("Exactly gmail.readonly is required; reauthorize locally")
            self.checked_token = self.credentials.token
        return {"Authorization": f"Bearer {self.credentials.token}"}

    def _get(self, path, params=None):
        response = self.session.get(
            "https://gmail.googleapis.com/gmail/v1/users/me/" + path,
            headers=self._headers(),
            params=params,
            timeout=25,
        )
        response.raise_for_status()
        return response.json()

    async def poll(self, store, accept):
        profile = self._get("profile")
        account = hashlib.sha256(profile["emailAddress"].encode()).hexdigest()
        saved_account = store.cursor("gmail:account")
        if saved_account is not None and saved_account != account:
            raise ValueError("Gmail account changed; use a separate database")
        if saved_account is None:
            store.set_cursor("gmail:account", account)
        cursor = store.cursor("gmail:history")
        if cursor is None:
            store.set_cursor("gmail:history", str(profile["historyId"]))
            return
        token = None
        while True:
            params = {"startHistoryId": cursor, "historyTypes": "messageAdded", "maxResults": 100}
            if token:
                params["pageToken"] = token
            try:
                page = self._get("history", params)
            except requests.HTTPError as exc:
                if exc.response is not None and exc.response.status_code == 404:
                    logging.getLogger("aissistant").error(
                        "gmail_history_expired_manual_rebaseline_required"
                    )
                raise
            for event in page.get("history", []):
                for added in event.get("messagesAdded", []):
                    ref = added["message"]
                    identity = Message("gmail", "me", ref["id"], "", "")
                    if store.contains(identity):
                        continue
                    try:
                        data = self._get(
                            "messages/" + quote(ref["id"], safe=""), {"format": "full"}
                        )
                    except requests.HTTPError as exc:
                        if exc.response is not None and exc.response.status_code == 404:
                            store.save(identity, None)  # Deleted before retrieval.
                            continue
                        raise
                    labels = set(data.get("labelIds", []))
                    if not self.labels.issubset(labels) or labels.intersection(
                        {"SENT", "DRAFT", "SPAM", "TRASH"}
                    ):
                        store.save(identity, None)
                        continue
                    payload = data.get("payload", {})
                    subject = next(
                        (
                            h["value"]
                            for h in payload.get("headers", [])
                            if h["name"].lower() == "subject"
                        ),
                        "",
                    )
                    text = (subject[:500] + "\n" + plain_text(payload, self.max_text))[
                        : self.max_text
                    ]
                    link = "https://mail.google.com/mail/u/0/#all/" + quote(ref["id"], safe="")
                    sender = next(
                        (
                            h["value"]
                            for h in payload.get("headers", [])
                            if h["name"].lower() == "from"
                        ),
                        "",
                    )
                    await accept(
                        Message(
                            "gmail",
                            "me",
                            ref["id"],
                            text,
                            link,
                            parseaddr(sender)[1].casefold(),
                            "mail",
                        )
                    )
            token = page.get("nextPageToken")
            if not token:
                store.set_cursor("gmail:history", str(page["historyId"]))
                break

    async def close(self):
        self.session.close()


def scheduled(settings, now=None):
    from zoneinfo import ZoneInfo

    now = (now or datetime.now(UTC)).astimezone(ZoneInfo(settings.schedule.timezone))
    return (
        now.weekday() in settings.schedule.weekdays
        and settings.schedule.start_hour <= now.hour < settings.schedule.end_hour
    )
