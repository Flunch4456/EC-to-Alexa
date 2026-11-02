"""Site web EC to Alexa : comptes, liaison École Directe et état de la liaison Alexa.

Rendu HTML côté serveur, sans JavaScript. À placer derrière un reverse proxy
HTTPS ; le processus n'écoute que sur loopback sauf configuration explicite.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from application import (
    AccountStore,
    AlexaSkill,
    SharedRateLimiter,
    read_body,
    trusted_client_address,
)
from amazon_link import AmazonLinkError, AmazonSkillLinker, pkce_pair
from ecole_directe import EcoleDirecteClient, EcoleDirecteError, EcoleDirecteQcmRequired
import ui

RATE_LIMITER = SharedRateLimiter()

# Messages de confirmation après redirection (jamais de texte libre dans l'URL).
FLASH = {
    "welcome": "Compte créé. Il reste à connecter École Directe.",
    "login": "Connexion réussie.",
    "ed": "École Directe est connecté. Ton identifiant élève a été vérifié.",
    "ed-auto": "École Directe est reconnecté : la question de sécurité a été reconnue et remplie automatiquement.",
    "qcm-off": "Les réponses de sécurité mémorisées ont été oubliées.",
    "ed-off": "École Directe est déconnecté et Alexa n'a plus accès à tes données.",
    "alexa-off": "La liaison Alexa est révoquée. Alexa te demandera de lier ton compte à nouveau.",
    "amazon": "Ton compte Amazon est lié : la skill Mon Carnet Scolaire est activée sur tes appareils Alexa.",
    "bye": "Session fermée.",
    "deleted": "Ton compte et toutes ses données ont été supprimés.",
}

# Questions proposées dans le simulateur : (clé, libellé, intent, emplacements).
SIMULATIONS: list[tuple[str, str, str, dict[str, Any]]] = [
    ("today", "Mon emploi du temps aujourd'hui", "GetTodayScheduleIntent", {}),
    ("tomorrow", "Mon emploi du temps demain", "GetTomorrowScheduleIntent", {}),
    ("next", "Mon prochain cours", "GetNextClassIntent", {}),
    ("homework", "Mes devoirs pour le prochain jour de cours", "GetHomeworkIntent", {}),
    ("tests", "Mes prochaines évaluations", "GetAssessmentsIntent", {}),
    ("grades", "Mes dernières notes", "GetGradesIntent", {}),
    ("absences", "Mes absences et retards", "GetAbsencesIntent", {}),
    ("messages", "Mes nouveaux messages", "GetMessagesIntent", {}),
]


class PendingQcm:
    """QCM École Directe en attente de réponse, gardé en mémoire 5 minutes au plus."""

    TTL = 300
    MAX = 200

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, tuple[float, EcoleDirecteClient, dict[str, Any]]] = {}

    @staticmethod
    def _key(session: str) -> str:
        return hashlib.sha256(session.encode()).hexdigest()

    def put(self, session: str, client: EcoleDirecteClient, question: dict[str, Any]) -> None:
        now = time.monotonic()
        with self._lock:
            self._items = {k: v for k, v in self._items.items() if v[0] > now}
            if len(self._items) >= self.MAX:
                self._items.pop(next(iter(self._items)))
            self._items[self._key(session)] = (now + self.TTL, client, question)

    def get(self, session: str) -> tuple[EcoleDirecteClient, dict[str, Any]] | None:
        with self._lock:
            item = self._items.get(self._key(session))
        if not item or item[0] <= time.monotonic():
            return None
        return item[1], item[2]

    def pop(self, session: str) -> None:
        with self._lock:
            self._items.pop(self._key(session), None)


class PendingAmazon:
    """Liaisons Amazon en cours (state OAuth → session, compte, vérificateur PKCE), 10 min."""

    TTL = 600
    MAX = 200

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, tuple[float, str, int, str]] = {}

    def put(self, state: str, session: str, user_id: int, verifier: str) -> None:
        now = time.monotonic()
        with self._lock:
            self._items = {k: v for k, v in self._items.items() if v[0] > now}
            if len(self._items) >= self.MAX:
                self._items.pop(next(iter(self._items)))
            self._items[state] = (now + self.TTL, PendingQcm._key(session), user_id, verifier)

    def pop(self, state: str, session: str) -> tuple[int, str] | None:
        with self._lock:
            item = self._items.pop(state, None)
        if not item or item[0] <= time.monotonic() or item[1] != PendingQcm._key(session):
            return None
        return item[2], item[3]


class AccountHandler(BaseHTTPRequestHandler):
    store: AccountStore
    skill: AlexaSkill
    pending = PendingQcm()
    linker: AmazonSkillLinker | None = None
    oauth_client_id: str = ""
    amazon_pending = PendingAmazon()

    # -- Routage ----------------------------------------------------------
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        flash = FLASH.get(query.get("ok", [""])[0], "")
        if path == "/healthz":
            self._send(200, "application/json", '{"status":"ok"}')
        elif path == ui.CSS_PATH:
            self._send(200, "text/css", ui.CSS, cache=True)
        elif path == "/":
            if self._user_id() is not None:
                self._redirect("/dashboard")
            else:
                self._send(200, "text/html", self._home(flash))
        elif path == "/dashboard":
            user_id = self._user_id()
            if user_id is None:
                self._redirect("/")
            else:
                self._send(200, "text/html", self._dashboard(user_id, flash))
        elif path == "/account/export":
            self._export()
        elif path == "/amazon/link":
            self._amazon_start(query)
        elif path == "/amazon/callback":
            self._amazon_callback(query)
        else:
            self._send(404, "text/html", ui.page("Page introuvable", self._not_found()))

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if not RATE_LIMITER.allow((trusted_client_address(self),), limit=30):
            self._send(429, "text/html", self._home("", "Trop de requêtes. Attends une minute."))
            return
        try:
            fields = self._fields()
        except (ValueError, UnicodeDecodeError):
            self._send(400, "text/html", self._home("", "Formulaire invalide."))
            return
        if not RATE_LIMITER.allow(("global",), limit=1000):
            self._send(429, "text/html", self._home("", "Le service est très sollicité. Réessaie dans une minute."))
            return
        if path in {"/login", "/register"}:
            self._public_form(path, fields)
            return
        session = self._cookie("session")
        try:
            user_id = self.store.user_for_token(session)
        except PermissionError:
            self._redirect("/")
            return
        if not self.store.check_csrf(session, fields.get("csrf", "")):
            self._send(403, "text/html", self._dashboard(user_id, error="Le formulaire a expiré. Recommence."))
            return
        actions = {
            "/connect-ecole-directe": self._connect,
            "/qcm": self._qcm,
            "/ecole-directe/verify": self._verify,
            "/ecole-directe/forget-qcm": self._forget_qcm,
            "/disconnect-ecole-directe": self._disconnect,
            "/alexa/unlink": self._unlink_alexa,
            "/simulate": self._simulate,
            "/logout": self._logout,
            "/account/delete": self._delete,
        }
        action = actions.get(path)
        if action is None:
            self._send(404, "text/html", ui.page("Page introuvable", self._not_found()))
            return
        try:
            action(user_id, session, fields)
        except (ValueError, RuntimeError) as error:
            self._send(400, "text/html", self._dashboard(user_id, error=str(error)))

    # -- Comptes ----------------------------------------------------------
    def _public_form(self, path: str, fields: dict[str, str]) -> None:
        email = fields.get("email", "").strip().lower()
        if not RATE_LIMITER.allow((f"account:{email or 'missing'}",), limit=5):
            self._send(429, "text/html", self._home("", "Trop d'essais pour ce compte. Attends une minute."))
            return
        try:
            if path == "/register":
                if fields.get("password", "") != fields.get("password2", ""):
                    raise ValueError("Les deux mots de passe ne correspondent pas.")
                user_id = self.store.register(email, fields.get("password", ""))
                self.store.audit(user_id, "account_created")
            token = self.store.login(email, fields.get("password", ""))
        except ValueError as error:
            self._send(400, "text/html", self._home("", str(error)))
            return
        except PermissionError:
            self._send(401, "text/html", self._home("", "E-mail ou mot de passe incorrect."))
            return
        self._set_cookie("session", token)
        self._redirect("/dashboard?ok=" + ("welcome" if path == "/register" else "login"))

    def _logout(self, user_id: int, session: str, fields: dict[str, str]) -> None:
        self.pending.pop(session)
        self.store.logout(session)
        self._set_cookie("session", "", max_age=0)
        self._redirect("/?ok=bye")

    def _delete(self, user_id: int, session: str, fields: dict[str, str]) -> None:
        if fields.get("confirm") != "oui":
            raise ValueError("Coche la case de confirmation pour supprimer ton compte.")
        self.pending.pop(session)
        self.store.delete_account(user_id)
        self._set_cookie("session", "", max_age=0)
        self._redirect("/?ok=deleted")

    def _export(self) -> None:
        user_id = self._user_id()
        if user_id is None:
            self._redirect("/")
            return
        body = json.dumps(self.store.export_account(user_id), ensure_ascii=False, indent=2)
        self._send(
            200, "application/json", body,
            extra={"Content-Disposition": 'attachment; filename="ec-to-alexa-compte.json"'},
        )

    # -- École Directe ----------------------------------------------------
    def _connect(self, user_id: int, session: str, fields: dict[str, str]) -> None:
        identifiant = fields.get("identifiant", "").strip()
        password = fields.get("ecole_password", "")
        if not identifiant or not password:
            raise ValueError("Saisis ton identifiant et ton mot de passe École Directe.")
        client = self.store.attach_qcm_memory(user_id, EcoleDirecteClient(identifiant, password))
        self._login_or_ask(user_id, session, client, automatic="ed-auto")

    def _verify(self, user_id: int, session: str, fields: dict[str, str]) -> None:
        """Reconnexion avec les identifiants enregistrés, quand Alexa a rencontré une question."""
        client = self.store.ecole_directe_client(user_id)
        if client is None:
            raise ValueError("Connecte d'abord École Directe.")
        self._login_or_ask(user_id, session, client)

    def _login_or_ask(
        self, user_id: int, session: str, client: EcoleDirecteClient, automatic: str = "ed-auto"
    ) -> None:
        """Connecte le client ; répond seul au QCM si la réponse est mémorisée, sinon le pose."""
        try:
            client.ensure_login()
        except EcoleDirecteQcmRequired as required:
            try:
                question = required.question if client.qcm_token else None
                if not question:
                    # Réponse mémorisée refusée (jeton QCM consommé) : nouvelle question.
                    client.login()
                    question = client.get_qcm().get("data") or {}
            except EcoleDirecteError as error:
                raise ValueError(f"École Directe a refusé la connexion : {error}") from error
            if required.known:
                self.store.audit(user_id, "qcm_answer_forgotten")
            self.pending.put(session, client, question)
            self._send(200, "text/html", self._dashboard(user_id))
            return
        except EcoleDirecteError as error:
            raise ValueError(f"École Directe a refusé la connexion : {error}") from error
        self._save_connection(user_id, client, flash=automatic if client.qcm_auto_answered else "ed")

    def _qcm(self, user_id: int, session: str, fields: dict[str, str]) -> None:
        pending = self.pending.get(session)
        if pending is None:
            raise ValueError("La question de sécurité a expiré. Recommence la connexion École Directe.")
        client, question = pending
        choice = fields.get("choix", "")
        if choice not in [str(value) for value in question.get("propositions") or []]:
            raise ValueError("Choisis une des réponses proposées.")
        try:
            client.complete_qcm(choice)
        except EcoleDirecteError as error:
            self.pending.pop(session)
            raise ValueError(f"Réponse refusée par École Directe : {error}") from error
        self.pending.pop(session)
        # Réponse acceptée : mémorisée (hachée) pour y répondre seul la prochaine fois.
        self.store.remember_qcm_answer(
            user_id, client.identifiant, str(question.get("question") or ""), choice
        )
        self._save_connection(user_id, client)

    def _forget_qcm(self, user_id: int, session: str, fields: dict[str, str]) -> None:
        self.store.forget_all_qcm_answers(user_id)
        self.store.audit(user_id, "qcm_answers_forgotten")
        self._redirect("/dashboard?ok=qcm-off")

    def _save_connection(self, user_id: int, client: EcoleDirecteClient, flash: str = "ed") -> None:
        if not client.token or not client.student_id:
            raise ValueError("École Directe n'a pas renvoyé d'identifiant élève vérifiable.")
        self.store.save_ecole_directe_connection(
            user_id,
            client.identifiant,
            client.mot_de_passe,
            client.student_id,
            device_uuid=client.device_uuid,
            cn=client.cn,
            cv=client.cv,
        )
        self.store.audit(user_id, "ecole_directe_connected")
        self._redirect(f"/dashboard?ok={flash}")

    def _disconnect(self, user_id: int, session: str, fields: dict[str, str]) -> None:
        self.pending.pop(session)
        self.store.delete_ecole_directe_connection(user_id)
        self.store.audit(user_id, "ecole_directe_disconnected")
        self._redirect("/dashboard?ok=ed-off")

    # -- Alexa ------------------------------------------------------------
    def _unlink_alexa(self, user_id: int, session: str, fields: dict[str, str]) -> None:
        self.store.revoke_alexa_for_user(user_id)
        self.store.audit(user_id, "alexa_unlinked")
        self._redirect("/dashboard?ok=alexa-off")

    def _amazon_start(self, query: dict[str, list[str]]) -> None:
        """Lien « Lier mon compte Amazon » : part vers Login with Amazon (GET + jeton CSRF)."""
        session = self._cookie("session")
        user_id = self._user_id()
        if user_id is None:
            self._redirect("/")
            return
        if not self.store.check_csrf(session, query.get("csrf", [""])[0]):
            self._send(403, "text/html", self._dashboard(user_id, error="Le lien a expiré. Recommence."))
            return
        if self.linker is None or not self.oauth_client_id:
            self._send(503, "text/html", self._dashboard(
                user_id, error="La liaison Amazon depuis le site n'est pas encore configurée sur le serveur."))
            return
        if not self.store.has_ecole_directe_connection(user_id):
            self._send(400, "text/html", self._dashboard(
                user_id, error="Connecte d'abord École Directe (étape 2)."))
            return
        state = secrets.token_urlsafe(24)
        verifier, challenge = pkce_pair()
        self.amazon_pending.put(state, session, user_id, verifier)
        self._redirect(self.linker.authorization_url(state, challenge))

    def _amazon_callback(self, query: dict[str, list[str]]) -> None:
        session = self._cookie("session")
        user_id = self._user_id()
        if user_id is None:
            self._redirect("/")
            return
        pending = self.amazon_pending.pop(query.get("state", [""])[0], session)
        if pending is None or pending[0] != user_id or self.linker is None:
            self._send(400, "text/html", self._dashboard(
                user_id, error="La liaison Amazon a expiré ou ne vient pas de cette session. Recommence."))
            return
        if query.get("error"):
            self._send(400, "text/html", self._dashboard(
                user_id, error="Liaison annulée sur la page Amazon. Tu peux recommencer quand tu veux."))
            return
        try:
            amazon_token = self.linker.exchange_code(query.get("code", [""])[0], pending[1])
            # Code OAuth à usage unique pour CE compte : Amazon l'échange sur /oauth/token.
            auth_code = self.store.create_authorization_code(
                user_id, self.oauth_client_id, self.linker.redirect_uri
            )
            result = self.linker.enable_and_link(amazon_token, auth_code)
        except AmazonLinkError as error:
            self._send(502, "text/html", self._dashboard(user_id, error=str(error)))
            return
        alexa_user = str((result.get("user") or {}).get("id") or "")
        if alexa_user:
            self.store.record_amazon_link(user_id, alexa_user, "site")
        self.store.audit(user_id, "amazon_linked")
        print(f"[ec-to-alexa] compte Amazon lié depuis le site pour le compte interne {user_id}", flush=True)
        self._redirect("/dashboard?ok=amazon")

    def _simulate(self, user_id: int, session: str, fields: dict[str, str]) -> None:
        choice = next((s for s in SIMULATIONS if s[0] == fields.get("question")), None)
        if choice is None:
            raise ValueError("Choisis une question à poser.")
        _, label, intent, slots = choice
        response = self.skill.handle_for_user(
            user_id,
            {"request": {"type": "IntentRequest", "intent": {"name": intent, "slots": slots}}},
        )
        ssml = response["response"]["outputSpeech"]["ssml"]
        spoken = re.sub(r"</?speak>", "", ssml)
        self._send(200, "text/html", self._dashboard(user_id, simulated=(choice[0], label, spoken)))

    # -- Pages ------------------------------------------------------------
    def _home(self, flash: str = "", error: str = "") -> str:
        body = f"""
