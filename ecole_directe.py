"""Client minimal pour l'API Ecole Directe."""

import json
import os
import sys
from http.client import RemoteDisconnected
from getpass import getpass
from http.cookies import SimpleCookie
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


API_URL = "https://api.ecoledirecte.com/v3"
API_VERSION = "4.75.0"


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
        self.gtk: str | None = None

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
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36"
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

        request = Request(f"{API_URL}/{endpoint.lstrip('/')}", data=body, headers=headers)
        try:
            with urlopen(request, timeout=20) as response:
                result = json.load(response)
        except (HTTPError, URLError, RemoteDisconnected, ConnectionResetError, TimeoutError) as error:
            raise EcoleDirecteError(f"Connexion impossible a Ecole Directe : {error}") from error
        except json.JSONDecodeError as error:
            raise EcoleDirecteError("La reponse d'Ecole Directe n'est pas un JSON valide.") from error

        if result.get("code") not in accepted_codes and result.get("code") not in tuple(
            str(code) for code in accepted_codes
        ):
            message = result.get("message") or result.get("title") or "Erreur inconnue"
            raise EcoleDirecteError(f"Ecole Directe a refuse la requete : {message}")
        return result

    def login(self, fa: list[dict[str, str]] | None = None) -> dict[str, Any]:
        self._get_gtk()
        payload: dict[str, Any] = {
            "identifiant": self.identifiant,
            "motdepasse": self.mot_de_passe,
            "isRelogin": False,
            "uuid": "",
        }
        if fa:
            payload["fa"] = fa
        result = self._request(
            f"login.awp?v={API_VERSION}",
            payload,
            accepted_codes=(200, 250),
        )
        if result.get("token"):
            self.token = result["token"]
        if result.get("code") in (250, "250"):
            return result
        if not self.token:
            raise EcoleDirecteError("Connexion acceptee mais aucun jeton n'a ete fourni.")
        return result

    def answer_qcm(self, choice: str) -> dict[str, Any]:
        if not self.token:
            raise EcoleDirecteError("Aucun token QCM n'est disponible.")
        return self._request("connexion/doubleauth.awp", {"choix": choice})

    def get_qcm(self) -> dict[str, Any]:
        if not self.token:
            raise EcoleDirecteError("Aucun token QCM n'est disponible.")
        return self._request("connexion/doubleauth.awp", {})

    def complete_qcm(self, choice: str) -> dict[str, Any]:
        result = self.answer_qcm(choice)
        data = result.get("data") or {}
        cn = data.get("cn")
        cv = data.get("cv")
        if not cn or not cv:
            raise EcoleDirecteError("La reponse QCM ne contient pas les valeurs cn/cv.")
        return self.login(fa=[{"cn": cn, "cv": cv}])

    def get(self, endpoint: str, **params: Any) -> dict[str, Any]:
        if not self.token:
            self.login()
        return self._request(endpoint, params)


def main() -> int:
    load_dotenv()
    identifiant = os.getenv("ECOLE_DIRECTE_IDENTIFIANT") or input("Identifiant Ecole Directe : ")
    mot_de_passe = os.getenv("ECOLE_DIRECTE_MOT_DE_PASSE") or getpass("Mot de passe : ")

    try:
        client = EcoleDirecteClient(identifiant, mot_de_passe)
        result = client.login()
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except EcoleDirecteError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())