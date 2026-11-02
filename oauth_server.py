"""Endpoints HTTP de la liaison de compte Alexa (OAuth 2.0) et du webhook du skill.

À placer derrière un reverse proxy HTTPS (Caddy dans docker-compose). Le
processus n'écoute que sur loopback sauf configuration explicite.
"""

from __future__ import annotations

import base64
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping
from urllib.parse import parse_qs, unquote, urlencode, urlparse

from alexa_verify import AlexaRequestVerifier, AlexaVerificationError
from amazon_link import public_base_url
from application import (
    AccountStore,
    AlexaSkill,
    OAuthAccountLinking,
    SharedRateLimiter,
    read_body,
    trusted_client_address,
)
import ui

RATE_LIMITER = SharedRateLimiter()


def log_event(message: str) -> None:
    """Journal court sur stderr (docker compose logs oauth) : jamais de jeton ni de donnée scolaire."""
    print(f"[ec-to-alexa] {message}", file=sys.stderr, flush=True)

# Hôtes de redirection Account Linking d'Amazon (Europe, Amérique, Japon).
ALEXA_LINK_HOSTS = ("layla.amazon.com", "pitangui.amazon.com", "alexa.amazon.co.jp")
# Les requêtes Alexa font quelques kilo-octets ; on garde une marge confortable.
MAX_ALEXA_BODY = 131_072


def redirect_uris_from_env(env: Mapping[str, str]) -> list[str]:
    uris = [
        uri.strip()
        for uri in env.get("ALEXA_OAUTH_REDIRECT_URI", "").split(",")
        if uri.strip()
    ]
    vendor_id = env.get("ALEXA_VENDOR_ID", "").strip()
    if vendor_id:
        uris += [f"https://{host}/api/skill/link/{vendor_id}" for host in ALEXA_LINK_HOSTS]
    # Liaison lancée depuis le site : Amazon échange notre code avec cette URL.
    base = public_base_url(env)
    if base and env.get("ALEXA_APP_CLIENT_ID", "").strip():
        uris.append(base + "/amazon/callback")
    return list(dict.fromkeys(uris))


def oauth_from_env(store: AccountStore, env: Mapping[str, str]) -> OAuthAccountLinking | None:
    """Retourne None tant que la liaison de compte Alexa n'est pas configurée."""
    client_id = env.get("ALEXA_OAUTH_CLIENT_ID", "").strip()
    client_secret = env.get("ALEXA_OAUTH_CLIENT_SECRET", "").strip()
    uris = redirect_uris_from_env(env)
    if not client_id or not client_secret or not uris:
        return None
    return OAuthAccountLinking(store, client_id, uris, client_secret)


def verifier_from_env(env: Mapping[str, str]) -> AlexaRequestVerifier | None:
    """Retourne None tant que ALEXA_SKILL_ID n'est pas défini : /alexa reste fermé."""
    skill_id = env.get("ALEXA_SKILL_ID", "").strip()
    if not skill_id:
        return None
    unsigned = env.get("ECO_TO_ALEXA_ALLOW_UNSIGNED_LOCAL", "0") == "1"
    return AlexaRequestVerifier(skill_id, verify_signature=not unsigned)


def alexa_access_token(request: dict[str, Any]) -> str:
    """Alexa transmet le jeton OAuth dans le corps, jamais dans un en-tête."""
    system = (request.get("context") or {}).get("System") or {}
    token = (system.get("user") or {}).get("accessToken")
    if not token:
        token = ((request.get("session") or {}).get("user") or {}).get("accessToken")
    return str(token or "")


