"""Local rejection before any model call. Heuristics are deliberately not a DLP guarantee."""

import re

PATTERNS = [
    r"(?is)(?:verification|confirmation|security|login|one.time|otp|код|подтвержден\w*)[^\n]{0,50}\b\d{4,8}\b",
    r"(?is)\b\d{4,8}\b[^\n]{0,30}(?:код|code|otp)\b",
    r"(?i)(?:password|passwd|парол\w*|api[_ -]?key|access[_ -]?token|client[_ -]?secret|refresh[_ -]?token)\s*[:=]\s*\S+",
    r"(?i)(?:password|passwd|парол[ьяи])\s+(?:(?:is|это)\s+)?\S+",
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{20,}|AKIA[A-Z0-9]{16})\b",
    r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b",
    r"\b\d{6,12}:[A-Za-z0-9_-]{25,}\b",
]
SENSITIVE = [re.compile(p) for p in PATTERNS]


def disposition(message, settings):
    privacy = settings.privacy
    if (
        message.scope in privacy.excluded_chats
        or f"{message.source}:{message.scope}" in privacy.excluded_chats
    ):
        return "skip"
    sender = message.sender.casefold()
    if sender and sender in {s.casefold() for s in privacy.excluded_senders}:
        return "skip"
    if any(k and k.casefold() in message.text.casefold() for k in settings.exclude_keywords):
        return "skip"
    if any(pattern.search(message.text) for pattern in SENSITIVE):
        return "skip"
    personal = message.source == "telegram" and message.chat_type in {"private", "bot", "unknown"}
    personal |= message.source == "gmail" and sender in {
        s.casefold() for s in privacy.personal_gmail_senders
    }
    return privacy.private_chat_policy if personal else "allow_ai"