<header class="masthead">
  <p class="brand">EC to Alexa</p>
  <h1>Ton cahier de texte, lu à voix haute.</h1>
  <p class="lede">Relie ton compte École Directe à Alexa, puis demande-lui ton
  emploi du temps, tes devoirs ou tes dernières notes.</p>
  <p class="say">Alexa, demande à {ui.INVOCATION} mes devoirs pour demain</p>
</header>
{ui.notice(flash)}{ui.notice(error, "error")}
<div class="two">
  <section class="panel">
    <h2>Se connecter</h2>
    <form method="post" action="/login" class="stack">
      <label>E-mail <input name="email" type="email" autocomplete="username" required></label>
      <label>Mot de passe <input name="password" type="password" autocomplete="current-password" required></label>
      <button type="submit">Se connecter</button>
    </form>
  </section>
  <section class="panel">
    <h2>Créer un compte</h2>
    <form method="post" action="/register" class="stack">
      <label>E-mail <input name="email" type="email" autocomplete="email" required></label>
      <label>Mot de passe (12 caractères minimum)
        <input name="password" type="password" minlength="12" autocomplete="new-password" required></label>
      <label>Confirme le mot de passe
        <input name="password2" type="password" minlength="12" autocomplete="new-password" required></label>
      <button type="submit">Créer mon compte</button>
    </form>
    <p class="aside">Ce compte est propre à EC to Alexa. Ton mot de passe École Directe
    se saisit à l'étape suivante.</p>
  </section>
