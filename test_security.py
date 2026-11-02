import io
import os
import unittest
from unittest.mock import patch

from application import read_body, trusted_client_address
from oauth_server import verifier_from_env


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

    def test_alexa_endpoint_stays_closed_without_skill_id(self):
        with patch.dict(os.environ, {"ALEXA_SKILL_ID": ""}, clear=False):
            self.assertIsNone(verifier_from_env(os.environ))
        with patch.dict(os.environ, {"ALEXA_SKILL_ID": "amzn1.ask.skill.x"}, clear=False):
            verifier = verifier_from_env(os.environ)
            self.assertIsNotNone(verifier)
            self.assertTrue(verifier.verify_signature)

    def test_trusted_proxies_accept_cidr_ranges(self):
        handler = FakeHandler({"X-Forwarded-For": "203.0.113.9, 172.18.0.2"}, b"")
        handler.client_address = ("172.18.0.5", 1234)
        with patch.dict(os.environ, {"ECO_TO_ALEXA_TRUSTED_PROXIES": "172.16.0.0/12"}):
            self.assertEqual(trusted_client_address(handler), "203.0.113.9")
        with patch.dict(os.environ, {"ECO_TO_ALEXA_TRUSTED_PROXIES": "10.0.0.1"}):
            self.assertEqual(trusted_client_address(handler), "172.18.0.5")

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
