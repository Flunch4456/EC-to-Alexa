"""Account and EcoleDirecte connection server for local/reverse-proxy use."""

from __future__ import annotations

import html
import hashlib
import hmac
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address, ip_network
from urllib.parse import parse_qs, urlparse

from application import AccountStore
from ecole_directe import EcoleDirecteClient, EcoleDirecteError


MAX_HTTP_BODY = 32_768


class SharedRateLimiter:
    def __init__(self, max_keys: int = 4096) -> None:
        self.max_keys = max_keys
        self._lock = threading.Lock()
        self.attempts: dict[str, list[float]] = {}

    def allow(self, key: str, limit: int, window: int) -> bool:
        now = time.time()
        with self._lock:
            values = [stamp for stamp in self.attempts.get(key, []) if now - stamp < window]
            if len(values) >= limit:
                self.attempts[key] = values
                return False
            values.append(now)
            self.attempts[key] = values
            if len(self.attempts) > self.max_keys:
                oldest = sorted(
                    self.attempts.items(),
                    key=lambda item: item[1][-1] if item[1] else now,
                )[: len(self.attempts) - self.max_keys]
                for stale_key, _ in oldest:
                    self.attempts.pop(stale_key, None)
            return True


class AccountHandler(BaseHTTPRequestHandler):
    store: AccountStore
    csrf_secret: bytes = b""
    limiter = SharedRateLimiter()

    def do_GET(self) -> None:
        self.connection.settimeout(10)
        path = urlparse(self.path).path
        if path == "/healthz":
            self._send(200, "application/json", '{"status":"ok"}')
        elif path == "/":
            self._send(200, "text/html", self._page())
        elif path == "/dashboard":
            self._dashboard()
        elif path == "/logout":
            self._send(405, "text/plain", "Méthode non autorisée.")
        else:
            self._send(404, "text/plain", "Not found")

    def do_POST(self) -> None:
        self.connection.settimeout(10)
        path = urlparse(self.path).path
        if path not in ("/register", "/login", "/connect-ecole-directe", "/disconnect-ecole-directe", "/logout"):
            self._send(404, "text/plain", "Not found")
            return
        try:
            fields = self._fields()
            if path in ("/register", "/login") and not self._allow_login_attempt(fields.get("email", "")):
                self._send(429, "text/plain", "Trop de tentatives de connexion.")
                return
            self._validate_csrf(fields)
            if path == "/register":
                self._register(fields)
            elif path == "/login":
                self._login(fields)
            elif path == "/connect-ecole-directe":
                self._connect(fields)
            elif path == "/disconnect-ecole-directe":
                self._disconnect(fields)
            elif path == "/logout":
                self._logout()
            else:
                self._send(404, "text/plain", "Not found")
        except (ValueError, PermissionError, RuntimeError) as error:
            self._send(400, "text/html", self._page(html.escape(str(error))))

    def _register(self, fields: dict[str, str]) -> None:
        self.store.register(fields.get("email", ""), fields.get("password", ""))
        self._login(fields)

    def _login(self, fields: dict[str, str]) -> None:
        token = self.store.login(fields.get("email", ""), fields.get("password", ""))
        self._set_cookie("session", token, secure=self._is_secure())
        self._send(200, "text/html", self._page("Connexion réussie."))

    def _connect(self, fields: dict[str, str]) -> None:
        session = self._cookie("session")
        user_id = self.store.user_for_token(session)
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
        self.store.delete_ecole_directe_connection(user_id)
        self.store.revoke_alexa_tokens_for_user(user_id)
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
        self._set_cookie("session", "", max_age=0, secure=self._is_secure())
        self._send(200, "text/html", self._page("Session fermée."))

    def _page(self, notice: str = "") -> str:
        self.store.cleanup_expired_tokens()
        session = self._cookie("session")
        authenticated = False
        csrf = self._csrf_token()
        connected = False
        if session:
            try:
                user_id = self.store.user_for_token(session)
                authenticated = True
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
                f"<form method='post' action='/logout'>"
                f"<input type='hidden' name='csrf' value='{html.escape(csrf, quote=True)}'>"
                "<button type='submit'>Déconnexion</button></form>"
            )
            if authenticated
            else (
               f"<form method='post' action='/register'><h2>Créer un compte</h2>"
               f"<input type='hidden' name='csrf' value='{html.escape(csrf, quote=True)}'>"
               "<input name='email' type='email' placeholder='E-mail' required>"
               "<input name='password' type='password' minlength='12' placeholder='Mot de passe (12 caractères minimum)' required>"
               "<button>Créer un compte</button></form>"
               f"<form method='post' action='/login'><h2>Se connecter</h2>"
               f"<input type='hidden' name='csrf' value='{html.escape(csrf, quote=True)}'>"
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
        body = self._read_body("application/x-www-form-urlencoded")
        return {key: values[0] for key, values in parse_qs(body.decode("utf-8")).items()}

    def _content_length(self) -> int:
        values = self.headers.get_all("Content-Length", [])
        if len(values) != 1:
            raise ValueError("Content-Length ambigu.")
        raw = values[0].strip()
        if not raw.isdigit():
            raise ValueError("Content-Length invalide.")
        length = int(raw)
        if length < 0 or length > MAX_HTTP_BODY:
            raise ValueError("Requête trop volumineuse.")
        return length

    def _read_body(self, expected_type: str) -> bytes:
        content_type = self.headers.get("Content-Type", "")
        media_type, _, charset_part = content_type.partition(";")
        if media_type.strip().lower() != expected_type:
            raise ValueError("Content-Type invalide.")
        if charset_part:
            if "charset=" not in charset_part.lower():
                raise ValueError("charset invalide.")
            charset = charset_part.split("=", 1)[1].strip().lower().strip('"')
            if charset != "utf-8":
                raise ValueError("charset invalide.")
        return self.rfile.read(self._content_length())

    def _allow_login_attempt(self, email: str) -> bool:
        origin = self._request_origin()
        account = email.strip().lower() or "anon"
        return (
            self.limiter.allow("global", limit=120, window=60)
            and self.limiter.allow(f"origin:{origin}", limit=30, window=60)
            and self.limiter.allow(f"account:{account}", limit=12, window=120)
        )

    def _request_origin(self) -> str:
        remote = self.client_address[0]
        if self.headers.get("X-Forwarded-For") and any(
            ip_address(remote) in network for network in self._trusted_proxy_networks()
        ):
            return self.headers.get("X-Forwarded-For", "").split(",")[0].strip() or remote
        return remote

    @staticmethod
    def _trusted_proxy_networks() -> tuple[object, ...]:
        configured = os.environ.get("ECO_TO_ALEXA_TRUSTED_PROXIES", "127.0.0.1/32,::1/128")
        networks = []
        for item in configured.split(","):
            item = item.strip()
            if item:
                networks.append(ip_network(item, strict=False))
        return tuple(networks)

    def _csrf_context(self) -> str:
        session = self._cookie("session")
        if session:
            return f"session:{session}"
        browser = self._cookie("browser")
        if not browser:
            browser = secrets.token_urlsafe(24)
            self._set_cookie("browser", browser, max_age=86_400 * 30, secure=self._is_secure())
        return f"browser:{browser}"

    def _csrf_token(self) -> str:
        context = self._csrf_context()
        return hmac.new(self.csrf_secret, context.encode("utf-8"), hashlib.sha256).hexdigest()

    def _validate_csrf(self, fields: dict[str, str]) -> None:
        provided = fields.get("csrf", "")
        if not provided:
            raise PermissionError("Jeton CSRF invalide.")
        expected = self._csrf_token()
        if not expected or not secrets.compare_digest(expected, provided):
            raise PermissionError("Jeton CSRF invalide.")

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
        if not hasattr(self, "_pending_cookies"):
            self._pending_cookies = []
        self._pending_cookies.append(flags)

    def _is_secure(self) -> bool:
        return os.environ.get("ECO_TO_ALEXA_ALLOW_INSECURE_HTTP", "0") != "1"

    def _send(self, status: int, content_type: str, body: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; form-action 'self'; frame-ancestors 'none'")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        if hasattr(self, "_pending_cookies"):
            for cookie in self._pending_cookies:
                self.send_header("Set-Cookie", cookie)
            del self._pending_cookies
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
    csrf_secret = os.environ.get("ECO_TO_ALEXA_CSRF_SECRET", key).encode("utf-8")
    AccountHandler.csrf_secret = hashlib.sha256(csrf_secret).digest()
    bind_host = os.environ.get("ECO_TO_ALEXA_ACCOUNT_BIND", "127.0.0.1")
    server = ThreadingHTTPServer((bind_host, 8000), AccountHandler)
    print(f"Serveur comptes disponible sur http://{bind_host}:8000")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        store.close()


if __name__ == "__main__":
    main()