</div>"""
        return ui.page("Accueil", body)

    def _dashboard(
        self,
        user_id: int,
        flash: str = "",
        error: str = "",
        simulated: tuple[str, str, str] | None = None,
    ) -> str:
        session = self._cookie("session")
        csrf = ui.esc(self.store.issue_csrf(session))
        hidden = f'<input type="hidden" name="csrf" value="{csrf}">'
        account = self.store.export_account(user_id)
        info = self.store.ecole_directe_info(user_id)
        alexa_links = self.store.alexa_link_count(user_id)
        amazon = self.store.amazon_link(user_id)
        pending = self.pending.get(session)

        # Étape 1 : compte
        step_account = f"""
<li class="done"><h2>Ton compte <span class="status">fait</span></h2>
  <p>Connecté en tant que <strong>{ui.esc(account['email'])}</strong>.</p></li>"""

        # Étape 2 : École Directe
        remembered = self.store.qcm_answer_count(user_id)
        memory_note = (
            f"""
  <p class="aside">Réponses de sécurité mémorisées : <strong>{remembered}</strong>. Elles sont
  hachées (illisibles, même pour le serveur) et servent seulement à reconnaître la bonne
  proposition quand École Directe repose la même question.</p>
  <form method="post" action="/ecole-directe/forget-qcm">{hidden}
    <button class="quiet" type="submit">Oublier ces réponses</button></form>"""
            if remembered else ""
        )
        if pending:
            question = pending[1]
            options = "".join(
                f'<label class="choice"><input type="radio" name="choix" value="{ui.esc(value)}" required> {ui.esc(value)}</label>'
                for value in question.get("propositions") or []
            )
            step_ed = f"""
