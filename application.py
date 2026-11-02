"""Secure application primitives for EC-to-Alexa.

The module deliberately keeps EcoleDirecte credentials in memory only.  A
production deployment must use a managed secret store (or an audited
encryption library) before enabling persistent EcoleDirecte connections.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import html
import os
import secrets
import sqlite3
import threading
import time
from datetime import date, datetime, timedelta
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from alexa_speech import (
    describe_absences,
    describe_assessments,
    describe_grades,
    describe_homework,
    describe_messages,
    describe_next_class,
    describe_timetable,
    next_school_day,
    school_year,
)
from ecole_directe import EcoleDirecteClient, EcoleDirecteError, EcoleDirecteQcmRequired

MAX_BODY_SIZE = 32_768


class SharedRateLimiter:
    """Bounded, process-wide limiter shared by all authentication endpoints."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._attempts: dict[str, list[float]] = {}
        self._last_cleanup = 0.0

    def allow(self, keys: tuple[str, ...], limit: int = 10, window: int = 60) -> bool:
        now = time.monotonic()
        with self._lock:
            if now - self._last_cleanup >= 60:
                self._attempts = {
                    key: values
                    for key, values in self._attempts.items()
                    if values and now - values[-1] < window
                }
                self._last_cleanup = now
            allowed = True
            for key in keys:
                values = [stamp for stamp in self._attempts.get(key, []) if now - stamp < window]
                if len(values) >= limit:
                    allowed = False
                values.append(now)
                self._attempts[key] = values[-limit:]
            return allowed


