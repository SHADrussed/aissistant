"""Run only on your personal device; credentials are read from a hidden prompt."""

import argparse
import getpass
import json
import re
import sys
import urllib.request
from urllib.parse import urlsplit


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Redirect refused")


def register(url, token, secret, confirm, opener=None):
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path != "/telegram/webhook"
        or parsed.port not in (None, 443)
    ):
        raise ValueError("Invalid webhook URL")
    if not re.fullmatch(r"[1-9][0-9]*:[A-Za-z0-9_-]+", token):
        raise ValueError("Invalid token format")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", secret):
        raise ValueError("Invalid webhook secret format")
    if confirm != "REGISTER AND DROP PENDING":
        raise ValueError("Confirmation required")
    payload = {
        "url": url,
        "secret_token": secret,
        "allowed_updates": ["business_connection", "business_message"],
        "drop_pending_updates": True,
        "max_connections": 1,
    }
    opener = opener or urllib.request.build_opener(NoRedirect())
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/setWebhook",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with opener.open(request, timeout=20) as result:
        data = json.load(result)
    if data.get("ok") is not True or data.get("result") is not True:
        raise ValueError("Registration failed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--personal-device", action="store_true", required=True)
    parser.parse_args()
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        print("Use an interactive terminal on your personal device.", file=sys.stderr)
        return 1
    try:
        url = input("Worker HTTPS URL ending /telegram/webhook: ").strip()
        token = getpass.getpass("New Business bot token (hidden): ").strip()
        secret = getpass.getpass("Webhook secret, same as Cloudflare (hidden): ").strip()
        print("This replaces this bot's webhook and discards ALL pending updates.")
        confirm = input("Type REGISTER AND DROP PENDING to proceed: ")
        register(url, token, secret, confirm)
    except (Exception, KeyboardInterrupt):
        # urllib exceptions may embed the bot token in their URL. Never print them.
        print("Registration not confirmed. Check settings privately; no details logged.")
        return 1
    print("Webhook registered. Real delivery still needs a controlled metadata test.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