<li><h2>École Directe <span class="status">question de sécurité</span></h2>
  <p>École Directe veut vérifier que c'est bien toi. Réponds à sa question pour terminer
  la connexion. Ta réponse est mémorisée sous forme hachée : quand École Directe reposera
  cette question, Alexa y répondra toute seule.</p>
  <form method="post" action="/qcm" class="stack">{hidden}
    <fieldset><legend>{ui.esc(question.get('question') or 'Question de sécurité')}</legend>{options}</fieldset>
    <button type="submit">Valider ma réponse</button>
  </form></li>"""
        elif info and info.get("qcm_required_at"):
            asked = datetime.fromtimestamp(int(info["qcm_required_at"])).strftime("%d/%m/%Y à %H:%M")
            step_ed = f"""
<li><h2>École Directe <span class="status">question à répondre</span></h2>
  <p>Le {asked}, École Directe a posé à Alexa une question de sécurité qu'elle ne connaissait
  pas encore. Réponds-y une fois ici : Alexa s'en souviendra pour les prochaines fois.</p>
  <form method="post" action="/ecole-directe/verify">{hidden}
    <button type="submit">Répondre à la question de sécurité</button></form>{memory_note}</li>"""
        elif info:
            since = datetime.fromtimestamp(int(info["updated_at"] or 0)).strftime("%d/%m/%Y à %H:%M")
            step_ed = f"""
