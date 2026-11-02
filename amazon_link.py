"""Liaison Alexa démarrée depuis le site (« app-to-app account linking », variante web).

Déroulé :
1. Le site envoie l'utilisateur sur Login with Amazon avec l'« Alexa Client Id »
   de la skill (scope ``alexa::skills:account_linking``, PKCE S256).
2. Amazon revient sur ``/amazon/callback`` avec un code ; le serveur l'échange
   contre un jeton Amazon (``api.amazon.com/auth/o2/token``).
3. Le serveur crée son propre code OAuth pour le compte EC-to-Alexa, puis appelle
   l'API Skill Enablement (``POST /v1/users/~current/skills/{skillId}/enablement``).
   Amazon échange ce code sur ``/oauth/token`` : la skill est activée et liée,
   et la réponse donne l'identifiant Alexa (``amzn1.ask.account…``) du compte.

Références : developer.amazon.com/docs/alexa/account-linking/app-to-app-account-linking-starting-from-your-app.html
et /docs/alexa/smapi/skill-enablement.html.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

AUTHORIZE_URL = "https://www.amazon.com/ap/oa"
# Login with Amazon a un point de jeton par région ; l'API Alexa documente celui
# d'Amérique du Nord, les autres servent de repli.
TOKEN_URLS = (
    "https://api.amazon.com/auth/o2/token",
    "https://api.amazon.co.uk/auth/o2/token",
    "https://api.amazon.co.jp/auth/o2/token",
)
DISCOVERY_URL = "https://api.amazonalexa.com/v1/alexaApiEndpoint"
# Europe d'abord : les comptes Alexa français y sont enregistrés.
ALEXA_API_ENDPOINTS = (
    "https://api.eu.amazonalexa.com",
    "https://api.amazonalexa.com",
    "https://api.fe.amazonalexa.com",
)
SCOPE = "alexa::skills:account_linking"
TIMEOUT = 10

HttpCall = Callable[[str, str, Mapping[str, str], bytes | None], tuple[int, Any]]


class AmazonLinkError(RuntimeError):
    """Message destiné à l'utilisateur, sans jeton ni secret."""


def _http(method: str, url: str, headers: Mapping[str, str], body: bytes | None) -> tuple[int, Any]:
    request = Request(url, data=body, method=method, headers=dict(headers))
    try:
        with urlopen(request, timeout=TIMEOUT) as response:
            status, raw = response.status, response.read(65_536)
    except HTTPError as error:
        status, raw = error.code, error.read(65_536)
    except (URLError, TimeoutError, OSError) as error:
        raise AmazonLinkError("Amazon ne répond pas pour le moment. Réessaie dans un instant.") from error
    try:
        return status, json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        return status, {}


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=")
    return verifier, challenge.decode("ascii")


class AmazonSkillLinker:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        skill_id: str,
        redirect_uri: str,
        stage: str = "development",
        http: HttpCall = _http,
    ) -> None:
        if not (client_id and client_secret and skill_id) or not redirect_uri.startswith("https://"):
            raise ValueError("Configuration de la liaison Amazon incomplète.")
        self.client_id = client_id
        self.client_secret = client_secret
        self.skill_id = skill_id
        self.redirect_uri = redirect_uri
        self.stage = stage
        self._http = http

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "AmazonSkillLinker | None":
        client_id = env.get("ALEXA_APP_CLIENT_ID", "").strip()
        client_secret = env.get("ALEXA_APP_CLIENT_SECRET", "").strip()
        skill_id = env.get("ALEXA_SKILL_ID", "").strip()
        base = public_base_url(env)
        if not (client_id and client_secret and skill_id and base):
            return None
        stage = env.get("ALEXA_SKILL_STAGE", "development").strip() or "development"
        return cls(client_id, client_secret, skill_id, base + "/amazon/callback", stage)

    # -- Étape 1 ----------------------------------------------------------
    def authorization_url(self, state: str, code_challenge: str) -> str:
        return AUTHORIZE_URL + "?" + urlencode(
            {
                "client_id": self.client_id,
                "scope": SCOPE,
                "response_type": "code",
                "redirect_uri": self.redirect_uri,
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            }
        )

    # -- Étape 2 ----------------------------------------------------------
    def exchange_code(self, code: str, code_verifier: str) -> str:
        body = urlencode(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.redirect_uri,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "code_verifier": code_verifier,
            }
        ).encode()
        headers = {"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"}
        last_error = ""
        for url in TOKEN_URLS:
            status, data = self._http("POST", url, headers, body)
            if status == 200 and isinstance(data, dict) and data.get("access_token"):
                return str(data["access_token"])
            last_error = str((data or {}).get("error_description") or (data or {}).get("error") or status)
        raise AmazonLinkError(f"Amazon a refusé la connexion ({last_error}). Recommence la liaison.")

    # -- Étape 3 ----------------------------------------------------------
    def _endpoints(self, token: str) -> list[str]:
        headers = {"Authorization": f"Bearer {token}"}
        try:
            status, data = self._http("GET", DISCOVERY_URL, headers, None)
        except AmazonLinkError:
            status, data = 0, {}
        found = []
        if status == 200 and isinstance(data, dict):
            for host in data.get("endpoints") or []:
                host = str(host).strip().rstrip("/")
                if host and not host.startswith("https://"):
                    host = "https://" + host
                if host in ALEXA_API_ENDPOINTS:
                    found.append(host)
        return found + [host for host in ALEXA_API_ENDPOINTS if host not in found]

    def enable_and_link(self, token: str, auth_code: str) -> dict[str, Any]:
        """Active la skill et lie le compte ; renvoie la réponse d'Amazon (201)."""
        body = json.dumps(
            {
                "stage": self.stage,
                "accountLinkRequest": {
                    "redirectUri": self.redirect_uri,
                    "authCode": auth_code,
                    "type": "AUTH_CODE",
                },
            }
        ).encode()
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        message = ""
        for endpoint in self._endpoints(token):
            url = f"{endpoint}/v1/users/~current/skills/{self.skill_id}/enablement"
            status, data = self._http("POST", url, headers, body)
            if status in (200, 201) and isinstance(data, dict):
                return data
            data = data if isinstance(data, dict) else {}
            if status == 400 and data.get("code") == "incorrectEndpoint":
                continue
            message = str(data.get("message") or data.get("code") or f"HTTP {status}")
            break
        raise AmazonLinkError(f"Amazon n'a pas pu activer la skill : {message or 'région introuvable'}.")


def public_base_url(env: Mapping[str, str]) -> str:
    """URL publique HTTPS du site, sans « / » final (ECO_TO_ALEXA_PUBLIC_URL ou DOMAIN)."""
    base = env.get("ECO_TO_ALEXA_PUBLIC_URL", "").strip().rstrip("/")
    if not base and env.get("DOMAIN", "").strip():
        base = "https://" + env["DOMAIN"].strip().rstrip("/")
    return base if base.startswith("https://") else ""
