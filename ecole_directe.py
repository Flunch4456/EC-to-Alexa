"""Client minimal pour l'API Ecole Directe."""

import json
import os
import sys
import base64
from http.client import RemoteDisconnected
from getpass import getpass
from http.cookies import SimpleCookie
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4


API_URL = "https://api.ecoledirecte.com/v3"
API_VERSION = "8.0.0"
SENSITIVE_RESPONSE_KEYS = {"token", "accessToken", "socketToken", "uid"}


def redact_response(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[masque]" if key in SENSITIVE_RESPONSE_KEYS else redact_response(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_response(item) for item in value]
    return value


def load_dotenv() -> None:
    """Charge les variables simples du fichier .env sans dependance externe."""
    env_file = ".env"
    if not os.path.exists(env_file):
        return
    with open(env_file, encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            name = name.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(name, value)


class EcoleDirecteError(RuntimeError):
    """Erreur renvoyee par l'API ou par la connexion."""


class EcoleDirecteClient:
    def __init__(self, identifiant: str, mot_de_passe: str) -> None:
        self.identifiant = identifiant
        self.mot_de_passe = mot_de_passe
        self.token: str | None = None
        self.student_id: str | None = None
        self.gtk: str | None = None
        self.qcm_token: str | None = None
        self.device_uuid = str(uuid4())

    def _get_gtk(self) -> None:
        request = Request(
            f"{API_URL}/login.awp?gtk=1&v={API_VERSION}",
            headers={"User-Agent": self._user_agent()},
        )
        try:
            with urlopen(request, timeout=20) as response:
                cookies = SimpleCookie(response.headers.get("Set-Cookie", ""))
        except (HTTPError, URLError, RemoteDisconnected, ConnectionResetError, TimeoutError) as error:
            raise EcoleDirecteError(f"Impossible d'obtenir le cookie GTK : {error}") from error

        gtk = cookies.get("GTK")
        if not gtk:
            raise EcoleDirecteError("Ecole Directe n'a pas fourni le cookie GTK requis.")
        self.gtk = gtk.value

    @staticmethod
    def _user_agent() -> str:
        return (
            "BlocksDirecte/1.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 "
            "EDMOBILE v8.0.0"
        )

    def _request(
        self,
        endpoint: str,
        payload: dict[str, Any],
        accepted_codes: tuple[int, ...] = (200,),
    ) -> dict[str, Any]:
        body = urlencode({"data": json.dumps(payload)}).encode("utf-8")
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
            "Origin": "https://www.ecoledirecte.com",
            "Referer": "https://www.ecoledirecte.com/",
            "X-Client": "web",
            "X-Requested-With": "XMLHttpRequest",
            "User-Agent": self._user_agent(),
        }
        if self.token:
            headers["X-Token"] = self.token
        if self.gtk:
            headers["X-Gtk"] = self.gtk
        if self.qcm_token:
            headers["2fa-token"] = self.qcm_token

        request = Request(f"{API_URL}/{endpoint.lstrip('/')}", data=body, headers=headers)
        try:
            with urlopen(request, timeout=20) as response:
                response_headers = response.headers
                result = json.load(response)
        except (HTTPError, URLError, RemoteDisconnected, ConnectionResetError, TimeoutError) as error:
            raise EcoleDirecteError(f"Connexion impossible a Ecole Directe : {error}") from error
        except json.JSONDecodeError as error:
            raise EcoleDirecteError("La reponse d'Ecole Directe n'est pas un JSON valide.") from error

        if result.get("token"):
            self.token = result["token"]
        if result.get("code") in (250, "250"):
            self.qcm_token = response_headers.get("2fa-token") or response_headers.get("x-token")

        if result.get("code") not in accepted_codes and result.get("code") not in tuple(
            str(code) for code in accepted_codes
        ):
            message = result.get("message") or result.get("title") or "Erreur inconnue"
            raise EcoleDirecteError(f"Ecole Directe a refuse la requete : {message}")
        return result

    def login(self, cn: str | None = None, cv: str | None = None) -> dict[str, Any]:
        self._get_gtk()
        payload: dict[str, Any] = {
            "identifiant": self.identifiant,
            "motdepasse": self.mot_de_passe,
            "sesouvenirdemoi": True,
            "isReLogin": False,
            "uuid": self.device_uuid,
        }
        if cn is not None and cv is not None:
            payload["cn"] = cn
            payload["cv"] = cv
        result = self._request(
            f"login.awp?v={API_VERSION}",
            payload,
            accepted_codes=(200, 250),
        )
        if result.get("token"):
            self.token = result["token"]
        self.student_id = self._find_student_id(result)
        if result.get("code") in (250, "250"):
            return result
        if not self.token:
            raise EcoleDirecteError("Connexion acceptee mais aucun jeton n'a ete fourni.")
        return result

    @staticmethod
    def _find_student_id(result: dict[str, Any]) -> str | None:
        data = result.get("data")
        if not isinstance(data, dict):
            return None
        accounts = data.get("accounts")
        if isinstance(accounts, list) and accounts:
            account = accounts[0]
            if isinstance(account, dict) and account.get("id") is not None:
                return str(account["id"])
        if data.get("id") is not None:
            return str(data["id"])
        return None

    def answer_qcm(self, choice: str) -> dict[str, Any]:
        if not self.qcm_token:
            raise EcoleDirecteError("Aucun token QCM n'est disponible.")
        return self._request("connexion/doubleauth.awp?verbe=post", {"choix": self._encode_qcm(choice)})

    def get_qcm(self) -> dict[str, Any]:
        if not self.qcm_token:
            raise EcoleDirecteError("Aucun token QCM n'est disponible.")
        result = self._request("connexion/doubleauth.awp?verbe=get", {})
        data = result.get("data") or {}
        result["data"] = {
            "question": self._decode_qcm(data.get("question", "")),
            "propositions": [self._decode_qcm(value) for value in data.get("propositions", [])],
        }
        return result

    @staticmethod
    def _encode_qcm(value: str) -> str:
        return base64.b64encode(value.encode("utf-8")).decode("ascii")

    @staticmethod
    def _decode_qcm(value: str) -> str:
        try:
            return base64.b64decode(value).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return value

    def complete_qcm(self, choice: str) -> dict[str, Any]:
        result = self.answer_qcm(choice)
        self.qcm_token = None
        data = result.get("data") or {}
        cn = data.get("cn")
        cv = data.get("cv")
        if not cn or not cv:
            raise EcoleDirecteError("La reponse QCM ne contient pas les valeurs cn/cv.")
        return self.login(cn=cn, cv=cv)

    def get(self, endpoint: str, **params: Any) -> dict[str, Any]:
        if not self.token:
            self.login()
        try:
            return self._request(endpoint, params)
        except EcoleDirecteError as error:
            if "token invalide" not in str(error).lower():
                raise
            self.token = None
            self.login()
            return self._request(endpoint, params)


def main() -> int:
    load_dotenv()
    identifiant = os.getenv("ECOLE_DIRECTE_IDENTIFIANT") or input("Identifiant Ecole Directe : ")
    mot_de_passe = os.getenv("ECOLE_DIRECTE_MOT_DE_PASSE") or getpass("Mot de passe : ")

    try:
        client = EcoleDirecteClient(identifiant, mot_de_passe)
        result = client.login()
        print(json.dumps(redact_response(result), ensure_ascii=False, indent=2))
    except EcoleDirecteError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())