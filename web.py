"""Interface web locale pour lancer la connexion Ecole Directe."""

import html
import json
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

from ecole_directe import EcoleDirecteClient, EcoleDirecteError, redact_response


HOST = "127.0.0.1"
PORT = 8000
CHALLENGES: dict[str, EcoleDirecteClient] = {}


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
        <label for="identifiant">Identifiant</label>
        <input id="identifiant" name="identifiant" autocomplete="username" required>
        <label for="mot_de_passe">Mot de passe</label>
        <input id="mot_de_passe" name="mot_de_passe" type="password" autocomplete="current-password" required>
        <button type="submit">Se connecter</button>
      </form>
      {qcm}
      {result}
    </section>
  </main>
</body>
</html>"""


def render_qcm(challenge_id: str, data: dict[str, object]) -> str:
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
        f'<form method="post" action="/qcm"><input type="hidden" name="challenge" '
        f'value="{html.escape(challenge_id, quote=True)}">{options}'
        '<button type="submit">Valider la réponse</button></form></section>'
    )


def render(result: str = "", error: bool = False, qcm: str = "") -> bytes:
    result_html = ""
    if result:
        result_html = f'<pre class="result{" error" if error else ""}">{html.escape(result)}</pre>'
    return PAGE.replace("{qcm}", qcm).replace("{result}", result_html).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path != "/":
            self.send_error(404)
            return
        self._send(200, render())

    def do_POST(self) -> None:
        if self.path not in ("/login", "/qcm"):
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        form = parse_qs(self.rfile.read(length).decode("utf-8"))
        try:
            if self.path == "/login":
                self._login(form)
            else:
                self._answer_qcm(form)
        except EcoleDirecteError as error:
            self._send(502, render(str(error), error=True))
        except (KeyError, ValueError):
            self._send(400, render("La requête de connexion est invalide.", error=True))

    def _login(self, form: dict[str, list[str]]) -> None:
        identifiant = form.get("identifiant", [""])[0].strip()
        mot_de_passe = form.get("mot_de_passe", [""])[0]
        if not identifiant or not mot_de_passe:
            self._send(400, render("Les deux champs sont obligatoires.", error=True))
            return

        client = EcoleDirecteClient(identifiant, mot_de_passe)
        result = client.login()
        if result.get("code") in (250, "250"):
            qcm = client.get_qcm()
            challenge_id = secrets.token_urlsafe(24)
            CHALLENGES[challenge_id] = client
            self._send(200, render(qcm=render_qcm(challenge_id, qcm.get("data") or {})))
            return
        self._send(200, render(json.dumps(redact_response(result), ensure_ascii=False, indent=2)))

    def _answer_qcm(self, form: dict[str, list[str]]) -> None:
        challenge_id = form.get("challenge", [""])[0]
        choice = form.get("choix", [""])[0]
        client = CHALLENGES.get(challenge_id)
        if not client or not choice:
            raise ValueError("Défi QCM introuvable")
        result = client.complete_qcm(choice)
        CHALLENGES.pop(challenge_id, None)
        self._send(200, render(json.dumps(redact_response(result), ensure_ascii=False, indent=2)))

    def _send(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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