"""Account and EcoleDirecte connection server for local/reverse-proxy use."""

from __future__ import annotations

import html
import os
import secrets
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from application import AccountStore
from ecole_directe import EcoleDirecteClient, EcoleDirecteError


class RateLimiter:
    def __init__(self) -> None:
        self.attempts: dict[str, list[float]] = {}

    def allow(self, key: str, limit: int = 10, window: int = 60) -> bool:
        now = time.time()
        values = [stamp for stamp in self.attempts.get(key, []) if now - stamp < window]
        if len(values) >= limit:
            self.attempts[key] = values
            return False
        values.append(now)
        self.attempts[key] = values
        return True


class AccountHandler(BaseHTTPRequestHandler):
    store: AccountStore
    csrf_tokens: dict[str, str] = {}
    limiter = RateLimiter()

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/healthz":
            self._send(200, "application/json", '{"status":"ok"}')
        elif path == "/":
            self._send(200, "text/html", self._page())
        elif path == "/dashboard":
            self._dashboard()
        elif path == "/logout":
            self._logout()
        else:
            self._send(404, "text/plain", "Not found")

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if not self.limiter.allow(self.client_address[0]):
            self._send(429, "text/plain", "Trop de requêtes.")
            return
        fields = self._fields()
        try:
            if path == "/register":
                self._register(fields)
            elif path == "/login":
                self._login(fields)
            elif path == "/connect-ecole-directe":
                self._connect(fields)
            elif path == "/disconnect-ecole-directe":
                self._disconnect(fields)
            else:
                self._send(404, "text/plain", "Not found")
        except (ValueError, PermissionError, RuntimeError) as error:
            self._send(400, "text/html", self._page(html.escape(str(error))))

    def _register(self, fields: dict[str, str]) -> None:
        self.store.register(fields.get("email", ""), fields.get("password", ""))
        self._login(fields)

    def _login(self, fields: dict[str, str]) -> None:
        token = self.store.login(fields.get("email", ""), fields.get("password", ""))
        csrf = secrets.token_urlsafe(24)
        self.csrf_tokens[token] = csrf
        self._set_cookie("session", token, secure=self._is_secure())
        self._send(200, "text/html", self._page("Connexion réussie."))

    def _connect(self, fields: dict[str, str]) -> None:
        session = self._cookie("session")
        user_id = self.store.user_for_token(session)
        if not secrets.compare_digest(self.csrf_tokens.get(session, ""), fields.get("csrf", "")):
            raise PermissionError("Jeton CSRF invalide.")
        identifiant = fields.get("identifiant", "").strip()
        password = fields.get("ecole_password", "")
        if not identifiant or not password:
            raise ValueError("Les identifiants EcoleDirecte sont obligatoires.")
        client = EcoleDirecteClient(identifiant, password)
        try:
            result = client.login()
        except EcoleDirecteError as error:
            raise ValueError(f"Connexion EcoleDirecte refusée : {error}") from error
        if result.get("code") in (250, "250"):
            raise ValueError(
                "EcoleDirecte demande un QCM : utilisez d'abord l'interface locale "
                "pour terminer cette vérification, puis réessayez."
            )
        if not client.student_id:
            raise ValueError("EcoleDirecte n'a pas renvoyé d'identifiant élève vérifiable.")
        self.store.save_ecole_directe_connection(
            user_id, identifiant, password, client.student_id
        )
        self.store.audit(user_id, "ecole_directe_connected")
        self._send(
            200,
            "text/html",
            self._page(
                "Connexion EcoleDirecte vérifiée et chiffrée. "
                f"Identifiant élève associé : {html.escape(client.student_id)}"
            ),
        )

    def _disconnect(self, fields: dict[str, str]) -> None:
        session = self._cookie("session")
        user_id = self.store.user_for_token(session)
        if not secrets.compare_digest(self.csrf_tokens.get(session, ""), fields.get("csrf", "")):
            raise PermissionError("Jeton CSRF invalide.")
        self.store.delete_ecole_directe_connection(user_id)
        self.store.audit(user_id, "ecole_directe_disconnected")
        self._send(200, "text/html", self._page("Connexion EcoleDirecte supprimée."))

    def _dashboard(self) -> None:
        session = self._cookie("session")
        user_id = self.store.user_for_token(session)
        data = self.store.export_account(user_id)
        self._send(
            200,
            "text/html",
            self._page(
                "Tableau de bord : "
                f"compte interne {data['id']}, "
                f"identifiant élève {data['ecole_directe']['student_id'] if data['ecole_directe'] else 'non associé'}."
            ),
        )

    def _logout(self) -> None:
        session = self._cookie("session")
        if session:
            self.store.logout(session)
            self.csrf_tokens.pop(session, None)
        self._set_cookie("session", "", max_age=0, secure=self._is_secure())
        self._send(200, "text/html", self._page("Session fermée."))

    def _page(self, notice: str = "") -> str:
        session = self._cookie("session")
        authenticated = False
        csrf = ""
        connected = False
        if session:
            try:
                user_id = self.store.user_for_token(session)
                authenticated = True
                csrf = self.csrf_tokens.get(session, "")
                connected = self.store.has_ecole_directe_connection(user_id)
            except PermissionError:
                pass
        forms = (
            (
                "<p>Compte connecté. EcoleDirecte : "
                + ("connecté." if connected else "non connecté.")
                + "</p>"
                + (
                    f"<p><a href='/dashboard'>Tableau de bord</a></p>"
                    if connected
                    else ""
                )
                + f"<form method='post' action='/connect-ecole-directe'>"
                f"<input type='hidden' name='csrf' value='{html.escape(csrf, quote=True)}'>"
                "<input name='identifiant' placeholder='Identifiant EcoleDirecte' required>"
                "<input name='ecole_password' type='password' placeholder='Mot de passe EcoleDirecte' required>"
                "<button>Enregistrer la connexion chiffrée</button></form>"
                f"<form method='post' action='/disconnect-ecole-directe'>"
                f"<input type='hidden' name='csrf' value='{html.escape(csrf, quote=True)}'>"
                "<button type='submit'>Déconnecter EcoleDirecte</button></form>"
                "<p><a href='/logout'>Déconnexion</a></p>"
            )
            if authenticated
            else (
                "<form method='post' action='/register'><h2>Créer un compte</h2>"
                "<input name='email' type='email' placeholder='E-mail' required>"
                "<input name='password' type='password' minlength='12' placeholder='Mot de passe (12 caractères minimum)' required>"
                "<button>Créer un compte</button></form>"
                "<form method='post' action='/login'><h2>Se connecter</h2>"
                "<input name='email' type='email' placeholder='E-mail' required>"
                "<input name='password' type='password' placeholder='Mot de passe' required>"
                "<button>Se connecter</button></form>"
            )
        )
        return (
            "<!doctype html><html lang='fr'><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>EC-to-Alexa</title><h1>EC-to-Alexa</h1>"
            f"<p>{html.escape(notice)}</p>{forms}</html>"
        )

    def _fields(self) -> dict[str, str]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 32_768:
            raise ValueError("Requête trop volumineuse.")
        return {key: values[0] for key, values in parse_qs(
            self.rfile.read(length).decode("utf-8")
        ).items()}

    def _cookie(self, name: str) -> str:
        header = self.headers.get("Cookie", "")
        for item in header.split(";"):
            key, _, value = item.strip().partition("=")
            if key == name:
                return value
        return ""

    def _set_cookie(self, name: str, value: str, max_age: int = 86_400, secure: bool = False) -> None:
        flags = f"{name}={value}; Max-Age={max_age}; HttpOnly; SameSite=Lax; Path=/"
        if secure:
            flags += "; Secure"
        self._pending_cookie = flags

    def _is_secure(self) -> bool:
        return os.environ.get("ECO_TO_ALEXA_SECURE_COOKIES", "0") == "1"

    def _send(self, status: int, content_type: str, body: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; form-action 'self'")
        self.send_header("Cache-Control", "no-store")
        if hasattr(self, "_pending_cookie"):
            self.send_header("Set-Cookie", self._pending_cookie)
            del self._pending_cookie
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> None:
    key = os.environ.get("ECO_TO_ALEXA_SECRET_KEY", "")
    if not key:
        raise SystemExit("Configure ECO_TO_ALEXA_SECRET_KEY avec une clé Fernet.")
    store = AccountStore(os.environ.get("ECO_TO_ALEXA_DB", "ec_to_alexa.sqlite3"), key.encode())
    AccountHandler.store = store
    server = ThreadingHTTPServer(("0.0.0.0", 8000), AccountHandler)
    print("Serveur comptes disponible sur http://0.0.0.0:8000")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        store.close()


if __name__ == "__main__":
    main()
