"""Local HTTP endpoints for Alexa Account Linking and the skill webhook.

Run behind HTTPS in production. The default process only exposes a loopback
listener and does not connect EcoleDirecte accounts automatically.
"""

from __future__ import annotations

import html
import json
import os
import time
import base64
import binascii
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from application import (
    AccountStore,
    AlexaSkill,
    OAuthAccountLinking,
    SharedRateLimiter,
    read_body,
    trusted_client_address,
)

RATE_LIMITER = SharedRateLimiter()


class OAuthHandler(BaseHTTPRequestHandler):
    oauth: OAuthAccountLinking
    skill: AlexaSkill

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/healthz":
            self._send_json(200, {"status": "ok"})
            return
        if parsed.path != "/oauth/authorize":
            self._send_json(404, {"error": "not_found"})
            return
        params = {key: values[0] for key, values in parse_qs(parsed.query).items()}
        if not self._valid_authorize_request(params):
            self._send_json(400, {"error": "invalid_request"})
            return
        self._send_html(self._login_page(params))

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            if not RATE_LIMITER.allow((trusted_client_address(self),)):
                self._send_json(429, {"error": "temporarily_unavailable"})
                return
            fields = self._form() if parsed.path != "/alexa" else {}
            if not RATE_LIMITER.allow(("global",), limit=1000):
                self._send_json(429, {"error": "temporarily_unavailable"})
                return
            if parsed.path == "/oauth/authorize":
                account_key = fields.get("email", "").strip().lower() or "missing-account"
                if not RATE_LIMITER.allow((f"account:{account_key}",), limit=5):
                    self._send_json(429, {"error": "temporarily_unavailable"})
                    return
            if parsed.path == "/oauth/authorize":
                self._authorize(fields)
            elif parsed.path == "/oauth/token":
                self._token(fields)
            elif parsed.path == "/oauth/revoke":
                self._revoke(fields)
            elif parsed.path == "/alexa":
                self._alexa()
            else:
                self._send_json(404, {"error": "not_found"})
        except PermissionError:
            self._send_json(400, {"error": "invalid_grant"})
        except (KeyError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(400, {"error": "invalid_request"})

    def _authorize(self, fields: dict[str, str]) -> None:
        params = {key: fields.get(key, "") for key in ("client_id", "redirect_uri", "state")}
        if not self._valid_authorize_request(params):
            self._send_json(400, {"error": "invalid_request"})
            return
        code = self.oauth.authorize(fields.get("email", ""), fields.get("password", ""))
        query = urlencode({"code": code, "state": params["state"]})
        self.send_response(302)
        self.send_header("Location", f"{params['redirect_uri']}?{query}")
        self.end_headers()

    def _token(self, fields: dict[str, str]) -> None:
        if fields.get("grant_type") != "authorization_code":
            raise ValueError("grant_type invalide")
        client_id = fields.get("client_id", "")
        client_secret = fields.get("client_secret", "")
        basic = self.headers.get("Authorization", "")
        if basic.lower().startswith("basic "):
            try:
                decoded = base64.b64decode(basic[6:], validate=True).decode("utf-8")
                client_id, client_secret = decoded.split(":", 1)
            except (ValueError, UnicodeDecodeError):
                raise ValueError("authentification client invalide")
        result = self.oauth.token(
            fields.get("code", ""),
            client_id,
            fields.get("redirect_uri", ""),
            client_secret,
        )
        self._send_json(200, result)

    def _revoke(self, fields: dict[str, str]) -> None:
        token = fields.get("token", "")
        if not token:
            raise ValueError("token manquant")
        self.oauth.revoke(token)
        self._send_json(200, {})

    def _alexa(self) -> None:
        body = read_body(self, "application/json")
        self._validate_alexa_headers(body)
        authorization = self.headers.get("Authorization", "")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            self._send_json(401, {"error": "invalid_token"})
            return
        request = json.loads(body.decode("utf-8", "strict"))
        self._send_json(200, self.skill.handle(token, request))

    def _validate_alexa_headers(self, body: bytes) -> None:
        skill_id = os.environ.get("ALEXA_SKILL_ID", "")
        if not skill_id:
            raise PermissionError("skill non configurée")
        request_skill = self.headers.get("X-Alexa-Request-Skill-Id", "")
        if request_skill != skill_id:
            raise PermissionError("skill invalide")
        timestamp = self.headers.get("X-Alexa-Request-Timestamp", "")
        try:
            if abs(time.time() - float(timestamp)) > 150:
                raise ValueError
        except ValueError as error:
            raise PermissionError("requête Alexa périmée") from error
        if os.environ.get("ECO_TO_ALEXA_ALLOW_UNSIGNED_LOCAL") != "1":
            signature = self.headers.get("Signature", "")
            cert_url = self.headers.get("SignatureCertChainUrl", "")
            if not signature or not cert_url:
                raise PermissionError("signature Alexa manquante")
            parsed = urlparse(cert_url)
            if parsed.scheme != "https" or parsed.hostname not in {"s3.amazonaws.com", "s3.eu-west-1.amazonaws.com"}:
                raise PermissionError("certificat Alexa non autorisé")
            try:
                request = Request(cert_url, headers={"Accept": "application/x-x509-ca-cert"})
                with urlopen(request, timeout=5) as response:
                    certificate = x509.load_pem_x509_certificate(response.read(16_384))
                now = datetime.now(timezone.utc)
                if not (certificate.not_valid_before_utc <= now <= certificate.not_valid_after_utc):
                    raise ValueError("certificat expiré")
                public_key = certificate.public_key()
                public_key.verify(
                    base64.b64decode(signature, validate=True),
                    body,
                    padding.PKCS1v15(),
                    hashes.SHA1(),
                )
            except (
                HTTPError,
                URLError,
                TimeoutError,
                OSError,
                ValueError,
                TypeError,
                binascii.Error,
                InvalidSignature,
            ) as error:
                raise PermissionError("signature Alexa invalide") from error

    def _valid_authorize_request(self, params: dict[str, str]) -> bool:
        return (
            params.get("response_type") in (None, "", "code")
            and params.get("client_id") == self.oauth.client_id
            and params.get("redirect_uri") == self.oauth.redirect_uri
            and bool(params.get("state"))
        )

    def _form(self) -> dict[str, str]:
        body = self.rfile.read(self._content_length()).decode("utf-8")
        return {key: values[0] for key, values in parse_qs(body).items()}

    def _login_page(self, params: dict[str, str]) -> bytes:
        hidden = "".join(
            f'<input type="hidden" name="{html.escape(key)}" value="{html.escape(params[key], quote=True)}">'
            for key in ("client_id", "redirect_uri", "state")
        )
        return (
            "<!doctype html><html lang='fr'><meta charset='utf-8'>"
            "<title>Lier Alexa</title><h1>Autoriser Alexa</h1>"
            "<p>Connectez-vous pour autoriser l'accès à vos données scolaires.</p>"
            "<form method='post' action='/oauth/authorize'>"
            f"{hidden}<label>E-mail <input name='email' type='email' required></label>"
            "<label>Mot de passe <input name='password' type='password' required></label>"
            "<button type='submit'>Autoriser</button></form>"
        ).encode("utf-8")

    def _send_html(self, body: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self._security_headers()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._security_headers()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; form-action 'self'")
        self.send_header("Cache-Control", "no-store")

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> None:
    secret_key = os.environ.get("ECO_TO_ALEXA_SECRET_KEY", "")
    if not secret_key:
        raise SystemExit("Configure ECO_TO_ALEXA_SECRET_KEY avec une clé Fernet.")
    store = AccountStore(
        os.getenv("ECO_TO_ALEXA_DB", "ec_to_alexa.sqlite3"), secret_key.encode()
    )
    client_id = os.environ.get("ALEXA_OAUTH_CLIENT_ID", "")
    redirect_uri = os.environ.get("ALEXA_OAUTH_REDIRECT_URI", "")
    client_secret = os.environ.get("ALEXA_OAUTH_CLIENT_SECRET", "")
    oauth = OAuthAccountLinking(store, client_id, redirect_uri, client_secret)
    OAuthHandler.oauth = oauth
    OAuthHandler.skill = AlexaSkill.from_store(store)
    host = os.environ.get("ECO_TO_ALEXA_HOST", "127.0.0.1")
    server = ThreadingHTTPServer((host, int(os.environ.get("ECO_TO_ALEXA_OAUTH_PORT", "8001"))), OAuthHandler)
    print(f"OAuth/Alexa server disponible sur http://{host}:{server.server_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        store.close()


if __name__ == "__main__":
    main()
