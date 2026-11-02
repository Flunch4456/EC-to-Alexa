"""Secure application primitives for EC-to-Alexa.

The module deliberately keeps EcoleDirecte credentials in memory only.  A
production deployment must use a managed secret store (or an audited
encryption library) before enabling persistent EcoleDirecte connections.
"""

from __future__ import annotations

import hashlib
import hmac
import html
import secrets
import sqlite3
import time
from datetime import date, datetime, timedelta
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from alexa_speech import (
    describe_absences,
    describe_grades,
    describe_homework,
    describe_messages,
    describe_next_class,
    describe_timetable,
    next_school_day,
    school_year,
)
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
        # Même endpoint que web.py, validé avec un compte réel.
        if not self.client.student_id:
            raise EcoleDirecteError("Aucun élève n'est associé à cette session.")
        return self.client.get(
            f"E/{self.client.student_id}/emploidutemps.awp?verbe=get&v=1.8.28",
            dateDebut=start.isoformat(),
            dateFin=end.isoformat(),
        )

    def homework(self, day: date) -> dict[str, Any]:
        # L'endpoint daté renvoie le contenu détaillé de chaque devoir.
        return self.client.get(
            self._student_endpoint(f"cahierdetexte/{day.isoformat()}.awp?verbe=get")
        )

    def absences(self) -> dict[str, Any]:
        return self.client.get(self._student_endpoint("viescolaire.awp?verbe=get"))

    def messages(self) -> dict[str, Any]:
        return self.client.get(
            self._student_endpoint(
                "messages.awp?force=false&typeRecuperation=received&idClasseur=0"
                "&orderBy=date&order=desc&query=&onlyRead=&page=0&itemsPerPage=20"
                "&getAll=0&verbe=get"
            ),
            anneeMessages=school_year(date.today()),
        )

    def grades(self) -> dict[str, Any]:
        return self.client.get(self._student_endpoint("notes.awp?verbe=get"), anneeScolaire="")


def _now() -> datetime:
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("Europe/Paris")).replace(tzinfo=None)
    except Exception:  # tzdata absent (Windows) : heure locale de la machine
        return datetime.now()


class AlexaSkill:
    """Maps authenticated Alexa intents to the current user's service only."""

    HELP = (
        "Tu peux me demander : quel est mon emploi du temps demain, "
        "c'est quoi mon prochain cours, quels sont mes devoirs pour lundi, "
        "mes dernières notes, mes absences ou mes nouveaux messages."
    )
    REPROMPT = "Que veux-tu savoir ? Par exemple : mes devoirs pour demain."

    def __init__(
        self,
        store: AccountStore,
        services: dict[int, EcoleDirecteService] | None = None,
        clock: Any = _now,
    ) -> None:
        self.store = store
        self.services = services if services is not None else {}
        self.clock = clock

    @classmethod
    def from_store(cls, store: AccountStore) -> "AlexaSkill":
        # Les services sont chargés à la demande : un compte lié après le
        # démarrage du serveur est donc pris en compte sans redémarrage.
        return cls(store)

    def _service_for(self, user_id: int) -> EcoleDirecteService | None:
        service = self.services.get(user_id)
        if service is None and self.store._fernet is not None:
            client = self.store.ecole_directe_client(user_id)
            if client is not None:
                service = self.services[user_id] = EcoleDirecteService(client)
        return service

    def handle(self, access_token: str | None, request: dict[str, Any]) -> dict[str, Any]:
        body = request.get("request", {})
        kind = body.get("type")
        if kind == "SessionEndedRequest":
            return {"version": "1.0", "response": {}}
        intent = body.get("intent", {})
        name = intent.get("name")
        if name in {"AMAZON.StopIntent", "AMAZON.CancelIntent"}:
            return self._response("À bientôt.", end_session=True)
        if name in {"AMAZON.HelpIntent", "HelpIntent"}:
            return self._response(self.HELP, reprompt=self.REPROMPT)
        if not access_token:
            return self._link_account()
        try:
            user_id = self.store.user_for_token(access_token, "alexa_tokens")
        except PermissionError:
            return self._link_account()
        service = self._service_for(user_id)
        if service is None:
            return self._response(
                "Ton compte École Directe n'est pas connecté. Connecte-le "
                "depuis le site EC to Alexa, puis redemande-moi."
            )
        if kind == "LaunchRequest":
            return self._response(
                "Bienvenue sur ton carnet École Directe. " + self.REPROMPT,
                reprompt=self.REPROMPT,
            )
        try:
            text = self._answer(service, name, intent.get("slots") or {})
        except EcoleDirecteError:
            text = (
                "Je n'arrive pas à joindre École Directe pour le moment. Si une "
                "question de sécurité t'est demandée, reconnecte ton compte sur le "
                "site EC to Alexa, puis réessaie."
            )
        if text is None:
            return self._response(
                "Je n'ai pas compris. " + self.HELP, reprompt=self.REPROMPT
            )
        return self._response(text, end_session=True)

    def _answer(
        self, service: EcoleDirecteService, name: str | None, slots: dict[str, Any]
    ) -> str | None:
        now = self.clock()
        today = now.date()
        if name == "GetAbsencesIntent":
            return describe_absences(service.absences())
        if name == "GetMessagesIntent":
            return describe_messages(service.messages())
        if name == "GetGradesIntent":
            return describe_grades(service.grades())
        if name == "GetHomeworkIntent":
            day = self._slot_date(slots, today) or next_school_day(today)
            return describe_homework(service.homework(day), day, today)
        if name == "GetNextClassIntent":
            return describe_next_class(service.timetable(today, today), now)
        if name in {"GetScheduleIntent", "GetTodayScheduleIntent", "GetTomorrowScheduleIntent"}:
            default = today + timedelta(days=1) if name == "GetTomorrowScheduleIntent" else today
            day = self._slot_date(slots, today) or default
            return describe_timetable(service.timetable(day, day), day, today)
        return None

    @staticmethod
    def _slot_date(slots: dict[str, Any], today: date) -> date | None:
        value = str((slots.get("jour") or {}).get("value") or "")
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
        # AMAZON.DATE peut renvoyer une semaine (2026-W45) : on prend son lundi.
        if "-W" in value:
            try:
                return datetime.strptime(value[:8] + "-1", "%G-W%V-%u").date()
            except ValueError:
                return None
        return None

    @staticmethod
    def _speech(text: str) -> dict[str, str]:
        return {"type": "SSML", "ssml": f"<speak>{html.escape(text, quote=False)}</speak>"}

    def _response(
        self, text: str, end_session: bool = False, reprompt: str | None = None
    ) -> dict[str, Any]:
        response: dict[str, Any] = {
            "outputSpeech": self._speech(text),
            "card": {"type": "Simple", "title": "École Directe", "content": text},
            "shouldEndSession": end_session if reprompt is None else False,
        }
        if reprompt:
            response["reprompt"] = {"outputSpeech": self._speech(reprompt)}
        return {"version": "1.0", "response": response}

    def _link_account(self) -> dict[str, Any]:
        text = (
            "Pour utiliser cette skill, lie ton compte EC to Alexa dans "
            "l'application Alexa. Je t'y ai envoyé une carte."
        )
        return {
            "version": "1.0",
            "response": {
                "outputSpeech": self._speech(text),
                "card": {"type": "LinkAccount"},
                "shouldEndSession": True,
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
