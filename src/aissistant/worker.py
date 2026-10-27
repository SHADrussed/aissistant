import json
import logging
import time
from dataclasses import replace
from datetime import UTC, datetime
from decimal import ROUND_CEILING

from .models import Analysis, Category, Priority
from .privacy import disposition

log = logging.getLogger("aissistant")


class BudgetExhausted(RuntimeError):
    pass


def micro_cost(ai, input_tokens, output_tokens):
    return int(
        (
            input_tokens * ai.input_usd_per_million + output_tokens * ai.output_usd_per_million
        ).to_integral_value(rounding=ROUND_CEILING)
    )


class Worker:
    def __init__(self, settings, store, sources, classifier, notifier):
        self.settings = settings
        self.store = store
        self.sources = sources
        self.classifier = classifier
        self.notifier = notifier
        self._requests = 0
        self._notifications = 0
        self._sent = 0
        self._delivery_failed = False

    async def accept(self, message):
        if self.store.contains(message):
            return
        policy = disposition(message, self.settings)
        clean = replace(message, text="")
        if policy == "skip":
            self.store.save(clean, None)
            return
        if policy == "local_only":
            analysis = Analysis(
                categories=[Category.PERSONAL],
                priority=Priority.LOW,
                summary="Новое личное сообщение. Содержимое не передавалось ИИ.",
                recommendation="Открыть источник вручную.",
                draft="",
                agreement="",
            )
        else:
            text = message.text[: self.settings.max_text_chars]
            if getattr(self.classifier, "metered", False):
                day = datetime.now(UTC).date().isoformat()
                ai = self.settings.ai
                amount = micro_cost(ai, *self.classifier.reservation_tokens(text))
                if self._requests >= ai.max_requests_per_cycle or not self.store.reserve_api(
                    day, amount, ai.max_requests_per_day, int(ai.daily_budget_usd * 1_000_000)
                ):
                    log.info("api_budget_exhausted_deferred")
                    raise BudgetExhausted("API budget exhausted; cursor must remain unchanged")
                self._requests += 1
                try:
                    analysis = await self.classifier.analyze(text)
                finally:
                    usage = self.classifier.last_usage
                    if usage is not None:
                        self.store.record_api(day, *usage, micro_cost(ai, *usage))
            else:
                analysis = await self.classifier.analyze(text)
        if not set(analysis.categories).intersection(self.settings.notify_categories):
            analysis = None
        self.store.save(clean, analysis)
        if analysis is not None and analysis.priority == Priority.CRITICAL:
            await self.deliver([Priority.CRITICAL], digest=False)

    async def deliver(self, priorities, *, digest=True):
        if self._delivery_failed:
            return False
        config = self.settings.notifications
        rows = self.store.pending(priorities, config.digest_max_items if digest else 100)
        batches = []
        for row in rows:
            if (
                digest
                and batches
                and len((batches[-1][0] + "\n\n" + row["payload"]).encode("utf-16-le")) // 2 <= 3500
            ):
                batches[-1][0] += "\n\n" + row["payload"]
                batches[-1][1].append(row["key"])
            else:
                batches.append([row["payload"], [row["key"]]])
        for payload, keys in batches:
            now = time.time()
            recent = [
                t for t in json.loads(self.store.cursor("notify:recent") or "[]") if t > now - 3600
            ]
            if self._notifications >= config.max_per_cycle or len(recent) >= config.max_per_hour:
                log.info("notification_limit_deferred")
                return False
            self.store.check_worker_lock()
            try:
                await self.notifier.send(payload)
                # at-least-once: a process death after send can still cause a duplicate.
                for key in keys:
                    self.store.delivered(key)
                self.store.set_cursor("notify:recent", json.dumps([*recent, now]))
                self._notifications += 1
                self._sent += len(keys)
            except Exception:
                self._delivery_failed = True
                log.error("notification_failed")
                return False
        return True

    async def cycle(self):
        self.store.check_worker_lock()
        self._requests = self._notifications = self._sent = 0
        self._delivery_failed = False
        ok = True
        # Retry durable critical backlog before any new polling.
        await self.deliver([Priority.CRITICAL], digest=False)
        for source in self.sources:
            try:
                await source.poll(self.store, self.accept)
            except BudgetExhausted:
                ok = False
                break
            except Exception:
                log.error("source_poll_failed")
                ok = False
        due = float(self.store.cursor("notify:last_digest") or "0")
        if time.time() - due >= self.settings.notifications.digest_seconds:
            if await self.deliver([Priority.IMPORTANT]):
                self.store.set_cursor("notify:last_digest", str(time.time()))
        self.store.prune(self.settings.retention_days)
        ok = ok and not self._delivery_failed
        log.info("cycle_complete sent=%d success=%s", self._sent, ok)
        return ok

    async def request_low(self):
        self.store.check_worker_lock()
        self.store.prune(self.settings.retention_days)
        self._notifications = self._sent = 0
        self._delivery_failed = False
        return await self.deliver([Priority.LOW])

    async def close(self):
        for component in [*self.sources, self.classifier, self.notifier]:
            try:
                await component.close()
            except Exception:
                log.error("component_close_failed")
        self.store.close()
