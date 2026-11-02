"""Gabarit HTML partagé par account_server.py et oauth_server.py.

Aucun JavaScript, aucune police externe : la feuille de style est servie par
chaque serveur sur ``CSS_PATH`` afin de respecter une CSP ``style-src 'self'``.
L'identité visuelle reprend une copie double Seyès (lignes bleues, marge rouge).
"""

from __future__ import annotations

import html

CSS_PATH = "/static/app.css"
INVOCATION = "mon carnet scolaire"

CSS = """
:root {
  --page: #eef2f9;
  --sheet: #ffffff;
  --rule: #d5e0f3;
  --rule-strong: #a9bde6;
  --margin: #e0485a;
  --ink: #1d3faf;
  --ink-dark: #142d80;
  --text: #23283a;
  --muted: #5d6478;
  --ok: #1e7a55;
  --ok-bg: #e7f5ee;
  --err: #b3261e;
  --err-bg: #fdecea;
  --margin-x: 72px;
  --serif: "Iowan Old Style", "Palatino Linotype", "Book Antiqua", Palatino, Georgia, serif;
  --sans: system-ui, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
}
* { box-sizing: border-box; }
html { background: var(--page); }
body {
  margin: 0;
  padding: 40px 16px 64px;
  color: var(--text);
  font: 16px/24px var(--sans);
  background: var(--page);
}
.sheet {
  position: relative;
  max-width: 760px;
  margin: 0 auto;
  padding: 40px 40px 48px calc(var(--margin-x) + 28px);
  background-color: var(--sheet);
  /* Réglure Seyès : interligne de 8 px, ligne forte tous les 32 px, colonnes de 32 px. */
  background-image:
    linear-gradient(to right, transparent calc(var(--margin-x) - 1px), var(--margin) calc(var(--margin-x) - 1px), var(--margin) calc(var(--margin-x) + 1px), transparent calc(var(--margin-x) + 1px)),
    repeating-linear-gradient(to bottom, transparent 0 31px, var(--rule-strong) 31px 32px),
    repeating-linear-gradient(to bottom, transparent 0 7px, var(--rule) 7px 8px),
    repeating-linear-gradient(to right, transparent 0 31px, var(--rule) 31px 32px);
  background-position: 0 0, 0 16px, 0 16px, var(--margin-x) 0;
  border-radius: 2px;
  box-shadow: 0 1px 0 #c8d2e6, 0 18px 40px -24px rgba(29, 63, 175, .35);
}
.sheet::before {
  /* Trous de perforation dans la marge. */
  content: "";
  position: absolute;
  left: 22px; top: 96px; bottom: 96px;
  width: 14px;
  background: radial-gradient(circle at 7px 7px, var(--page) 6px, transparent 7px) 0 0 / 14px 160px repeat-y;
}
a { color: var(--ink); text-underline-offset: 3px; }
a:hover { color: var(--ink-dark); }
:focus-visible { outline: 3px solid var(--ink); outline-offset: 2px; }

.masthead { margin-bottom: 32px; }
.brand { margin: 0 0 24px; display: flex; justify-content: space-between; align-items: baseline; gap: 16px; font: 600 15px/24px var(--sans); color: var(--margin); }
.brand form { margin: 0; }
h1 { margin: 0 0 16px; font: 400 40px/48px var(--serif); letter-spacing: -.01em; color: var(--text); max-width: 18em; }
h2 { margin: 0 0 8px; font: 400 26px/32px var(--serif); }
h3 { margin: 0 0 8px; font: 600 16px/24px var(--sans); }
p { margin: 0 0 16px; max-width: 62ch; }
.lede { font-size: 18px; line-height: 32px; color: var(--muted); }

.say {
  margin: 24px 0 0;
  padding: 0;
  font: italic 22px/32px var(--serif);
  color: var(--ink);
}
.say::before { content: "« "; }
.say::after { content: " »"; }

.panel { margin: 0 0 40px; }
.panel > p:last-child { margin-bottom: 0; }

.steps { list-style: none; counter-reset: step; margin: 0 0 40px; padding: 0; }
.steps > li { counter-increment: step; position: relative; margin: 0 0 32px; }
.steps > li::before {
  content: counter(step);
  position: absolute;
  left: calc(-1 * var(--margin-x) - 28px + 20px);
  top: 0;
  width: 32px;
  text-align: center;
  font: 400 26px/32px var(--serif);
  color: var(--margin);
}
.steps > li.done::before { content: "✓"; color: var(--ok); }
.status { display: inline-block; margin-left: 8px; font: 600 13px/24px var(--sans); color: var(--muted); }
.done .status { color: var(--ok); }

.stack { display: grid; gap: 16px; max-width: 420px; }
.row { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; }
label { display: grid; gap: 4px; font: 600 14px/24px var(--sans); color: var(--muted); }
input[type=email], input[type=password], input[type=text], select {
  width: 100%;
  padding: 8px 12px;
  border: 1px solid var(--rule-strong);
  border-radius: 4px;
  background: #fff;
  color: var(--text);
  font: 16px/24px var(--sans);
}
input:focus, select:focus { border-color: var(--ink); outline: none; box-shadow: 0 0 0 3px rgba(29,63,175,.18); }
fieldset { margin: 0; padding: 0; border: 0; display: grid; gap: 8px; }
legend { margin-bottom: 8px; font: 600 16px/24px var(--sans); }
.choice { display: flex; gap: 8px; align-items: center; font-weight: 400; color: var(--text); }
.check { display: flex; gap: 8px; align-items: flex-start; font-weight: 400; color: var(--text); }

button {
  justify-self: start;
  padding: 8px 20px;
  border: 1px solid var(--ink);
  border-radius: 4px;
  background: var(--ink);
  color: #fff;
  font: 600 16px/24px var(--sans);
  cursor: pointer;
}
button:hover { background: var(--ink-dark); border-color: var(--ink-dark); }
button.quiet { background: transparent; color: var(--ink); }
button.quiet:hover { background: rgba(29,63,175,.08); }
button.link { padding: 0; border: 0; background: none; color: var(--ink); font-weight: 600; text-decoration: underline; text-underline-offset: 3px; }
button.danger { background: transparent; color: var(--err); border-color: var(--err); }
button.danger:hover { background: var(--err-bg); }

.notice { margin: 0 0 32px; padding: 12px 16px; border-left: 4px solid var(--ok); background: var(--ok-bg); max-width: 62ch; }
.notice.error { border-color: var(--err); background: var(--err-bg); color: #5c1410; }
.aside { margin-top: 16px; color: var(--muted); font-size: 15px; }
.facts { display: grid; grid-template-columns: max-content 1fr; gap: 0 16px; margin: 0 0 16px; }
.facts dt { color: var(--muted); }
.facts dd { margin: 0; font-weight: 600; }

.answer {
  margin: 16px 0 0;
  padding: 16px 20px;
  max-width: 62ch;
  border: 1px solid var(--rule-strong);
  border-radius: 4px;
  background: #fff;
  font: 18px/32px var(--serif);
}
.answer .who { display: block; font: 600 13px/24px var(--sans); color: var(--muted); }

.two { display: grid; grid-template-columns: 1fr 1fr; gap: 40px; }
details { margin-top: 16px; }
summary { cursor: pointer; font-weight: 600; color: var(--ink); }
footer { max-width: 760px; margin: 16px auto 0; color: var(--muted); font-size: 14px; }

@media (max-width: 720px) {
  :root { --margin-x: 32px; }
  body { padding: 16px 8px 40px; }
  .sheet { padding: 24px 16px 32px calc(var(--margin-x) + 16px); }
  .sheet::before { display: none; }
  h1 { font-size: 30px; line-height: 40px; }
  .two { grid-template-columns: 1fr; gap: 24px; }
  .steps > li::before { left: calc(-1 * var(--margin-x) - 16px + 2px); width: 28px; font-size: 20px; }
}
@media (prefers-reduced-motion: reduce) { * { scroll-behavior: auto !important; } }
"""


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def notice(text: str, kind: str = "ok") -> str:
    if not text:
        return ""
    css = "notice error" if kind == "error" else "notice"
    role = "alert" if kind == "error" else "status"
    return f'<p class="{css}" role="{role}">{esc(text)}</p>'


def page(title: str, body: str) -> str:
    return (
        "<!doctype html>\n<html lang='fr'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{esc(title)} – EC to Alexa</title>"
        f"<link rel='stylesheet' href='{CSS_PATH}'>"
        "</head><body><main class='sheet'>"
        f"{body}</main>"
        "<footer>EC to Alexa n'est pas affilié à École Directe ni à Amazon. "
        "Tes identifiants École Directe sont chiffrés et ne sont jamais affichés.</footer>"
        "</body></html>"
    )