<li class="done"><h2>École Directe <span class="status">connecté</span></h2>
  <dl class="facts"><dt>Identifiant élève</dt><dd>{ui.esc(info['student_id'])}</dd>
  <dt>Vérifié le</dt><dd>{since}</dd></dl>
  <p>Ton mot de passe est chiffré sur le serveur et n'est jamais affiché.</p>{memory_note}
  <form method="post" action="/disconnect-ecole-directe">{hidden}
    <button class="danger" type="submit">Déconnecter École Directe</button></form></li>"""
        else:
            step_ed = f"""
<li><h2>École Directe <span class="status">à connecter</span></h2>
  <p>Saisis les identifiants que tu utilises sur ecoledirecte.com. Ils sont vérifiés
  auprès d'École Directe puis chiffrés.</p>
  <form method="post" action="/connect-ecole-directe" class="stack">{hidden}
    <label>Identifiant École Directe <input name="identifiant" type="text" autocomplete="off" required></label>
    <label>Mot de passe École Directe <input name="ecole_password" type="password" autocomplete="off" required></label>
    <button type="submit">Connecter École Directe</button>
  </form></li>"""

        # Étape 3 : Alexa
        if alexa_links:
            if amazon:
                linked = datetime.fromtimestamp(int(amazon["linked_at"])).strftime("%d/%m/%Y à %H:%M")
                seen = (
                    datetime.fromtimestamp(int(amazon["last_seen_at"])).strftime("%d/%m/%Y à %H:%M")
                    if amazon.get("last_seen_at") else "pas encore"
                )
                origin = "depuis ce site" if amazon["source"] == "site" else "depuis l'application Alexa"
                facts = f"""
  <dl class="facts"><dt>Compte Alexa</dt><dd>…{ui.esc(str(amazon['alexa_user_id'])[-8:])}</dd>
  <dt>Lié le</dt><dd>{linked} ({origin})</dd>
  <dt>Dernière demande</dt><dd>{seen}</dd></dl>
  <p>Toutes les demandes de ce compte Alexa utilisent ton École Directe
  (identifiant élève {ui.esc((info or {}).get('student_id') or '?')}).</p>"""
            else:
                facts = """
  <p>Le compte Alexa sera identifié ici dès sa première demande.</p>"""
            step_alexa = f"""
