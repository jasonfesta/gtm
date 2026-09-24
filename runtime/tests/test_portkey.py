import json
import tempfile
import unittest
from pathlib import Path

from crm import portkey


class Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


class PortkeyTests(unittest.TestCase):
    def test_ready_reports_presence_without_exposing_key(self):
        status = portkey.ready(env={"PORTKEY_API_KEY": "secret"}, credentials="/missing")
        self.assertTrue(status["ready"])
        self.assertNotIn("secret", json.dumps(status))
        self.assertEqual(status["model"], "gpt-4o-mini")

    def test_ready_can_use_shared_credentials_path(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "portkey.json"
            path.write_text('{"PORTKEY_API_KEY":"secret"}')
            status = portkey.ready(
                env={"PORTKEY_CREDENTIALS_PATH": str(path)}, credentials="/missing"
            )
        self.assertTrue(status["ready"])
        self.assertNotIn("secret", json.dumps(status))

    def test_complete_uses_fixed_model_and_json_mode(self):
        seen = {}

        def opener(request, timeout):
            seen["timeout"] = timeout
            seen["body"] = json.loads(request.data)
            return Response(
                {
                    "model": "gpt-4o-mini-2024-07-18",
                    "choices": [{"message": {"content": '{"text":"hello"}'}}],
                    "usage": {"total_tokens": 10},
                }
            )

        result = portkey.complete(
            "return json",
            env={"PORTKEY_API_KEY": "secret"},
            credentials="/missing",
            opener=opener,
        )
        self.assertEqual(result["text"], '{"text":"hello"}')
        self.assertEqual(seen["body"]["model"], "gpt-4o-mini")
        self.assertEqual(seen["body"]["response_format"], {"type": "json_object"})
        self.assertEqual(seen["timeout"], 30)

    def test_wrong_model_and_empty_key_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "API_KEY"):
            portkey.complete("prompt", env={}, credentials="/missing")

        def opener(*_args, **_kwargs):
            return Response(
                {
                    "model": "different-model",
                    "choices": [{"message": {"content": "{}"}}],
                }
            )

        with self.assertRaisesRegex(RuntimeError, "model mismatch"):
            portkey.complete(
                "prompt",
                env={"PORTKEY_API_KEY": "secret"},
                credentials="/missing",
                opener=opener,
            )


if __name__ == "__main__":
    unittest.main()
