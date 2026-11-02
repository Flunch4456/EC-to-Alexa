"""Local HTTP endpoints for Alexa Account Linking and the skill webhook.

Run behind HTTPS in production. The default process only exposes a loopback
listener and does not connect EcoleDirecte accounts automatically.
"""

from __future__ import annotations

import html
import json
import os
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address, ip_network
from urllib.parse import parse_qs, urlencode, urlparse

from application import AccountStore, AlexaSkill, OAuthAccountLinking


MAX_HTTP_BODY = 32_768
MAX_CLOCK_SKEW_SECONDS = 150


class SharedRateLimiter:
    def __init__(self, max_keys: int = 4096) -> None:
        self.max_keys = max_keys
        self._lock = threading.Lock()
        self._attempts: dict[str, list[float]] = {}

    def allow(self, key: str, limit: int, window_seconds: int) -> bool:
        now = time.time()
        with self._lock:
            values = [stamp for stamp in self._attempts.get(key, []) if now - stamp < window_seconds]
            if len(values) >= limit:
                self._attempts[key] = values
                return False
            values.append(now)
            self._attempts[key] = values
            if len(self._attempts) > self.max_keys:
                oldest = sorted(
                    self._attempts.items(),
                    key=lambda item: item[1][-1] if item[1] else now,
                )[: len(self._attempts) - self.max_keys]
                for stale_key, _ in oldest:
                    self._attempts.pop(stale_key, None)
            return True