<li class="done"><h2>Alexa <span class="status">liée</span></h2>{facts}
  <p>Essaie maintenant sur ton enceinte :</p>
  <p class="say">Alexa, demande à {ui.INVOCATION} mon prochain cours</p>
  <form method="post" action="/alexa/unlink">{hidden}
    <button class="quiet" type="submit">Révoquer l'accès d'Alexa</button></form></li>"""
        elif self.linker is not None and info:
            # La liaison se fait sur ce site : c'est ici que l'on choisit quel
            # compte Amazon lit quel École Directe.
            step_alexa = f"""
<li><h2>Alexa <span class="status">à lier</span></h2>
  <p>Relie ton compte Amazon (celui de ton Echo) à ce compte EC to Alexa. Tu te
  connectes sur la page officielle d'Amazon, puis la skill « Mon Carnet Scolaire »
  est activée et liée automatiquement.</p>
  <p><a class="button" href="/amazon/link?csrf={csrf}">Lier mon compte Amazon</a></p>
  <p class="aside">Le compte Amazon lié est ensuite affiché ici. Pour le changer,
  révoque l'accès puis lie un autre compte.</p></li>"""
        elif self.linker is not None:
            step_alexa = """
<li><h2>Alexa <span class="status">en attente</span></h2>
  <p>Connecte d'abord École Directe (étape 2) : le bouton « Lier mon compte Amazon »
  apparaîtra ici.</p></li>"""
        else:
            step_alexa = f"""
<li><h2>Alexa <span class="status">à lier</span></h2>
  <p>Dans l'application Alexa, ouvre <strong>Plus › Skills et jeux</strong>, cherche
  la skill « Mon Carnet Scolaire » (onglet <em>Vos skills › Développeur</em> tant qu'elle
  n'est pas publiée), puis touche <strong>Activer</strong>. Alexa t'enverra sur une page
  de connexion : utilise ce même compte EC to Alexa.</p>
  <p>Ou, directement à la voix :</p>
  <p class="say">Alexa, ouvre {ui.INVOCATION}</p></li>"""

        # Simulateur
        options = "".join(
            f'<option value="{key}"{" selected" if simulated and simulated[0] == key else ""}>{ui.esc(label)}</option>'
            for key, label, _, _ in SIMULATIONS
        )
        answer = ""
        if simulated:
            answer = (
                f'<div class="answer" role="status"><span class="who">Alexa répondrait :</span>'
                f"{ui.esc(simulated[2])}</div>"
            )
        simulator = f"""
<section class="panel" id="essai">
  <h2>Essayer sans enceinte</h2>
  <p>Pose une question comme le ferait Alexa : la réponse est calculée avec tes vraies
  données École Directe, exactement comme sur l'enceinte.</p>
  <form method="post" action="/simulate#essai" class="row">{hidden}
    <label>Question <select name="question">{options}</select></label>
    <button type="submit">Demander</button>
  </form>{answer}
</section>""" if info else ""

        body = f"""
<header class="masthead">
  <div class="brand">EC to Alexa
    <form method="post" action="/logout">{hidden}<button class="link" type="submit">Se déconnecter</button></form></div>
  <h1>Ton carnet scolaire sur Alexa</h1>
  <p class="lede">Trois étapes, une seule fois. Ensuite, il suffit de parler.</p>
</header>
{ui.notice(flash)}{ui.notice(error, "error")}
<ol class="steps">{step_account}{step_ed}{step_alexa}</ol>
{simulator}
<section class="panel">
  <h2>Tes données</h2>
  <p><a href="/account/export">Télécharger les informations de mon compte</a> (fichier JSON).</p>
  <details><summary>Supprimer mon compte</summary>
    <form method="post" action="/account/delete" class="stack">{hidden}
      <p>Supprime ton compte EC to Alexa, la liaison École Directe chiffrée et l'accès d'Alexa.
      Ton compte École Directe lui-même n'est pas touché.</p>
      <label class="check"><input type="checkbox" name="confirm" value="oui" required>
        Je comprends que cette suppression est définitive.</label>
      <button class="danger" type="submit">Supprimer définitivement</button>
    </form></details>
</section>"""
        return ui.page("Tableau de bord", body)

    @staticmethod
    def _not_found() -> str:
        return (
            '<header class="masthead"><p class="brand">EC to Alexa</p>'
            "<h1>Cette page n'existe pas.</h1>"
            '<p class="lede"><a href="/">Revenir à l\'accueil</a></p></header>'
        )

    # -- HTTP -------------------------------------------------------------
    def _user_id(self) -> int | None:
        session = self._cookie("session")
        if not session:
            return None
        try:
            return self.store.user_for_token(session)
        except PermissionError:
            return None

    def _fields(self) -> dict[str, str]:
        body = read_body(self, "application/x-www-form-urlencoded")
        if not body:
            return {}
        return {
            key: values[0]
            for key, values in parse_qs(body.decode("utf-8", "strict"), strict_parsing=True).items()
        }

    def _cookie(self, name: str) -> str:
        for item in self.headers.get("Cookie", "").split(";"):
            key, _, value = item.strip().partition("=")
            if key == name:
                return value
        return ""

    def _set_cookie(self, name: str, value: str, max_age: int = 86_400) -> None:
        flags = f"{name}={value}; Max-Age={max_age}; HttpOnly; SameSite=Lax; Path=/"
        if os.environ.get("ECO_TO_ALEXA_SECURE_COOKIES", "1") != "0":
            flags += "; Secure"
        self._pending_cookie = flags

    def _redirect(self, location: str) -> None:
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self._headers()
        self.end_headers()

    def _send(
        self,
        status: int,
        content_type: str,
        body: str,
        cache: bool = False,
        extra: dict[str, str] | None = None,
    ) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self._headers(cache)
        self.end_headers()
        self.wfile.write(encoded)

    def _headers(self, cache: bool = False) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'self'; form-action 'self'; "
            "frame-ancestors 'none'; base-uri 'none'",
        )
        self.send_header("Cache-Control", "public, max-age=3600" if cache else "no-store")
        cookie = getattr(self, "_pending_cookie", None)
        if cookie:
            self.send_header("Set-Cookie", cookie)
            self._pending_cookie = None

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> None:
    from ecole_directe import load_dotenv

    load_dotenv()
    key = os.environ.get("ECO_TO_ALEXA_SECRET_KEY", "")
    if not key:
        raise SystemExit("Configure ECO_TO_ALEXA_SECRET_KEY avec une clé Fernet.")
    store = AccountStore(os.environ.get("ECO_TO_ALEXA_DB", "ec_to_alexa.sqlite3"), key.encode())
    AccountHandler.store = store
    AccountHandler.skill = AlexaSkill.from_store(store)
    AccountHandler.linker = AmazonSkillLinker.from_env(os.environ)
    AccountHandler.oauth_client_id = os.environ.get("ALEXA_OAUTH_CLIENT_ID", "").strip()
    if AccountHandler.linker is None:
        print("Liaison Amazon depuis le site désactivée : renseignez ALEXA_APP_CLIENT_ID et ALEXA_APP_CLIENT_SECRET.")
    host = os.environ.get("ECO_TO_ALEXA_HOST", "127.0.0.1")
    port = int(os.environ.get("ECO_TO_ALEXA_ACCOUNT_PORT", "8000"))
    server = ThreadingHTTPServer((host, port), AccountHandler)
    print(f"Site EC to Alexa disponible sur http://{host}:{server.server_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        store.close()


if __name__ == "__main__":
    main()