class OAuthHandler(BaseHTTPRequestHandler):
    oauth: OAuthAccountLinking | None = None
    skill: AlexaSkill
    verifier: AlexaRequestVerifier | None = None
    account_url: str = "/"

    # -- GET --------------------------------------------------------------
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/healthz":
            self._send_json(
                200,
                {
                    "status": "ok",
                    "oauth": self.oauth is not None,
                    "alexa": self.verifier is not None,
                },
            )
            return
        if parsed.path == ui.CSS_PATH:
            self._send(200, "text/css", ui.CSS.encode("utf-8"), cache=True)
            return
        if parsed.path != "/oauth/authorize":
            self._send_json(404, {"error": "not_found"})
            return
        if self.oauth is None:
            self._send_json(503, {"error": "oauth_not_configured"})
            return
        params = {key: values[0] for key, values in parse_qs(parsed.query).items()}
        if not self._valid_authorize_request(params):
            self._send_json(400, {"error": "invalid_request"})
            return
        self._send_html(200, self._login_page(params))

    # -- POST -------------------------------------------------------------
    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            if not RATE_LIMITER.allow((trusted_client_address(self),), limit=60):
                self._send_json(429, {"error": "temporarily_unavailable"})
                return
            if not RATE_LIMITER.allow(("global",), limit=1000):
                self._send_json(429, {"error": "temporarily_unavailable"})
                return
            # Le corps ne peut être lu qu'une fois : /alexa lit du JSON brut
            # (nécessaire à la signature), les routes OAuth un formulaire.
            if parsed.path == "/alexa":
                self._alexa()
            elif not parsed.path.startswith("/oauth/"):
                self._send_json(404, {"error": "not_found"})
            elif self.oauth is None:
                self._send_json(503, {"error": "oauth_not_configured"})
            elif parsed.path == "/oauth/authorize":
                self._authorize(self._form())
            elif parsed.path == "/oauth/token":
                self._token(self._form())
            elif parsed.path == "/oauth/revoke":
                self._revoke(self._form())
            else:
                self._send_json(404, {"error": "not_found"})
        except AlexaVerificationError as error:
            log_event(f"/alexa refusée : {error}")
            self._send_json(400, {"error": "invalid_alexa_request"})
        except PermissionError as error:
            log_event(f"{parsed.path} refusée : {type(error).__name__}")
            self._send_json(400, {"error": "invalid_grant"})
        except (KeyError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
            log_event(f"{parsed.path} requête invalide : {error}")
            self._send_json(400, {"error": "invalid_request"})
        except Exception as error:  # noqa: BLE001 - Alexa doit toujours recevoir une réponse
            log_event(f"{parsed.path} erreur interne : {type(error).__name__}: {error}")
            self._send_json(500, {"error": "server_error"})

    # -- OAuth ------------------------------------------------------------
    def _authorize(self, fields: dict[str, str]) -> None:
        assert self.oauth is not None
        params = {
            key: fields.get(key, "")
            for key in ("client_id", "redirect_uri", "state", "response_type", "scope")
        }
        if not self._valid_authorize_request(params):
            self._send_json(400, {"error": "invalid_request"})
            return
        account_key = fields.get("email", "").strip().lower() or "missing-account"
        if not RATE_LIMITER.allow((f"account:{account_key}",), limit=5):
            self._send_html(429, self._login_page(params, "Trop d'essais. Attends une minute."))
            return
        try:
            code = self.oauth.authorize(
                fields.get("email", ""), fields.get("password", ""), params["redirect_uri"]
            )
        except PermissionError:
            self._send_html(
                401, self._login_page(params, "E-mail ou mot de passe incorrect.")
            )
            return
        log_event("liaison Alexa : connexion acceptée, code envoyé à Amazon")
        query = urlencode({"code": code, "state": params["state"]})
        self.send_response(302)
        self.send_header("Location", f"{params['redirect_uri']}?{query}")
        self._security_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _token(self, fields: dict[str, str]) -> None:
        assert self.oauth is not None
        if fields.get("grant_type") != "authorization_code":
            self._send_json(400, {"error": "unsupported_grant_type"})
            return
        client_id, client_secret = self._client_credentials(fields)
        result = self.oauth.token(
            fields.get("code", ""),
            client_id,
            fields.get("redirect_uri", ""),
            client_secret,
        )
        user_id = self.oauth.store.user_for_token(result["access_token"], "alexa_tokens")
        self.oauth.store.audit(user_id, "alexa_linked")
        log_event(f"liaison Alexa enregistrée pour le compte interne {user_id}")
        self._send_json(200, result)

    def _client_credentials(self, fields: dict[str, str]) -> tuple[str, str]:
        """Identifiants client en HTTP Basic (défaut Alexa) ou dans le corps."""
        client_id = fields.get("client_id", "")
        client_secret = fields.get("client_secret", "")
        scheme, _, value = self.headers.get("Authorization", "").partition(" ")
        if scheme.lower() == "basic":
            try:
                decoded = base64.b64decode(value, validate=True).decode("utf-8")
            except (ValueError, UnicodeDecodeError) as error:
                raise ValueError("authentification client invalide") from error
            basic_id, separator, basic_secret = decoded.partition(":")
            if not separator:
                raise ValueError("authentification client invalide")
            client_id, client_secret = unquote(basic_id), unquote(basic_secret)
        return client_id, client_secret

    def _revoke(self, fields: dict[str, str]) -> None:
        assert self.oauth is not None
        token = fields.get("token", "")
        if not token:
            raise ValueError("token manquant")
        self.oauth.revoke(token)
        self._send_json(200, {})

    def _valid_authorize_request(self, params: dict[str, str]) -> bool:
        return (
            self.oauth is not None
            and params.get("response_type") in (None, "", "code")
            and params.get("client_id") == self.oauth.client_id
            and params.get("redirect_uri") in self.oauth.redirect_uris
            and bool(params.get("state"))
            and len(params.get("state", "")) <= 1024
        )

    # -- Webhook Alexa ----------------------------------------------------
    def _alexa(self) -> None:
        if self.verifier is None:
            self._send_json(503, {"error": "alexa_not_configured"})
            return
        body = read_body(self, "application/json", MAX_ALEXA_BODY)
        envelope = json.loads(body.decode("utf-8", "strict"))
        if not isinstance(envelope, dict):
            raise ValueError("corps Alexa invalide")
        self.verifier.verify(self.headers, body, envelope)
        token = alexa_access_token(envelope)
        request = envelope.get("request") or {}
        name = (request.get("intent") or {}).get("name") or request.get("type")
        log_event(f"/alexa acceptée : {name} ({'compte lié' if token else 'sans liaison'})")
        self._send_json(200, self.skill.handle(token or None, envelope))

    # -- Rendu ------------------------------------------------------------
    def _form(self) -> dict[str, str]:
        body = read_body(self, "application/x-www-form-urlencoded")
        return {
            key: values[0]
            for key, values in parse_qs(body.decode("utf-8", "strict")).items()
        }

    def _login_page(self, params: dict[str, str], error: str = "") -> bytes:
        hidden = "".join(
            f'<input type="hidden" name="{key}" value="{ui.esc(params.get(key, ""))}">'
            for key in ("client_id", "redirect_uri", "state", "response_type", "scope")
        )
        body = f"""
<header class="masthead">
  <p class="brand">EC to Alexa</p>
  <h1>Autoriser Alexa à lire ton carnet</h1>
  <p class="lede">Alexa pourra te lire ton emploi du temps, tes devoirs, tes notes,
  tes absences et tes messages. Elle ne pourra rien modifier.</p>
</header>
{ui.notice(error, "error") if error else ""}
<section class="panel">
  <h2>Connecte-toi avec ton compte EC to Alexa</h2>
  <form method="post" action="/oauth/authorize" class="stack">
    {hidden}
    <label>E-mail <input name="email" type="email" autocomplete="username" required></label>
    <label>Mot de passe <input name="password" type="password" autocomplete="current-password" required></label>
    <button type="submit">Autoriser Alexa</button>
  </form>
  <p class="aside">Pas encore de compte ? <a href="{ui.esc(self.account_url)}">Crée-le sur le site</a>,
  connecte École Directe, puis relance la liaison depuis l'application Alexa.</p>
</section>"""
        return ui.page("Lier Alexa", body).encode("utf-8")

    def _send_html(self, status: int, body: bytes) -> None:
        self._send(status, "text/html", body)

    def _send_json(self, status: int, payload: dict[str, object]) -> None:
        self._send(status, "application/json", json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def _send(self, status: int, content_type: str, body: bytes, cache: bool = False) -> None:
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self._security_headers(cache)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _security_headers(self, cache: bool = False) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        # Chrome applique form-action à la redirection qui suit le formulaire :
        # les hôtes Amazon doivent donc être autorisés pour terminer la liaison.
        form_action = " ".join(["'self'"] + [f"https://{host}" for host in ALEXA_LINK_HOSTS])
        self.send_header(
            "Content-Security-Policy",
            f"default-src 'none'; style-src 'self'; form-action {form_action}; "
            "frame-ancestors 'none'; base-uri 'none'",
        )
        self.send_header("Cache-Control", "public, max-age=3600" if cache else "no-store")

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> None:
    from ecole_directe import load_dotenv

    load_dotenv()
    secret_key = os.environ.get("ECO_TO_ALEXA_SECRET_KEY", "")
    if not secret_key:
        raise SystemExit("Configure ECO_TO_ALEXA_SECRET_KEY avec une clé Fernet.")
    store = AccountStore(
        os.getenv("ECO_TO_ALEXA_DB", "ec_to_alexa.sqlite3"), secret_key.encode()
    )
    OAuthHandler.oauth = oauth_from_env(store, os.environ)
    OAuthHandler.verifier = verifier_from_env(os.environ)
    OAuthHandler.skill = AlexaSkill.from_store(store)
    OAuthHandler.account_url = os.environ.get("ECO_TO_ALEXA_PUBLIC_URL", "").rstrip("/") + "/"
    host = os.environ.get("ECO_TO_ALEXA_HOST", "127.0.0.1")
    port = int(os.environ.get("ECO_TO_ALEXA_OAUTH_PORT", "8001"))
    server = ThreadingHTTPServer((host, port), OAuthHandler)
    print(f"Serveur OAuth/Alexa disponible sur http://{host}:{server.server_port}")
    if OAuthHandler.oauth is None:
        print(
            "Liaison de compte non configurée : renseignez ALEXA_OAUTH_CLIENT_ID, "
            "ALEXA_OAUTH_CLIENT_SECRET et ALEXA_VENDOR_ID (ou ALEXA_OAUTH_REDIRECT_URI)."
        )
    if OAuthHandler.verifier is None:
        print("Webhook /alexa fermé : renseignez ALEXA_SKILL_ID.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        store.close()


if __name__ == "__main__":
    main()
