import importlib.util
import io
import json
import unittest
from pathlib import Path
from unittest.mock import Mock

spec = importlib.util.spec_from_file_location(
    "registration", Path(__file__).parents[1] / "scripts" / "register_webhook.py"
)
registration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(registration)


class RegistrationTests(unittest.TestCase):
    def test_register_only_subscribes_to_metadata_events(self):
        opener = Mock()
        opener.open.return_value = io.BytesIO(b'{"ok":true,"result":true}')
        registration.register(
            "https://bridge.example/telegram/webhook",
            "123:synthetic",
            "synthetic_secret",
            "REGISTER AND DROP PENDING",
            opener,
        )
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.telegram.org/bot123:synthetic/setWebhook")
        payload = json.loads(request.data)
        self.assertEqual(payload["allowed_updates"], ["business_connection", "business_message"])
        self.assertTrue(payload["drop_pending_updates"])
        self.assertEqual(payload["max_connections"], 1)

    def test_invalid_destination_and_missing_confirmation_make_no_request(self):
        opener = Mock()
        for url, confirmation in [
            ("http://bridge.example/telegram/webhook", "REGISTER AND DROP PENDING"),
            ("https://user:pass@bridge.example/telegram/webhook", "REGISTER AND DROP PENDING"),
            ("https://bridge.example/telegram/webhook?secret=bad", "REGISTER AND DROP PENDING"),
            ("https://bridge.example/telegram/webhook", ""),
        ]:
            with self.assertRaises(ValueError):
                registration.register(url, "123:synthetic", "secret", confirmation, opener)
        opener.open.assert_not_called()

    def test_redirects_refused(self):
        with self.assertRaises(ValueError):
            registration.NoRedirect().redirect_request(
                None, None, 302, "", {}, "https://evil.example"
            )


if __name__ == "__main__":
    unittest.main()