def _is_trusted_proxy(address: str) -> bool:
    """Adresses exactes ou plages CIDR listées dans ECO_TO_ALEXA_TRUSTED_PROXIES."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    for value in os.environ.get("ECO_TO_ALEXA_TRUSTED_PROXIES", "").split(","):
        value = value.strip()
        if not value:
            continue
        try:
            if ip in ipaddress.ip_network(value, strict=False):
                return True
        except ValueError:
            continue
    return False


def trusted_client_address(handler: Any) -> str:
    """Use forwarded addresses only when the immediate proxy is explicitly trusted."""
    address = handler.client_address[0]
    if _is_trusted_proxy(address):
        forwarded = handler.headers.get("X-Forwarded-For", "").split(",", 1)[0].strip()
        if forwarded and len(forwarded) <= 64:
            return forwarded
    return address


def read_body(handler: Any, content_type: str, max_size: int = MAX_BODY_SIZE) -> bytes:
    """Read exactly one bounded UTF-8 request body with an unambiguous length."""
    raw_length = handler.headers.get("Content-Length")
    if raw_length is None or "," in raw_length or not raw_length.strip().isdigit():
        raise ValueError("Content-Length invalide.")
    length = int(raw_length.strip())
    if length < 0 or length > max_size:
        raise ValueError("Requête trop volumineuse.")
    actual_type = handler.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
    if actual_type != content_type:
        raise ValueError("Content-Type invalide.")
    body = handler.rfile.read(length)
    if len(body) != length:
        raise ValueError("Corps de requête incomplet.")
    return body


class AccountStore:
    """Small SQLite-backed account and token store with tenant isolation."""

    def __init__(self, path: str = "ec_to_alexa.sqlite3", secret_key: bytes | None = None) -> None:
        self._lock = threading.RLock()
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
            CREATE TABLE IF NOT EXISTS csrf_tokens (
                session_hash TEXT PRIMARY KEY,
                token_hash TEXT NOT NULL,
                expires_at INTEGER NOT NULL
            );
            -- Compte Alexa (identifiant amzn1.ask.account… propre à la skill)
            -- associé à un compte EC-to-Alexa, donc à sa liaison École Directe.
            CREATE TABLE IF NOT EXISTS amazon_links (
                user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                alexa_user_id TEXT NOT NULL,
                source TEXT NOT NULL,
                linked_at INTEGER NOT NULL,
                last_seen_at INTEGER
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
        with self._lock:
            row = self.db.execute(
                "SELECT id, password_hash FROM users WHERE email = ? COLLATE NOCASE",
                (email.strip(),),
            ).fetchone()
        # Keep the work factor similar for unknown accounts.
        valid = row and self._password_matches(password, row["password_hash"])
        if not row:
            self._password_matches(password, self._password_hash("invalid-account"))
        if not valid:
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
        with self._lock:
            token_hash = self._hash_token(token)
            self.db.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
            self.db.execute("DELETE FROM csrf_tokens WHERE session_hash = ?", (token_hash,))
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
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute(
                """
                SELECT user_id FROM oauth_codes
                WHERE code_hash = ? AND client_id = ? AND redirect_uri = ?
                  AND expires_at > ? AND used = 0
                """,
                (self._hash_token(code), client_id, redirect_uri, int(time.time())),
            ).fetchone()
            if not row:
                self.db.rollback()
                raise PermissionError("Code OAuth invalide, expiré ou déjà utilisé.")
            updated = self.db.execute(
                "UPDATE oauth_codes SET used = 1 WHERE code_hash = ? AND used = 0",
                (self._hash_token(code),),
            ).rowcount
            if updated != 1:
                self.db.rollback()
                raise PermissionError("Code OAuth invalide, expiré ou déjà utilisé.")
            token = secrets.token_urlsafe(32)
            self.db.execute(
                "INSERT INTO alexa_tokens VALUES (?, ?, ?)",
                (self._hash_token(token), int(row["user_id"]), int(time.time()) + 31_536_000),
            )
            self.db.commit()
            return token

    def revoke_alexa_token(self, token: str) -> None:
        with self._lock:
            self.db.execute(
                "DELETE FROM alexa_tokens WHERE token_hash = ?", (self._hash_token(token),)
            )
            self.db.commit()

    def save_ecole_directe_connection(
        self,
        user_id: int,
        identifiant: str,
        mot_de_passe: str,
        student_id: str,
        device_uuid: str | None = None,
        cn: str | None = None,
        cv: str | None = None,
    ) -> None:
        if self._fernet is None:
            raise RuntimeError("ECO_TO_ALEXA_SECRET_KEY est requis pour chiffrer la connexion.")
        # L'UUID d'appareil et les valeurs cn/cv du QCM sont conservés (chiffrés)
        # pour que les reconnexions d'Alexa ne redéclenchent pas le QCM.
        payload = json.dumps(
            {
                "identifiant": identifiant,
                "mot_de_passe": mot_de_passe,
                "device_uuid": device_uuid,
                "cn": cn,
                "cv": cv,
            }
        ).encode("utf-8")
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
            (user_id, encrypted, student_id, time.time_ns() // 1000),
        )
        self.db.commit()

    def ecole_directe_client(
        self, user_id: int, timeout: float = 20
    ) -> EcoleDirecteClient | None:
        row = self.db.execute(
            "SELECT encrypted_credentials, student_id FROM ecole_directe_connections WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if not row:
            return None
        if self._fernet is None:
            raise RuntimeError("ECO_TO_ALEXA_SECRET_KEY est requis pour déchiffrer la connexion.")
        try:
            raw = self._fernet.decrypt(row["encrypted_credentials"]).decode("utf-8")
            if raw.startswith("{"):
                data = json.loads(raw)
            else:  # format historique « identifiant\x00mot_de_passe »
                identifiant, mot_de_passe = raw.split("\x00", 1)
                data = {"identifiant": identifiant, "mot_de_passe": mot_de_passe}
        except (InvalidToken, UnicodeDecodeError, ValueError) as error:
            raise RuntimeError("Connexion EcoleDirecte chiffrée illisible.") from error
        client = EcoleDirecteClient(
            data["identifiant"],
            data["mot_de_passe"],
            device_uuid=data.get("device_uuid"),
            cn=data.get("cn"),
            cv=data.get("cv"),
            timeout=timeout,
        )
        client.student_id = row["student_id"]
        return client

    def ecole_directe_version(self, user_id: int) -> int | None:
        """Horodatage de la liaison : change à chaque reconnexion ou suppression."""
        row = self.db.execute(
            "SELECT updated_at FROM ecole_directe_connections WHERE user_id = ?", (user_id,)
        ).fetchone()
        return int(row["updated_at"]) if row else None

    def alexa_link_count(self, user_id: int) -> int:
        row = self.db.execute(
            "SELECT COUNT(*) FROM alexa_tokens WHERE user_id = ? AND expires_at > ?",
            (user_id, int(time.time())),
        ).fetchone()
        return int(row[0])

    def revoke_alexa_for_user(self, user_id: int) -> None:
        with self._lock:
            self.db.execute("DELETE FROM alexa_tokens WHERE user_id = ?", (user_id,))
            self.db.execute("DELETE FROM amazon_links WHERE user_id = ?", (user_id,))
            self.db.commit()

    def record_amazon_link(self, user_id: int, alexa_user_id: str, source: str) -> None:
        """Associe un compte Alexa à ce compte EC-to-Alexa (un seul compte Alexa par compte)."""
        if not alexa_user_id.startswith("amzn1.") or len(alexa_user_id) > 512:
            raise ValueError("Identifiant Alexa invalide.")
        now = int(time.time())
        with self._lock:
            self.db.execute(
                """
                INSERT INTO amazon_links(user_id, alexa_user_id, source, linked_at, last_seen_at)
                VALUES (?, ?, ?, ?, NULL)
                ON CONFLICT(user_id) DO UPDATE SET
                    alexa_user_id=excluded.alexa_user_id,
                    source=excluded.source,
                    linked_at=CASE WHEN amazon_links.alexa_user_id = excluded.alexa_user_id
                                   THEN amazon_links.linked_at ELSE excluded.linked_at END
                """,
                (user_id, alexa_user_id, source, now),
            )
            self.db.commit()

    def touch_alexa_user(self, user_id: int, alexa_user_id: str) -> None:
        """Mémorise le compte Alexa qui interroge la skill (liaison faite depuis l'app Alexa)."""
        if not alexa_user_id.startswith("amzn1."):
            return
        row = self.db.execute(
            "SELECT alexa_user_id FROM amazon_links WHERE user_id = ?", (user_id,)
        ).fetchone()
        if not row or row["alexa_user_id"] != alexa_user_id:
            self.record_amazon_link(user_id, alexa_user_id, "alexa_app")
        with self._lock:
            self.db.execute(
                "UPDATE amazon_links SET last_seen_at = ? WHERE user_id = ?",
                (int(time.time()), user_id),
            )
            self.db.commit()

    def amazon_link(self, user_id: int) -> dict[str, Any] | None:
        row = self.db.execute(
            "SELECT alexa_user_id, source, linked_at, last_seen_at FROM amazon_links WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        return dict(row) if row else None

    def ecole_directe_info(self, user_id: int) -> dict[str, str | None] | None:
        row = self.db.execute(
            "SELECT student_id, updated_at FROM ecole_directe_connections WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if not row:
            return None
        stamp = int(row["updated_at"])
        if stamp > 10**11:  # microsecondes depuis la version qui sert de jeton de cache
            stamp //= 1_000_000
        return {"student_id": row["student_id"], "updated_at": str(stamp)}

    def delete_ecole_directe_connection(self, user_id: int) -> None:
        with self._lock:
            self.db.execute("DELETE FROM ecole_directe_connections WHERE user_id = ?", (user_id,))
            self.db.execute("DELETE FROM alexa_tokens WHERE user_id = ?", (user_id,))
            self.db.execute("DELETE FROM amazon_links WHERE user_id = ?", (user_id,))
            self.db.commit()

    def issue_csrf(self, session: str, ttl: int = 86_400) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            self.db.execute(
                "INSERT OR REPLACE INTO csrf_tokens VALUES (?, ?, ?)",
                (self._hash_token(session), self._hash_token(token), int(time.time()) + ttl),
            )
            self.db.commit()
        return token

    def check_csrf(self, session: str, token: str) -> bool:
        if not session or not token:
            return False
        with self._lock:
            row = self.db.execute(
                "SELECT token_hash, expires_at FROM csrf_tokens WHERE session_hash = ?",
                (self._hash_token(session),),
            ).fetchone()
        return bool(
            row
            and row["expires_at"] > int(time.time())
            and hmac.compare_digest(row["token_hash"], self._hash_token(token))
        )

    def purge_expired(self, limit: int = 100) -> None:
        with self._lock:
            now = int(time.time())
            for table, column in (
                ("sessions", "expires_at"),
                ("alexa_tokens", "expires_at"),
                ("oauth_codes", "expires_at"),
                ("csrf_tokens", "expires_at"),
            ):
                self.db.execute(
                    f"DELETE FROM {table} WHERE rowid IN "
                    f"(SELECT rowid FROM {table} WHERE {column} <= ? LIMIT ?)",
                    (now, limit),
                )
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
            "amazon": self.amazon_link(user_id),
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

    def upcoming_homework(self) -> dict[str, Any]:
        # Résumé des devoirs à venir indexé par date (endpoint validé dans web.py).
        if not self.client.student_id:
            raise EcoleDirecteError("Aucun élève n'est associé à cette session.")
        return self.client.get(f"Eleves/{self.client.student_id}/cahierdetexte.awp?verbe=get")

    def grades(self) -> dict[str, Any]:
        return self.client.get(self._student_endpoint("notes.awp?verbe=get"), anneeScolaire="")


def _now() -> datetime:
    try:
        from zoneinfo import ZoneInfo

        zone = os.environ.get("ECO_TO_ALEXA_TIMEZONE", "Europe/Paris")
        return datetime.now(ZoneInfo(zone)).replace(tzinfo=None)
    except Exception:  # tzdata absent (Windows) : heure locale de la machine
        return datetime.now()


class AlexaSkill:
    """Maps authenticated Alexa intents to the current user's service only."""

    HELP = (
        "Tu peux me demander : quel est mon emploi du temps demain, "
        "c'est quoi mon prochain cours, quels sont mes devoirs pour lundi, "
        "mes dernières notes, mes prochaines évaluations, mes absences "
        "ou mes nouveaux messages."
    )
    REPROMPT = "Que veux-tu savoir ? Par exemple : mes devoirs pour demain."
    CACHE_TTL = 900

    def __init__(
        self,
        store: AccountStore,
        services: dict[int, EcoleDirecteService] | None = None,
        clock: Any = _now,
    ) -> None:
        self.store = store
        # Services injectés (tests) : utilisés tels quels, sans rechargement.
        self.services = services if services is not None else {}
        self.clock = clock
        # Cache des clients connectés : user_id -> (version, expiration, service).
        self._cache: dict[int, tuple[int, float, EcoleDirecteService]] = {}
        self._cache_lock = threading.Lock()

    @classmethod
    def from_store(cls, store: AccountStore) -> "AlexaSkill":
        # Les services sont chargés à la demande : un compte lié après le
        # démarrage du serveur est donc pris en compte sans redémarrage.
        return cls(store)

    def _service_for(self, user_id: int) -> EcoleDirecteService | None:
        if user_id in self.services:
            return self.services[user_id]
        if self.store._fernet is None:
            return None
        # La version est relue à chaque requête : une déconnexion ou une
        # reconnexion École Directe prend effet immédiatement.
        version = self.store.ecole_directe_version(user_id)
        now = time.monotonic()
        with self._cache_lock:
            cached = self._cache.get(user_id)
            if version is None:
                self._cache.pop(user_id, None)
                return None
            if cached and cached[0] == version and cached[1] > now:
                return cached[2]
        # Alexa attend une réponse en moins de 8 secondes.
        client = self.store.ecole_directe_client(user_id, timeout=6)
        if client is None:
            return None
        service = EcoleDirecteService(client)
        with self._cache_lock:
            self._cache[user_id] = (version, now + self.CACHE_TTL, service)
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
        alexa_user = (
            ((request.get("context") or {}).get("System") or {}).get("user") or {}
        ).get("userId") or ((request.get("session") or {}).get("user") or {}).get("userId")
        if alexa_user:
            self.store.touch_alexa_user(user_id, str(alexa_user))
        return self.handle_for_user(user_id, request)

    def handle_for_user(self, user_id: int, request: dict[str, Any]) -> dict[str, Any]:
        """Répond pour un utilisateur déjà authentifié (webhook ou simulateur du site)."""
        body = request.get("request", {})
        kind = body.get("type")
        intent = body.get("intent", {})
        name = intent.get("name")
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
        except EcoleDirecteQcmRequired:
            text = (
                "École Directe demande de répondre à une question de sécurité. "
                "Ouvre le site EC to Alexa, reconnecte ton compte École Directe "
                "et réponds à la question, puis redemande-moi."
            )
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
        if name in {"GetAssessmentsIntent", "GetUpcomingAssessmentsIntent"}:
            return describe_assessments(service.upcoming_homework(), today)
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

    def __init__(
        self,
        store: AccountStore,
        client_id: str,
        redirect_uri: str | list[str] | tuple[str, ...],
        client_secret: str = "",
    ) -> None:
        # Amazon utilise une URL de redirection différente selon la région du
        # compte Alexa : plusieurs URL peuvent donc être autorisées.
        uris = [redirect_uri] if isinstance(redirect_uri, str) else list(redirect_uri)
        if not client_id or not uris or not all(uri.startswith("https://") for uri in uris):
            raise ValueError("OAuth doit utiliser un client et une URL HTTPS.")
        self.store = store
        self.client_id = client_id
        self.redirect_uris = tuple(uris)
        self.redirect_uri = uris[0]
        self.client_secret = client_secret

    def authorize(self, email: str, password: str, redirect_uri: str | None = None) -> str:
        redirect_uri = redirect_uri or self.redirect_uri
        if redirect_uri not in self.redirect_uris:
            raise PermissionError("URL de redirection non autorisée.")
        user_id = self.store.user_for_login(email, password)
        return self.store.create_authorization_code(
            user_id, self.client_id, redirect_uri
        )

    def token(
        self, code: str, client_id: str, redirect_uri: str, client_secret: str = ""
    ) -> dict[str, Any]:
        if (
            not self.client_secret
            or not hmac.compare_digest(client_id, self.client_id)
            or not hmac.compare_digest(client_secret, self.client_secret)
            or redirect_uri not in self.redirect_uris
        ):
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
