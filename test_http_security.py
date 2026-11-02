import io
import os
import unittest

from account_server import AccountHandler
from oauth_server import OAuthHandler


class FakeHeaders(dict):
    def get_all(self, name, default=None):
        key = name
        if key in self:
            value = self[key]
            if isinstance(value, list):
                return value
            return [value]
        return default if default is not None else []


class OAuthHttpValidationTests(unittest.TestCase):
    def test_content_length_rejects_ambiguous_values(self):
        handler = OAuthHandler.__new__(OAuthHandler)
        handler.headers = FakeHeaders({"Content-Length": ["3", "4"]})
        with self.assertRaises(ValueError):
            handler._content_length()

    def test_alexa_request_is_rejected_without_proxy_validation_header(self):
        handler = OAuthHandler.__new__(OAuthHandler)
        handler.headers = FakeHeaders({})
        handler.client_address = ("127.0.0.1", 12345)
        os.environ["ALEXA_PROXY_SIGNATURE_VALIDATION"] = "1"
        self.assertFalse(handler._is_alexa_request_verified())


class AccountCsrfTests(unittest.TestCase):
    def test_csrf_validation_rejects_missing_token(self):
        handler = AccountHandler.__new__(AccountHandler)
        handler.csrf_secret = b"secret"
        handler.headers = FakeHeaders({"Cookie": "browser=abc"})
        with self.assertRaises(PermissionError):
            handler._validate_csrf({})

    def test_content_length_rejects_non_numeric(self):
        handler = AccountHandler.__new__(AccountHandler)
        handler.headers = FakeHeaders({"Content-Length": "abc"})
        with self.assertRaises(ValueError):
            handler._content_length()

    def test_form_content_type_is_required(self):
        handler = AccountHandler.__new__(AccountHandler)
        handler.headers = FakeHeaders(
            {"Content-Length": "3", "Content-Type": "application/json"}
        )
        handler.rfile = io.BytesIO(b"a=1")
        with self.assertRaises(ValueError):
            handler._fields()


if __name__ == "__main__":
    unittest.main()
