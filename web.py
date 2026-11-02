"""Interface web locale pour lancer la connexion Ecole Directe."""

import html
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

from ecole_directe import EcoleDirecteClient, EcoleDirecteError, redact_response



HOST = os.environ.get("ECO_TO_ALEXA_WEB_BIND", "127.0.0.1")
PORT = 8000
MAX_HTTP_BODY = 32_768
CHALLENGE_TTL_SECONDS = 300
SESSION_TTL_SECONDS = 1_800
MAX_CACHE_ENTRIES = 512
CSRF_SECRET = hashlib.sha256(
    os.environ.get("ECO_TO_ALEXA_CSRF_SECRET", "local-dev-csrf").encode("utf-8")
).digest()
LOCK = threading.Lock()
CHALLENGES: dict[str, dict[str, object]] = {}
SESSIONS: dict[str, dict[str, object]] = {}
LOGIN_ATTEMPTS: dict[str, list[float]] = {}
SERVICES = {
    "emploi": ("Emploi du temps", "E/{student_id}/emploidutemps.awp?verbe=get&v=1.8.28"),
    "cahier": ("Cahier de texte", "Eleves/{student_id}/cahierdetexte.awp?verbe=get"),
    "notes": ("Notes", "eleves/{student_id}/notes.awp?verbe=get"),
    "absences": ("Absences", "eleves/{student_id}/viescolaire.awp?verbe=get"),
    "messages": ("Messages", "eleves/{student_id}/messages.awp?verbe=get"),
}


PAGE = """<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>EC to Alexa | Connexion</title>
  <style>
    :root { color-scheme: light; --ink: #17221d; --muted: #65726a; --line: #d7dfd9; --paper: #f5f7f2; --accent: #d96b3b; --accent-dark: #a74420; }
    * { box-sizing: border-box; }
    body { margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 24px; color: var(--ink); background: radial-gradient(circle at 12% 10%, #e7efe4, transparent 32%), linear-gradient(135deg, #f8f5ed, var(--paper)); font: 16px/1.5 Georgia, serif; }
    main { width: min(100%, 920px); display: grid; grid-template-columns: .9fr 1.1fr; overflow: hidden; border: 1px solid var(--line); background: rgba(255,255,255,.82); box-shadow: 0 22px 70px rgba(48, 65, 53, .14); }
    .intro { padding: clamp(28px, 6vw, 68px); background: #29483b; color: #f6f2e9; }
    .eyebrow { margin: 0 0 42px; color: #f4a276; font: 700 12px/1.2 Arial, sans-serif; letter-spacing: .14em; text-transform: uppercase; }
    h1 { max-width: 330px; margin: 0; font-size: clamp(38px, 6vw, 68px); line-height: .96; font-weight: 400; }
    .intro p { max-width: 290px; margin: 26px 0 0; color: #d4e0d6; font-size: 17px; }
    .form-panel { padding: clamp(28px, 6vw, 68px); }
    h2 { margin: 0 0 8px; font-size: 30px; font-weight: 400; }
    .hint { margin: 0 0 32px; color: var(--muted); font: 14px/1.5 Arial, sans-serif; }
    label { display: block; margin: 18px 0 7px; color: var(--muted); font: 700 12px/1.2 Arial, sans-serif; letter-spacing: .08em; text-transform: uppercase; }
    input { width: 100%; padding: 14px 0; border: 0; border-bottom: 1px solid var(--line); outline: 0; background: transparent; color: var(--ink); font: 18px Georgia, serif; }
    input:focus { border-color: var(--accent); }
    button { width: 100%; margin-top: 30px; padding: 15px 18px; border: 0; background: var(--accent); color: white; cursor: pointer; font: 700 14px Arial, sans-serif; letter-spacing: .08em; text-transform: uppercase; }
    button:hover { background: var(--accent-dark); }
    button:disabled { cursor: wait; opacity: .65; }
    .qcm { margin-top: 28px; padding: 20px; border-left: 3px solid var(--accent); background: #fff8ef; font: 15px/1.5 Arial, sans-serif; }
    .qcm-title { margin: 0 0 8px; color: var(--accent-dark); font-weight: 700; }
    .qcm label { display: block; margin: 12px 0; font-weight: 400; letter-spacing: 0; text-transform: none; }
    .qcm input { width: auto; margin-right: 7px; }
    .services { margin-top: 28px; }
    .services-title { margin: 0 0 14px; font: 700 12px/1.2 Arial, sans-serif; letter-spacing: .08em; text-transform: uppercase; }
    .service-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; }
    .service-grid button { margin-top: 0; text-transform: none; letter-spacing: 0; }
    .result { margin-top: 28px; padding: 16px; overflow: auto; background: #18251f; color: #d9efdb; font: 13px/1.5 Consolas, monospace; white-space: pre-wrap; }
    .error { background: #fff0eb; color: #8e321c; }
    @media (max-width: 700px) { main { grid-template-columns: 1fr; } .intro { padding: 32px; } .eyebrow { margin-bottom: 24px; } .intro p { margin-top: 18px; } }
  </style>
</head>
<body>
  <main>
    <section class="intro">
      <p class="eyebrow">EC to Alexa</p>
      <h1>Votre espace scolaire, simplement.</h1>
      <p>Connectez-vous à Ecole Directe pour récupérer votre réponse API.</p>
    </section>
    <section class="form-panel">
      <h2>Connexion</h2>
      <p class="hint">Les identifiants sont transmis uniquement au processus local et ne sont pas enregistrés.</p>
      <form method="post" action="/login">
        {csrf}
        <label for="identifiant">Identifiant</label>
        <input id="identifiant" name="identifiant" autocomplete="username" required>
        <label for="mot_de_passe">Mot de passe</label>
        <input id="mot_de_passe" name="mot_de_passe" type="password" autocomplete="current-password" required>
        <button type="submit">Se connecter</button>
      </form>
    {qcm}
    {services}
      {result}
    </section>
  </main>
</body>
</html>"""


