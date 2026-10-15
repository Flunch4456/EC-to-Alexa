"""Secure application primitives for EC-to-Alexa.

The module deliberately keeps EcoleDirecte credentials in memory only.  A
production deployment must use a managed secret store (or an audited
encryption library) before enabling persistent EcoleDirecte connections.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import time
from datetime import date, timedelta
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from ecole_directe import EcoleDirecteClient, EcoleDirecteError


class AccountStore:
    """Small SQLite-backed account and token store with tenant isolation."""

    def __init__(self, path: str = "ec_to_alexa.sqlite3", secret_key: bytes | None = None) -> None:
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS alexa_tokens (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS oauth_codes (
                code_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                client_id TEXT NOT NULL,
                redirect_uri TEXT NOT NULL,
                expires_at INTEGER NOT NULL,
                used INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS ecole_directe_connections (
                user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                encrypted_credentials BLOB NOT NULL,
                updated_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS audit_events (
                id INTEGER PRIMARY KEY,
                user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                event TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );
            """
        )
        try:
            self.db.execute(
                "ALTER TABLE ecole_directe_connections ADD COLUMN student_id TEXT"
            )
        except sqlite3.OperationalError:
            pass
        self.db.commit()
        self._fernet = Fernet(secret_key) if secret_key else None

    @staticmethod
    def _password_hash(password: str, salt: bytes | None = None) -> str:
        salt = salt or secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 210_000)
        return f"{salt.hex()}${digest.hex()}"

    @staticmethod
    def _password_matches(password: str, encoded: str) -> bool:
        try:
            salt_hex, digest_hex = encoded.split("$", 1)
            actual = hashlib.pbkdf2_hmac(
                "sha256", password.encode(), bytes.fromhex(salt_hex), 210_000
            ).hex()
        except (ValueError, TypeError):
            return False
        return hmac.compare_digest(actual, digest_hex)

    @staticmethod
    def _hash_token(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def register(self, email: str, password: str) -> int:
        email = email.strip().lower()
        if "@" not in email or len(email) > 254:
            raise ValueError("Adresse e-mail invalide.")
        if len(password) < 12:
            raise ValueError("Le mot de passe doit contenir au moins 12 caractères.")
        try:
            cursor = self.db.execute(
                "INSERT INTO users(email, password_hash, created_at) VALUES (?, ?, ?)",
                (email, self._password_hash(password), int(time.time())),
            )
            self.db.commit()
        except sqlite3.IntegrityError as error:
            raise ValueError("Cette adresse e-mail est déjà utilisée.") from error
        return int(cursor.lastrowid)

    def user_for_login(self, email: str, password: str) -> int:
        row = self.db.execute(
            "SELECT id, password_hash FROM users WHERE email = ? COLLATE NOCASE",
            (email.strip(),),
        ).fetchone()
        if not row or not self._password_matches(password, row["password_hash"]):
            raise PermissionError("Identifiants invalides.")
        return int(row["id"])

    def login(self, email: str, password: str, ttl: int = 86_400) -> str:
        user_id = self.user_for_login(email, password)
        token = secrets.token_urlsafe(32)
        self.db.execute(
            "INSERT INTO sessions VALUES (?, ?, ?)",
            (self._hash_token(token), user_id, int(time.time()) + ttl),
        )
        self.db.commit()
        return token

    def user_for_token(self, token: str, table: str = "sessions") -> int:
        if table not in {"sessions", "alexa_tokens"}:
            raise ValueError("Table de jetons invalide.")
        row = self.db.execute(
            f"SELECT user_id, expires_at FROM {table} WHERE token_hash = ?",
            (self._hash_token(token),),
        ).fetchone()
        if not row or row["expires_at"] <= int(time.time()):
            raise PermissionError("Jeton absent ou expiré.")
        return int(row["user_id"])

    def logout(self, token: str) -> None:
        self.db.execute("DELETE FROM sessions WHERE token_hash = ?", (self._hash_token(token),))
        self.db.commit()

    def has_ecole_directe_connection(self, user_id: int) -> bool:
        return (
            self.db.execute(
                "SELECT 1 FROM ecole_directe_connections WHERE user_id = ?", (user_id,)
            ).fetchone()
            is not None
        )

    def create_alexa_token(self, user_id: int, ttl: int = 31_536_000) -> str:
        token = secrets.token_urlsafe(32)
        self.db.execute(
            "INSERT INTO alexa_tokens VALUES (?, ?, ?)",
            (self._hash_token(token), user_id, int(time.time()) + ttl),
        )
        self.db.commit()
        return token

    def create_authorization_code(
        self, user_id: int, client_id: str, redirect_uri: str, ttl: int = 300
    ) -> str:
        code = secrets.token_urlsafe(32)
        self.db.execute(
            "INSERT INTO oauth_codes VALUES (?, ?, ?, ?, ?, 0)",
            (
                self._hash_token(code),
                user_id,
                client_id,
                redirect_uri,
                int(time.time()) + ttl,
            ),
        )
        self.db.commit()
        return code

    def exchange_authorization_code(
        self, code: str, client_id: str, redirect_uri: str
    ) -> str:
        row = self.db.execute(
            """
            SELECT user_id FROM oauth_codes
            WHERE code_hash = ? AND client_id = ? AND redirect_uri = ?
              AND expires_at > ? AND used = 0
            """,
            (self._hash_token(code), client_id, redirect_uri, int(time.time())),
        ).fetchone()
        if not row:
            raise PermissionError("Code OAuth invalide, expiré ou déjà utilisé.")
        self.db.execute(
            "UPDATE oauth_codes SET used = 1 WHERE code_hash = ?",
            (self._hash_token(code),),
        )
        token = self.create_alexa_token(int(row["user_id"]))
        self.db.commit()
        return token

    def revoke_alexa_token(self, token: str) -> None:
        self.db.execute(
            "DELETE FROM alexa_tokens WHERE token_hash = ?", (self._hash_token(token),)
        )
        self.db.commit()

    def save_ecole_directe_connection(
        self, user_id: int, identifiant: str, mot_de_passe: str, student_id: str
    ) -> None:
        if self._fernet is None:
            raise RuntimeError("ECO_TO_ALEXA_SECRET_KEY est requis pour chiffrer la connexion.")
        payload = f"{identifiant}\x00{mot_de_passe}".encode("utf-8")
        encrypted = self._fernet.encrypt(payload)
        self.db.execute(
            """
            INSERT INTO ecole_directe_connections(
                user_id, encrypted_credentials, student_id, updated_at
            )
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                encrypted_credentials=excluded.encrypted_credentials,
                student_id=excluded.student_id,
                updated_at=excluded.updated_at
            """,
            (user_id, encrypted, student_id, int(time.time())),
        )
        self.db.commit()

    def ecole_directe_client(self, user_id: int) -> EcoleDirecteClient | None:
        if self._fernet is None:
            raise RuntimeError("ECO_TO_ALEXA_SECRET_KEY est requis pour déchiffrer la connexion.")
        row = self.db.execute(
            "SELECT encrypted_credentials, student_id FROM ecole_directe_connections WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if not row:
            return None
        try:
            identifiant, mot_de_passe = self._fernet.decrypt(row["encrypted_credentials"]).decode(
                "utf-8"
            ).split("\x00", 1)
        except (InvalidToken, UnicodeDecodeError, ValueError) as error:
            raise RuntimeError("Connexion EcoleDirecte chiffrée illisible.") from error
        client = EcoleDirecteClient(identifiant, mot_de_passe)
        client.student_id = row["student_id"]
        return client

    def ecole_directe_info(self, user_id: int) -> dict[str, str | None] | None:
        row = self.db.execute(
            "SELECT student_id, updated_at FROM ecole_directe_connections WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if not row:
            return None
        return {"student_id": row["student_id"], "updated_at": str(row["updated_at"])}

    def delete_ecole_directe_connection(self, user_id: int) -> None:
        self.db.execute("DELETE FROM ecole_directe_connections WHERE user_id = ?", (user_id,))
        self.db.commit()

    def delete_account(self, user_id: int) -> None:
        self.db.execute("DELETE FROM users WHERE id = ?", (user_id,))
        self.db.commit()

    def export_account(self, user_id: int) -> dict[str, Any]:
        row = self.db.execute(
            "SELECT id, email, created_at FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if not row:
            raise PermissionError("Compte introuvable.")
        return {
            "id": int(row["id"]),
            "email": row["email"],
            "created_at": int(row["created_at"]),
            "ecole_directe_connected": self.has_ecole_directe_connection(user_id),
            "ecole_directe": self.ecole_directe_info(user_id),
        }

    def audit(self, user_id: int, event: str) -> None:
        if not event or len(event) > 80:
            raise ValueError("Événement d'audit invalide.")
        self.db.execute(
            "INSERT INTO audit_events(user_id, event, created_at) VALUES (?, ?, ?)",
            (user_id, event, int(time.time())),
        )
        self.db.commit()

    def close(self) -> None:
        self.db.close()


class EcoleDirecteService:
    """Validated, user-scoped façade over the existing API client."""

    def __init__(self, client: EcoleDirecteClient) -> None:
        self.client = client

    def _student_endpoint(self, path: str) -> str:
        if not self.client.student_id:
            raise EcoleDirecteError("Aucun élève n'est associé à cette session.")
        return f"eleves/{self.client.student_id}/{path}"

    def timetable(self, start: date, end: date) -> dict[str, Any]:
        return self.client.get(
            self._student_endpoint("emploidutemps.awp?verbe=get"),
            dateDebut=start.isoformat(),
            dateFin=end.isoformat(),
        )

    def homework(self, day: date) -> dict[str, Any]:
        return self.client.get(
            self._student_endpoint("cahierdetexte.awp?verbe=get"),
            dateDebut=day.isoformat(),
            dateFin=day.isoformat(),
        )

    def absences(self) -> dict[str, Any]:
        return self.client.get(self._student_endpoint("viescolaire.awp?verbe=get"))

    def messages(self) -> dict[str, Any]:
        return self.client.get(self._student_endpoint("messages.awp?verbe=get"))


class AlexaSkill:
    """Maps authenticated Alexa intents to the current user's service only."""

    def __init__(self, store: AccountStore, services: dict[int, EcoleDirecteService]) -> None:
        self.store = store
        self.services = services

    @classmethod
    def from_store(cls, store: AccountStore) -> "AlexaSkill":
        services: dict[int, EcoleDirecteService] = {}
        rows = store.db.execute("SELECT id FROM users").fetchall()
        for row in rows:
            client = store.ecole_directe_client(int(row["id"]))
            if client is not None:
                services[int(row["id"])] = EcoleDirecteService(client)
        return cls(store, services)

    def handle(self, access_token: str, request: dict[str, Any]) -> dict[str, Any]:
        user_id = self.store.user_for_token(access_token, "alexa_tokens")
        service = self.services.get(user_id)
        if service is None:
            return self._response("Ton compte EcoleDirecte n'est pas connecté.")
        intent = request.get("request", {}).get("intent", {}).get("name")
        if intent == "GetAbsencesIntent":
            result = service.absences()
            return self._response(self._count_message("absence", result))
        if intent == "GetMessagesIntent":
            result = service.messages()
            return self._response(self._count_message("message", result))
        if intent == "GetHomeworkIntent":
            result = service.homework(date.today())
            return self._response(self._count_message("devoir", result))
        if intent in {"GetTodayScheduleIntent", "GetNextClassIntent"}:
            result = service.timetable(date.today(), date.today())
            return self._response(self._count_message("cours", result))
        if intent == "GetTomorrowScheduleIntent":
            tomorrow = date.today() + timedelta(days=1)
            result = service.timetable(tomorrow, tomorrow)
            return self._response(self._count_message("cours", result))
        if intent == "GetUpcomingAssessmentsIntent":
            return self._response(
                "Les évaluations annoncées ne sont pas exposées de façon vérifiée "
                "par le client actuel."
            )
        if intent in {"AMAZON.HelpIntent", "HelpIntent"}:
            return self._response("Tu peux demander tes devoirs, tes absences ou tes messages.")
        if intent in {"AMAZON.StopIntent", "AMAZON.CancelIntent"}:
            return self._response("À bientôt.", end_session=True)
        return self._response("Je ne peux pas répondre à cette demande.")

    @staticmethod
    def _count_message(kind: str, result: dict[str, Any]) -> str:
        data = result.get("data")
        count = len(data) if isinstance(data, list) else 0
        return f"Il y a {count} {kind}{'s' if count != 1 else ''} dans les données disponibles."

    @staticmethod
    def _response(text: str, end_session: bool = False) -> dict[str, Any]:
        return {
            "version": "1.0",
            "response": {
                "outputSpeech": {"type": "SSML", "ssml": f"<speak>{text}</speak>"},
                "shouldEndSession": end_session,
            },
        }


class OAuthAccountLinking:
    """OAuth authorization-code provider for Alexa Account Linking."""

    def __init__(self, store: AccountStore, client_id: str, redirect_uri: str) -> None:
        if not client_id or not redirect_uri.startswith("https://"):
            raise ValueError("OAuth doit utiliser un client et une URL HTTPS.")
        self.store = store
        self.client_id = client_id
        self.redirect_uri = redirect_uri

    def authorize(self, email: str, password: str) -> str:
        user_id = self.store.user_for_login(email, password)
        return self.store.create_authorization_code(
            user_id, self.client_id, self.redirect_uri
        )

    def token(self, code: str, client_id: str, redirect_uri: str) -> dict[str, Any]:
        if client_id != self.client_id or redirect_uri != self.redirect_uri:
            raise PermissionError("Client OAuth non autorisé.")
        access_token = self.store.exchange_authorization_code(
            code, client_id, redirect_uri
        )
        return {
            "token_type": "Bearer",
            "access_token": access_token,
            "expires_in": 31_536_000,
        }

    def revoke(self, token: str) -> None:
        self.store.revoke_alexa_token(token)
