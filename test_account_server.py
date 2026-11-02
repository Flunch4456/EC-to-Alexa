"""Parcours complet du site avec un faux client École Directe (aucun appel réseau)."""

import http.cookiejar
import json
import os
import re
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from cryptography.fernet import Fernet

import account_server
from application import AccountStore, AlexaSkill


class FakeClient:
    """Imite EcoleDirecteClient : QCM au premier login, succès après la bonne réponse."""

    def __init__(self, identifiant, mot_de_passe, **_):
        self.identifiant, self.mot_de_passe = identifiant, mot_de_passe
        self.token = self.student_id = self.cn = self.cv = None
        self.device_uuid = "uuid-fixe"

    def login(self, cn=None, cv=None):
        if cn:
            self.token, self.student_id = "jeton", "4321"
            return {"code": 200}
        return {"code": 250}

    def get_qcm(self):
        return {"data": {"question": "Mois de naissance ?", "propositions": ["Mars", "Mai"]}}

    def complete_qcm(self, choice):
        self.cn, self.cv = "cn", "cv"
        return self.login("cn", "cv")


class FakeService:
    def timetable(self, start, end):
        day = start.isoformat()
        return {"data": [{"start_date": f"{day} 08:00", "end_date": f"{day} 09:00",
                          "matiere": "MATHEMATIQUES", "salle": "A3"}]}


class AccountSiteTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"ECO_TO_ALEXA_SECURE_COOKIES": "0"})
        self.env.start()
        self.client_patch = patch.object(account_server, "EcoleDirecteClient", FakeClient)
        self.client_patch.start()
        self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self.store = AccountStore(self.tmp.name, Fernet.generate_key())
        skill = AlexaSkill(self.store)
        skill._service_for = lambda uid: FakeService() if self.store.ecole_directe_version(uid) else None
        account_server.AccountHandler.store = self.store
        account_server.AccountHandler.skill = skill
        account_server.AccountHandler.pending = account_server.PendingQcm()
        account_server.RATE_LIMITER = account_server.SharedRateLimiter()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), account_server.AccountHandler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(self.jar)
        )

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.store.close()
        self.client_patch.stop()
        self.env.stop()

    def get(self, path):
        with self.opener.open(self.base + path, timeout=5) as response:
            return response.status, response.read().decode()

    def post(self, path, **fields):
        data = urllib.parse.urlencode(fields).encode()
        try:
            with self.opener.open(self.base + path, data=data, timeout=5) as response:
                return response.status, response.read().decode()
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode()

    @staticmethod
    def csrf(page):
        return re.search(r'name="csrf" value="([^"]+)"', page).group(1)

    def register(self):
        return self.post("/register", email="eleve@example.test",
                         password="motdepasse-solide", password2="motdepasse-solide")

    def test_full_flow_with_security_question_and_simulator(self):
        status, page = self.register()
        self.assertEqual(status, 200)
        self.assertIn("Compte créé", page)
        status, page = self.post("/connect-ecole-directe", csrf=self.csrf(page),
                                 identifiant="eleve", ecole_password="secret")
        self.assertIn("Mois de naissance ?", page)
        status, page = self.post("/qcm", csrf=self.csrf(page), choix="Mars")
        self.assertIn("École Directe est connecté", page)
        self.assertIn("4321", page)
        user_id = self.store.user_for_login("eleve@example.test", "motdepasse-solide")
        client = self.store.ecole_directe_client(user_id)
        self.assertEqual((client.device_uuid, client.cn, client.cv), ("uuid-fixe", "cn", "cv"))
        status, page = self.post("/simulate", csrf=self.csrf(page), question="today")
        self.assertIn("Mathematiques en salle A3", page)

    def test_state_changing_forms_require_csrf(self):
        self.register()
        status, page = self.post("/disconnect-ecole-directe", csrf="faux")
        self.assertEqual(status, 403)
        self.assertIn("formulaire a expiré", page)

    def test_qcm_answer_must_be_one_of_the_choices(self):
        _, page = self.register()
        _, page = self.post("/connect-ecole-directe", csrf=self.csrf(page),
                            identifiant="eleve", ecole_password="secret")
        status, page = self.post("/qcm", csrf=self.csrf(page), choix="Juin")
        self.assertEqual(status, 400)
        self.assertIn("Choisis une des réponses proposées", page)

    def test_wrong_password_and_mismatched_confirmation(self):
        status, page = self.post("/register", email="a@example.test",
                                 password="motdepasse-solide", password2="autre-chose-12")
        self.assertEqual(status, 400)
        self.assertIn("ne correspondent pas", page)
        status, page = self.post("/login", email="a@example.test", password="mauvais-mdp-12")
        self.assertEqual(status, 401)

    def test_export_and_delete_account(self):
        _, page = self.register()
        status, body = self.get("/account/export")
        self.assertEqual(json.loads(body)["email"], "eleve@example.test")
        status, page = self.post("/account/delete", csrf=self.csrf(page))
        self.assertEqual(status, 400)
        _, page = self.get("/dashboard")
        status, page = self.post("/account/delete", csrf=self.csrf(page), confirm="oui")
        self.assertIn("supprimés", page)
        with self.assertRaises(PermissionError):
            self.store.user_for_login("eleve@example.test", "motdepasse-solide")

    def test_pages_send_strict_security_headers_and_stylesheet(self):
        with self.opener.open(self.base + "/", timeout=5) as response:
            csp = response.headers["Content-Security-Policy"]
        self.assertIn("default-src 'none'", csp)
        self.assertIn("style-src 'self'", csp)
        status, css = self.get("/static/app.css")
        self.assertIn("--margin", css)


if __name__ == "__main__":
    unittest.main()
