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
        OpenAIClassifier(settings.ai.model),
        BotNotifier(),
    )


async def run(settings, once):
    worker = build(settings)
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


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description="Opt-in secretary worker")
    parser.add_argument("--config", default=os.getenv("ASSISTANT_CONFIG"))
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    setup_logging()
    try:
        result = asyncio.run(run(load(args.config), args.once))
    except Exception:
        logging.getLogger("aissistant").error("startup_or_storage_failed_check_configuration")
        result = 1
    raise SystemExit(result)


if __name__ == "__main__":
    main()