def render_qcm(challenge_id: str, csrf: str, data: dict[str, object]) -> str:
    question = html.escape(str(data.get("question", "Question de sécurité")))
    propositions = data.get("propositions") or []
    options = "".join(
        f'<label><input type="radio" name="choix" value="{html.escape(str(value), quote=True)}" required>'
        f" {html.escape(str(value))}</label>"
        for value in propositions
    )
    return (
        '<section class="qcm"><p class="qcm-title">Vérification de sécurité</p>'
        f"<p>{question}</p>"
        f'<form method="post" action="/qcm"><input type="hidden" name="csrf" value="{html.escape(csrf, quote=True)}"><input type="hidden" name="challenge" '
        f'value="{html.escape(challenge_id, quote=True)}">{options}'
        '<button type="submit">Valider la réponse</button></form></section>'
    )


def render_services(session_id: str, csrf: str) -> str:
    buttons = "".join(
        f'<button type="submit" name="service" value="{key}">{html.escape(label)}</button>'
        for key, (label, _) in SERVICES.items()
    )
    return (
        '<section class="services"><p class="services-title">Que voulez-vous consulter ?</p>'
        f'<form method="post" action="/service"><input type="hidden" name="csrf" value="{html.escape(csrf, quote=True)}"><input type="hidden" name="session" '
        f'value="{html.escape(session_id, quote=True)}"><div class="service-grid">{buttons}'
        '</div></form></section>'
    )


