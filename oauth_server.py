"""Local HTTP endpoints for Alexa Account Linking and the skill webhook.

Run behind HTTPS in production. The default process only exposes a loopback
listener and does not connect EcoleDirecte accounts automatically.
"""

from __future__ import annotations

import html
import json
import os
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from application import AccountStore, AlexaSkill, OAuthAccountLinking

MAX_REQUEST_AGE = 150  # secondes, tolérance imposée par Amazon


def alexa_access_token(request: dict[str, Any]) -> str:
    system = request.get("context", {}).get("System", {})
    token = system.get("user", {}).get("accessToken")
    if not token:
        token = request.get("session", {}).get("user", {}).get("accessToken")
    return str(token or "")


def valid_alexa_envelope(
    request: dict[str, Any], skill_id: str, now: datetime | None = None
) -> bool:
    """Vérifie l'identifiant de skill et la fraîcheur de la requête."""
    if skill_id:
        application = (
            request.get("context", {}).get("System", {}).get("application")
            or request.get("session", {}).get("application")
            or {}
        )
        if application.get("applicationId") != skill_id:
            return False
    timestamp = str(request.get("request", {}).get("timestamp", ""))
    try:
        sent = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return False
    if sent.tzinfo is None:
        sent = sent.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return abs((now - sent).total_seconds()) <= MAX_REQUEST_AGE


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
            # Le corps ne peut être lu qu'une fois : /alexa lit du JSON, les
            # routes OAuth un formulaire.
            if parsed.path == "/alexa":
                self._alexa()
            elif parsed.path == "/oauth/authorize":
                self._authorize(self._form())
            elif parsed.path == "/oauth/token":
                self._token(self._form())
            elif parsed.path == "/oauth/revoke":
                self._revoke(self._form())
            else:
                self._send_json(404, {"error": "not_found"})
        except PermissionError as error:
            self._send_json(400, {"error": "invalid_grant", "error_description": str(error)})
        except (KeyError, ValueError):
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
        result = self.oauth.token(
            fields.get("code", ""),
            fields.get("client_id", ""),
            fields.get("redirect_uri", ""),
        )
        self._send_json(200, result)

    def _revoke(self, fields: dict[str, str]) -> None:
        token = fields.get("token", "")
        if not token:
            raise ValueError("token manquant")
        self.oauth.revoke(token)
        self._send_json(200, {})

    def _alexa(self) -> None:
        request = json.loads(self.rfile.read(self._content_length()))
        if not valid_alexa_envelope(request, os.environ.get("ALEXA_SKILL_ID", "")):
            self._send_json(403, {"error": "forbidden"})
            return
        # Alexa transmet le jeton OAuth dans le corps de la requête, pas dans
        # un en-tête Authorization.
        token = alexa_access_token(request)
        if not token:
            scheme, _, header_token = self.headers.get("Authorization", "").partition(" ")
            token = header_token if scheme.lower() == "bearer" else ""
        self._send_json(200, self.skill.handle(token or None, request))

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

    def _content_length(self) -> int:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 32_768:
            raise ValueError("requête trop grande")
        return length

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
    oauth = OAuthAccountLinking(store, client_id, redirect_uri)
    OAuthHandler.oauth = oauth
    OAuthHandler.skill = AlexaSkill.from_store(store)
    server = ThreadingHTTPServer(("0.0.0.0", 8001), OAuthHandler)
    print("OAuth/Alexa server disponible sur http://0.0.0.0:8001")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        store.close()


if __name__ == "__main__":
    main()
