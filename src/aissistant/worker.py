import logging
from dataclasses import replace

log = logging.getLogger("aissistant")


class Worker:
    def __init__(self, settings, store, sources, classifier, notifier):
        self.settings = settings
        self.store = store
        self.sources = sources
        self.classifier = classifier
        self.notifier = notifier

    async def accept(self, message):
        if self.store.contains(message):
            return
        text = message.text[: self.settings.max_text_chars]
        if any(k.casefold() in text.casefold() for k in self.settings.exclude_keywords if k):
            self.store.save(message, None)
            return
        analysis = await self.classifier.analyze(text)
        if not set(analysis.categories).intersection(self.settings.notify_categories):
            analysis = None
        # The store never receives original message text.
        self.store.save(replace(message, text=""), analysis)

    async def cycle(self):
        self.store.check_worker_lock()
        ok = True
        for source in self.sources:
            try:
                await source.poll(self.store, self.accept)
            except Exception:
                log.error("source_poll_failed")
                ok = False
        sent = 0
        for row in self.store.pending():
            self.store.check_worker_lock()
            try:
                await self.notifier.send(row["payload"])
                self.store.delivered(row["key"])
                sent += 1
            except Exception:
                log.error("notification_failed")
                ok = False
                break
        self.store.prune(self.settings.retention_days)
        log.info("cycle_complete sent=%d success=%s", sent, ok)
        return ok

    async def close(self):
        for component in [*self.sources, self.classifier, self.notifier]:
            try:
                await component.close()
            except Exception:
                log.error("component_close_failed")
        self.store.close()
