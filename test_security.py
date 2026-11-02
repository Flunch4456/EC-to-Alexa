import io
import os
import unittest
from unittest.mock import patch

from application import read_body
from oauth_server import OAuthHandler


class FakeHandler:
    def __init__(self, headers, body):
        self.headers = headers
        self.rfile = io.BytesIO(body)


class HttpInputTests(unittest.TestCase):
    def test_body_requires_exact_length_and_content_type(self):
        handler = FakeHandler(
            {"Content-Length": "3", "Content-Type": "application/json; charset=utf-8"},
            b"{}",
        )
        with self.assertRaises(ValueError):
            read_body(handler, "application/json")

    def test_alexa_rejects_wrong_skill_and_stale_request(self):
        handler = OAuthHandler.__new__(OAuthHandler)
        handler.headers = {
            "X-Alexa-Request-Skill-Id": "wrong",
            "X-Alexa-Request-Timestamp": "0",
        }
        with patch.dict(os.environ, {"ALEXA_SKILL_ID": "expected"}, clear=False):
            with self.assertRaises(PermissionError):
                handler._validate_alexa_headers(b"{}")

    def test_body_rejects_ambiguous_negative_and_oversized_lengths(self):
        for length in ("-1", "1, 1", "not-a-number", "32769"):
            handler = FakeHandler(
                {"Content-Length": length, "Content-Type": "application/json"},
                b"x",
            )
            with self.assertRaises(ValueError):
                read_body(handler, "application/json")


if __name__ == "__main__":
    unittest.main()
