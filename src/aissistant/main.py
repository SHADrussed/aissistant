import argparse
import asyncio
import logging
import os
import signal

from .classifier import OpenAIClassifier, SyntheticClassifier
from .config import load
from .notifier import BotNotifier, SyntheticNotifier
from .sources import GmailSource, SyntheticSource, TelegramSource, scheduled
from .store import Store
from .worker import Worker


def setup_logging():
    # SDK failures may contain message bodies, bearer tokens or Bot API URLs.
    # Disable all third-party propagation, including loggers instantiated later.
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(logging.NullHandler())
    root.setLevel(logging.CRITICAL + 1)
    for name in list(logging.Logger.manager.loggerDict):
        if name != "aissistant":
            logging.getLogger(name).handlers.clear()
            logging.getLogger(name).addHandler(logging.NullHandler())
            logging.getLogger(name).propagate = False
    own = logging.getLogger("aissistant")
    own.handlers.clear()
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    own.addHandler(handler)
    own.propagate = False
    own.setLevel(logging.INFO)


def build(settings):
    settings.validate_live()
    if settings.mode == "synthetic":
        return Worker(
            settings,
            Store(settings.database_url(), worker_lock=True),
            [SyntheticSource()],
            SyntheticClassifier(),
            SyntheticNotifier(),
        )
    sources = []
    if settings.telegram.enabled:
        sources.append(TelegramSource(settings))
    if settings.gmail.enabled:
        sources.append(GmailSource(settings))
    return Worker(
        settings,
        Store(settings.database_url(), worker_lock=True),
        sources,
        OpenAIClassifier(settings.ai.model, settings.ai.max_output_tokens),
        BotNotifier(),
    )


async def run(settings, once, request_low=False):
    worker = build(settings)
    if request_low:
        try:
            return 0 if await worker.request_low() else 1
        finally:
            await worker.close()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stop.set)
    try:
        while not stop.is_set():
            if scheduled(settings):
                result = await worker.cycle()
                if once:
                    return 0 if result else 1
            elif once:
                logging.getLogger("aissistant").info("outside_schedule")
                return 2
            try:
                await asyncio.wait_for(stop.wait(), timeout=settings.poll_seconds)
            except TimeoutError:
                pass
        return 0
    finally:
        await worker.close()


async def test_notification():
    bot = BotNotifier()
    try:
        await bot.send(
            "Тест ИИ-секретаря: доставка уведомлений владельцу работает. "
            "Переписка не читалась, OpenAI не вызывался."
        )
    finally:
        await bot.close()


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description="Opt-in secretary worker")
    parser.add_argument("--config", default=os.getenv("ASSISTANT_CONFIG"))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true")
    mode.add_argument(
        "--request-low",
        action="store_true",
        help="Deliver stored low-priority summaries without polling sources",
    )
    mode.add_argument(
        "--test-notification",
        action="store_true",
        help="Send one fixed owner notification without loading worker configuration",
    )
    args = parser.parse_args()
    setup_logging()
    try:
        if args.test_notification:
            asyncio.run(test_notification())
            logging.getLogger("aissistant").info("test_notification_sent")
            result = 0
        else:
            result = asyncio.run(run(load(args.config), args.once, args.request_low))
    except Exception:
        event = (
            "test_notification_failed_check_bot_configuration"
            if args.test_notification
            else "startup_or_storage_failed_check_configuration"
        )
        logging.getLogger("aissistant").error(event)
        result = 1
    raise SystemExit(result)


if __name__ == "__main__":
    main()