def render(
    result: str = "",
    error: bool = False,
    qcm: str = "",
    services: str = "",
    csrf: str = "",
) -> bytes:
    result_html = ""
    if result:
        result_html = f'<pre class="result{" error" if error else ""}">{html.escape(result)}</pre>'
    csrf_html = f'<input type="hidden" name="csrf" value="{html.escape(csrf, quote=True)}">' if csrf else ""
    return (
        PAGE.replace("{qcm}", qcm)
        .replace("{services}", services)
        .replace("{result}", result_html)
        .replace("{csrf}", csrf_html)
        .encode("utf-8")
    )


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/healthz":
            self._send(200, b'{"status":"ok"}', content_type="application/json; charset=utf-8")
            return
        if self.path != "/":
            self.send_error(404)
            return
        self._send(200, render(csrf=self._csrf_token()))

    def do_POST(self) -> None:
        if self.path not in ("/login", "/qcm", "/service"):
            self.send_error(404)
            return
        form = parse_qs(self._read_body().decode("utf-8"))
        try:
            self._cleanup_state()
            self._validate_csrf(form)
            if self.path == "/login":
                self._login(form)
            elif self.path == "/qcm":
                self._answer_qcm(form)
            else:
                self._service(form)
        except EcoleDirecteError as error:
            self._send(502, render(str(error), error=True, csrf=self._csrf_token()))
        except (KeyError, ValueError):
            self._send(400, render("La requête de connexion est invalide.", error=True, csrf=self._csrf_token()))

    def _login(self, form: dict[str, list[str]]) -> None:
        if not self._allow_login():
            self._send(429, render("Trop de tentatives, réessayez plus tard.", error=True, csrf=self._csrf_token()))
            return
        identifiant = form.get("identifiant", [""])[0].strip()
        mot_de_passe = form.get("mot_de_passe", [""])[0]
        if not identifiant or not mot_de_passe:
            self._send(400, render("Les deux champs sont obligatoires.", error=True, csrf=self._csrf_token()))
            return

        client = EcoleDirecteClient(identifiant, mot_de_passe)
        result = client.login()
        if result.get("code") in (250, "250"):
            qcm = client.get_qcm()
            challenge_id = secrets.token_urlsafe(24)
            csrf = self._csrf_token()
            with LOCK:
                CHALLENGES[challenge_id] = {"client": client, "created_at": time.time(), "attempts": 0}
            self._send(200, render(qcm=render_qcm(challenge_id, csrf, qcm.get("data") or {}), csrf=csrf))
            return
        self._show_services(client)

    def _answer_qcm(self, form: dict[str, list[str]]) -> None:
        challenge_id = form.get("challenge", [""])[0]
        choice = form.get("choix", [""])[0]
        with LOCK:
            challenge = CHALLENGES.get(challenge_id)
        client = challenge["client"] if isinstance(challenge, dict) else None
        if not client or not choice:
            raise ValueError("Défi QCM introuvable")
        with LOCK:
            if isinstance(challenge, dict):
                challenge["attempts"] = int(challenge.get("attempts", 0)) + 1
                if int(challenge["attempts"]) > 5:
                    CHALLENGES.pop(challenge_id, None)
                    raise ValueError("Trop d'essais QCM.")
        result = client.complete_qcm(choice)
        with LOCK:
            CHALLENGES.pop(challenge_id, None)
        self._show_services(client)

    def _show_services(self, client: EcoleDirecteClient) -> None:
        if not client.student_id:
            raise EcoleDirecteError("Impossible de trouver l'identifiant de l'élève.")
        session_id = secrets.token_urlsafe(24)
        csrf = self._csrf_token()
        with LOCK:
            SESSIONS[session_id] = {"client": client, "created_at": time.time()}
        self._send(200, render(services=render_services(session_id, csrf), csrf=csrf))

    def _service(self, form: dict[str, list[str]]) -> None:
        session_id = form.get("session", [""])[0]
        service = form.get("service", [""])[0]
        with LOCK:
            session_data = SESSIONS.get(session_id)
        client = session_data["client"] if isinstance(session_data, dict) else None
        if not client or service not in SERVICES:
            raise ValueError("Session ou service invalide")
        _, endpoint_template = SERVICES[service]
        endpoint = endpoint_template.format(student_id=client.student_id)
        params: dict[str, str] = {}
        if service in ("emploi", "cahier"):
            start = date.today() - timedelta(days=date.today().weekday())
            params = {
                "dateDebut": start.isoformat(),
                "dateFin": (start + timedelta(days=6)).isoformat(),
            }
        result = client.get(endpoint, **params)
        response_text = json.dumps(redact_response(result), ensure_ascii=False, indent=2)
        if service in ("emploi", "cahier") and result.get("data") == []:
            end = start + timedelta(days=6)
            response_text = (
                f"Aucune donnée trouvée entre le {start:%d/%m/%Y} et le {end:%d/%m/%Y}.\n\n"
                f"Réponse API :\n{response_text}"
            )
        self._send(200, render(response_text, csrf=self._csrf_token()))

    def _send(
        self,
        status: int,
        body: bytes,
        content_type: str = "text/html; charset=utf-8",
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; form-action 'self'; style-src 'unsafe-inline'; frame-ancestors 'none'")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        if hasattr(self, "_pending_cookies"):
            for cookie in self._pending_cookies:
                self.send_header("Set-Cookie", cookie)
            del self._pending_cookies
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> bytes:
        values = self.headers.get_all("Content-Length", [])
        if len(values) != 1:
            raise ValueError("Content-Length ambigu.")
        raw = values[0].strip()
        if not raw.isdigit():
            raise ValueError("Content-Length invalide.")
        length = int(raw)
        if length < 0 or length > MAX_HTTP_BODY:
            raise ValueError("Requête trop volumineuse.")
        content_type = self.headers.get("Content-Type", "")
        media_type, _, charset_part = content_type.partition(";")
        if media_type.strip().lower() != "application/x-www-form-urlencoded":
            raise ValueError("Content-Type invalide.")
        if charset_part:
            if "charset=" not in charset_part.lower():
                raise ValueError("charset invalide.")
            charset = charset_part.split("=", 1)[1].strip().lower().strip('"')
            if charset != "utf-8":
                raise ValueError("charset invalide.")
        return self.rfile.read(length)

    def _cookie(self, name: str) -> str:
        header = self.headers.get("Cookie", "")
        for item in header.split(";"):
            key, _, value = item.strip().partition("=")
            if key == name:
                return value
        return ""

    def _set_cookie(self, name: str, value: str, max_age: int = 86_400) -> None:
        flags = f"{name}={value}; Max-Age={max_age}; HttpOnly; SameSite=Lax; Path=/"
        if os.environ.get("ECO_TO_ALEXA_ALLOW_INSECURE_HTTP", "0") != "1":
            flags += "; Secure"
        if not hasattr(self, "_pending_cookies"):
            self._pending_cookies = []
        self._pending_cookies.append(flags)

    def _csrf_context(self) -> str:
        browser = self._cookie("browser")
        if not browser:
            browser = secrets.token_urlsafe(24)
            self._set_cookie("browser", browser, max_age=86_400 * 30)
        return browser

    def _csrf_token(self) -> str:
        return hmac.new(CSRF_SECRET, self._csrf_context().encode("utf-8"), hashlib.sha256).hexdigest()

    def _validate_csrf(self, form: dict[str, list[str]]) -> None:
        provided = form.get("csrf", [""])[0]
        if not provided:
            raise ValueError("Jeton CSRF invalide.")
        expected = self._csrf_token()
        if not secrets.compare_digest(provided, expected):
            raise ValueError("Jeton CSRF invalide.")

    def _allow_login(self) -> bool:
        now = time.time()
        key = self.client_address[0]
        with LOCK:
            values = [stamp for stamp in LOGIN_ATTEMPTS.get(key, []) if now - stamp < 60]
            if len(values) >= 12:
                LOGIN_ATTEMPTS[key] = values
                return False
            values.append(now)
            LOGIN_ATTEMPTS[key] = values
            return True

    def _cleanup_state(self) -> None:
        now = time.time()
        with LOCK:
            for challenge_id, data in list(CHALLENGES.items()):
                if now - float(data.get("created_at", 0)) > CHALLENGE_TTL_SECONDS:
                    CHALLENGES.pop(challenge_id, None)
            for session_id, data in list(SESSIONS.items()):
                if now - float(data.get("created_at", 0)) > SESSION_TTL_SECONDS:
                    SESSIONS.pop(session_id, None)
            while len(CHALLENGES) > MAX_CACHE_ENTRIES:
                CHALLENGES.pop(next(iter(CHALLENGES)))
            while len(SESSIONS) > MAX_CACHE_ENTRIES:
                SESSIONS.pop(next(iter(SESSIONS)))

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Site disponible sur http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServeur arrêté.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()