class OAuthHandler(BaseHTTPRequestHandler):
    oauth: OAuthAccountLinking
    skill: AlexaSkill
    limiter = SharedRateLimiter()

    def do_GET(self) -> None:
        self.connection.settimeout(10)
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
        self.connection.settimeout(10)
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/oauth/authorize":
                fields = self._form()
                self._authorize(fields)
            elif parsed.path == "/oauth/token":
                fields = self._form()
                self._token(fields)
            elif parsed.path == "/oauth/revoke":
                fields = self._form()
                self._revoke(fields)
            elif parsed.path == "/alexa":
                request = self._json()
                self._alexa(request)
            else:
                self._send_json(404, {"error": "not_found"})
        except PermissionError as error:
            self._send_json(400, {"error": "invalid_grant", "error_description": str(error)})
        except (KeyError, ValueError):
            self._send_json(400, {"error": "invalid_request"})

    def _authorize(self, fields: dict[str, str]) -> None:
        if not self._allow_auth_attempt(fields.get("email", "")):
            self._send_json(429, {"error": "rate_limited"})
            return
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
        if not self._allow_auth_attempt(fields.get("client_id", "")):
            self._send_json(429, {"error": "rate_limited"})
            return
        result = self.oauth.token(
            fields.get("code", ""),
            fields.get("client_id", ""),
            fields.get("client_secret", ""),
            fields.get("redirect_uri", ""),
            fields.get("grant_type", ""),
        )
        self._send_json(200, result)

    def _revoke(self, fields: dict[str, str]) -> None:
        token = fields.get("token", "")
        if not token:
            raise ValueError("token manquant")
        self.oauth.revoke(token, fields.get("client_id", ""), fields.get("client_secret", ""))
        self._send_json(200, {})

    def _alexa(self, request: dict[str, object]) -> None:
        if not self._is_alexa_request_verified():
            self._send_json(401, {"error": "invalid_signature"})
            return
        authorization = self.headers.get("Authorization", "")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            self._send_json(401, {"error": "invalid_token"})
            return
        self._validate_alexa_payload(request, token)
        self._send_json(200, self.skill.handle(token, request))

    def _valid_authorize_request(self, params: dict[str, str]) -> bool:
        return (
            params.get("response_type") in (None, "", "code")
            and params.get("client_id") == self.oauth.client_id
            and params.get("redirect_uri") == self.oauth.redirect_uri
            and bool(params.get("state"))
        )

    def _form(self) -> dict[str, str]:
        body = self._read_body("application/x-www-form-urlencoded").decode("utf-8")
        return {key: values[0] for key, values in parse_qs(body).items()}

    def _json(self) -> dict[str, object]:
        body = self._read_body("application/json")
        payload = json.loads(body.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("JSON invalide")
        return payload

    def _content_length(self) -> int:
        raw_values = self.headers.get_all("Content-Length", [])
        if len(raw_values) != 1:
            raise ValueError("Content-Length ambigu")
        raw = raw_values[0].strip()
        if not raw.isdigit():
            raise ValueError("Content-Length invalide")
        length = int(raw)
        if length < 0:
            raise ValueError("Content-Length invalide")
        if length > MAX_HTTP_BODY:
            raise ValueError("requête trop grande")
        return length

    def _read_body(self, expected_type: str) -> bytes:
        content_type = self.headers.get("Content-Type", "")
        media_type, _, charset_part = content_type.partition(";")
        if media_type.strip().lower() != expected_type:
            raise ValueError("Content-Type invalide")
        if charset_part:
            if "charset=" not in charset_part.lower():
                raise ValueError("charset invalide")
            charset = charset_part.split("=", 1)[1].strip().lower()
            if charset not in {"utf-8", '"utf-8"'}:
                raise ValueError("charset invalide")
        return self.rfile.read(self._content_length())

    def _allow_auth_attempt(self, account_key: str) -> bool:
        account_key = account_key.strip().lower()
        origin = self._request_origin()
        return (
            self.limiter.allow("global", limit=80, window_seconds=60)
            and self.limiter.allow(f"origin:{origin}", limit=20, window_seconds=60)
            and self.limiter.allow(f"account:{account_key or 'anon'}", limit=10, window_seconds=120)
        )

    def _request_origin(self) -> str:
        client_ip = self.client_address[0]
        trusted_proxies = self._trusted_proxy_networks()
        if any(ip_address(client_ip) in network for network in trusted_proxies) and self.headers.get("X-Forwarded-For"):
            return self.headers.get("X-Forwarded-For", "").split(",")[0].strip() or client_ip
        return client_ip

    @staticmethod
    def _trusted_proxy_networks() -> tuple[object, ...]:
        configured = os.environ.get("ECO_TO_ALEXA_TRUSTED_PROXIES", "127.0.0.1/32,::1/128")
        networks = []
        for value in configured.split(","):
            value = value.strip()
            if not value:
                continue
            networks.append(ip_network(value, strict=False))
        return tuple(networks)

    def _is_alexa_request_verified(self) -> bool:
        if os.environ.get("ALEXA_PROXY_SIGNATURE_VALIDATION", "0") != "1":
            return False
        if not any(
            ip_address(self.client_address[0]) in network
            for network in self._trusted_proxy_networks()
        ):
            return False
        return self.headers.get("X-Alexa-Signature-Verified", "") == "1"

    def _validate_alexa_payload(self, request: dict[str, object], bearer_token: str) -> None:
        skill_id = os.environ.get("ALEXA_SKILL_ID", "")
        if not skill_id:
            raise ValueError("ALEXA_SKILL_ID manquant")
        session = request.get("session")
        if isinstance(session, dict):
            application = session.get("application")
            if isinstance(application, dict) and application.get("applicationId") != skill_id:
                raise PermissionError("skill invalide")
            user = session.get("user")
            if isinstance(user, dict):
                user_token = user.get("accessToken")
                if user_token and not isinstance(user_token, str):
                    raise PermissionError("token invalide")
                if isinstance(user_token, str) and user_token and user_token != bearer_token:
                    raise PermissionError("token invalide")
        request_payload = request.get("request")
        if isinstance(request_payload, dict):
            timestamp = request_payload.get("timestamp")
            if not isinstance(timestamp, str):
                raise ValueError("timestamp manquant")
            parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            request_epoch = int(parsed.astimezone(timezone.utc).timestamp())
            if abs(int(time.time()) - request_epoch) > MAX_CLOCK_SKEW_SECONDS:
                raise PermissionError("requête expirée")

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
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
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
    client_secret = os.environ.get("ALEXA_OAUTH_CLIENT_SECRET", "")
    redirect_uri = os.environ.get("ALEXA_OAUTH_REDIRECT_URI", "")
    oauth = OAuthAccountLinking(store, client_id, redirect_uri, client_secret)
    OAuthHandler.oauth = oauth
    OAuthHandler.skill = AlexaSkill.from_store(store)
    bind_host = os.environ.get("ECO_TO_ALEXA_OAUTH_BIND", "127.0.0.1")
    server = ThreadingHTTPServer((bind_host, 8001), OAuthHandler)
    print("OAuth/Alexa server démarré.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        store.close()


if __name__ == "__main__":
    main()
