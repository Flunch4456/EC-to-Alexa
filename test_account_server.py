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
from ecole_directe import EcoleDirecteClient, EcoleDirecteError


class FakeClient(EcoleDirecteClient):
    """Imite École Directe : QCM tant que cn/cv ne sont pas valides, succès après la bonne réponse.

    Les valeurs cn/cv valides et la question posée sont réglables pour simuler
    une session expirée ou une nouvelle question.
    """

    QUESTIONS = {"Mois de naissance ?": (["Mars", "Mai"], "Mars"),
                 "Année de naissance ?": (["2009", "2010"], "2010")}
    question = "Mois de naissance ?"
    valid_cn = "cn-1"
    logins = 0
    answers = []

    def __init__(self, identifiant, mot_de_passe, **kwargs):
        super().__init__(identifiant, mot_de_passe, **kwargs)
        self.device_uuid = "uuid-fixe"

    def login(self, cn=None, cv=None):
        type(self).logins += 1
        if cn and cn == type(self).valid_cn:
            self.token, self.student_id = "jeton", "4321"
            return {"code": 200}
        self.token, self.qcm_token = None, "qcm"
        return {"code": 250}

    def get_qcm(self):
        propositions, _ = self.QUESTIONS[type(self).question]
        return {"data": {"question": type(self).question, "propositions": propositions}}

    def complete_qcm(self, choice):
        type(self).answers.append(choice)
        self.qcm_token = None
        if choice != self.QUESTIONS[type(self).question][1]:
            raise EcoleDirecteError("Réponse incorrecte")
        self.cn, self.cv = type(self).valid_cn, "cv"
        return self.login(self.cn, self.cv)

    def _request(self, endpoint, payload, accepted_codes=(200,)):
        return {"code": 200, "data": []}


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
        self.store_client_patch = patch("application.EcoleDirecteClient", FakeClient)
        self.store_client_patch.start()
        FakeClient.question, FakeClient.valid_cn, FakeClient.logins, FakeClient.answers = (
            "Mois de naissance ?", "cn-1", 0, [])
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
        self.store_client_patch.stop()
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
        self.assertEqual((client.device_uuid, client.cn, client.cv), ("uuid-fixe", "cn-1", "cv"))
        status, page = self.post("/simulate", csrf=self.csrf(page), question="today")
        self.assertIn("Mathématiques en salle A3", page)

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


class FakeAmazon:
    """Imite Login with Amazon et l'API Skill Enablement, puis l'échange du code par Amazon."""

    def __init__(self, store, client_id):
        self.store, self.client_id, self.calls = store, client_id, []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url))
        if url.endswith("/auth/o2/token"):
            fields = urllib.parse.parse_qs(body.decode())
            assert fields["code_verifier"][0] and fields["code"][0] == "code-amazon"
            return 200, {"access_token": "jeton-amazon"}
        if url.endswith("/alexaApiEndpoint"):
            return 200, {"endpoints": ["api.eu.amazonalexa.com"]}
        if url.endswith("/enablement"):
            request = json.loads(body)
            link = request["accountLinkRequest"]
            # Amazon échange notre code sur /oauth/token, avec la même redirectUri.
            self.store.exchange_authorization_code(link["authCode"], self.client_id, link["redirectUri"])
            return 201, {"user": {"id": "amzn1.ask.account.TESTUSER42"},
                         "accountLink": {"status": "LINKED"}, "status": "ENABLED"}
        return 404, {}


class AmazonLinkFlowTests(AccountSiteTests):
    def setUp(self):
        super().setUp()
        from amazon_link import AmazonSkillLinker
        self.fake = FakeAmazon(self.store, "ec-to-alexa")
        account_server.AccountHandler.linker = AmazonSkillLinker(
            "amzn1.application-oa2-client.test", "secret", "amzn1.ask.skill.test",
            "https://ec-to-alexa.example/amazon/callback", http=self.fake,
        )
        account_server.AccountHandler.oauth_client_id = "ec-to-alexa"
        account_server.AccountHandler.amazon_pending = account_server.PendingAmazon()

    def tearDown(self):
        account_server.AccountHandler.linker = None
        super().tearDown()

    def connected_page(self):
        _, page = self.register()
        _, page = self.post("/connect-ecole-directe", csrf=self.csrf(page),
                            identifiant="eleve", ecole_password="secret")
        _, page = self.post("/qcm", csrf=self.csrf(page), choix="Mars")
        return page

    def test_link_from_site_records_amazon_account(self):
        page = self.connected_page()
        link = re.search(r'href="(/amazon/link\?csrf=[^"]+)"', page).group(1)
        no_redirect = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(self.jar),
            type("NoRedirect", (urllib.request.HTTPRedirectHandler,), {
                "redirect_request": lambda *a, **k: None})(),
        )
        with self.assertRaises(urllib.error.HTTPError) as caught:
            no_redirect.open(self.base + link.replace("&amp;", "&"), timeout=5)
        location = caught.exception.headers["Location"]
        self.assertTrue(location.startswith("https://www.amazon.com/ap/oa?"))
        params = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)
        self.assertEqual(params["scope"], ["alexa::skills:account_linking"])
        self.assertEqual(params["code_challenge_method"], ["S256"])
        state = params["state"][0]
        status, page = self.get(f"/amazon/callback?code=code-amazon&state={state}")
        self.assertIn("Ton compte Amazon est lié", page)
        self.assertIn("…STUSER42", page)  # 8 derniers caractères de l'identifiant Alexa
        user_id = self.store.user_for_login("eleve@example.test", "motdepasse-solide")
        self.assertEqual(self.store.amazon_link(user_id)["alexa_user_id"], "amzn1.ask.account.TESTUSER42")
        self.assertEqual(self.store.alexa_link_count(user_id), 1)
        self.assertTrue(any("api.eu.amazonalexa.com" in url for _, url in self.fake.calls))
        # Un state rejoué ne relie rien une seconde fois.
        with self.assertRaises(urllib.error.HTTPError) as replay:
            self.get(f"/amazon/callback?code=code-amazon&state={state}")
        self.assertEqual(replay.exception.code, 400)
        self.assertIn("a expiré", replay.exception.read().decode())

    def test_link_requires_csrf_and_ecole_directe(self):
        _, page = self.register()
        self.assertNotIn("/amazon/link", page)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.get("/amazon/link?csrf=faux")
        self.assertEqual(caught.exception.code, 403)
        self.assertIn("Le lien a expiré", caught.exception.read().decode())

    def test_alexa_requests_identify_the_amazon_account(self):
        self.connected_page()
        user_id = self.store.user_for_login("eleve@example.test", "motdepasse-solide")
        token = self.store.create_alexa_token(user_id)
        skill = account_server.AccountHandler.skill
        skill.handle(token, {"context": {"System": {"user": {"userId": "amzn1.ask.account.APPLINK"}}},
                             "request": {"type": "LaunchRequest"}})
        link = self.store.amazon_link(user_id)
        self.assertEqual((link["alexa_user_id"], link["source"]), ("amzn1.ask.account.APPLINK", "alexa_app"))
        self.assertIsNotNone(link["last_seen_at"])


class WebOnlyLinkingTests(AmazonLinkFlowTests):
    def test_dashboard_offers_only_the_website_link(self):
        page = self.connected_page()
        self.assertIn("Lier mon compte Amazon", page)
        self.assertNotIn("Skills et jeux", page)

    def test_unlinked_voice_request_points_to_the_website(self):
        from unittest.mock import patch as env_patch
        with env_patch.dict(os.environ, {"DOMAIN": "ec-to-alexa.example", "ECO_TO_ALEXA_PUBLIC_URL": ""}):
            response = account_server.AccountHandler.skill.handle(None, {"request": {"type": "LaunchRequest"}})
        card = response["response"]["card"]
        self.assertEqual(card["type"], "Simple")
        self.assertIn("https://ec-to-alexa.example", card["content"])
        self.assertIn("site EC to Alexa", response["response"]["outputSpeech"]["ssml"])


class QcmMemoryTests(AccountSiteTests):
    """Réponses au QCM mémorisées (hachées) et réutilisées par Alexa et par le site."""

    TODAY = {"request": {"type": "IntentRequest", "intent": {"name": "GetTodayScheduleIntent"}}}

    def setUp(self):
        super().setUp()
        self.skill = AlexaSkill(self.store)  # vrai chargement depuis la base

    def connect(self):
        _, page = self.register()
        _, page = self.post("/connect-ecole-directe", csrf=self.csrf(page),
                            identifiant="eleve", ecole_password="secret")
        _, page = self.post("/qcm", csrf=self.csrf(page), choix="Mars")
        self.assertIn("École Directe est connecté", page)
        return self.store.user_for_login("eleve@example.test", "motdepasse-solide")

    def speak(self, user_id):
        response = AlexaSkill(self.store).handle_for_user(user_id, self.TODAY)
        return response["response"]["outputSpeech"]["ssml"]

    def test_answers_are_stored_as_keyed_hashes_only(self):
        user_id = self.connect()
        self.assertEqual(self.store.qcm_answer_count(user_id), 1)
        with open(self.tmp.name, "rb") as file:
            raw = file.read()
        for clear in (b"Mars", b"Mois de naissance", "Mois de naissance".encode("utf-16-le")):
            self.assertNotIn(clear, raw)
        row = self.store.db.execute("SELECT question_hash, answer_hash FROM qcm_answers").fetchone()
        self.assertRegex(row["question_hash"], r"^[0-9a-f]{64}$")
        self.assertRegex(row["answer_hash"], r"^[0-9a-f]{64}$")
        # Autre clé serveur : la réponse n'est plus reconnue.
        other = AccountStore(self.tmp.name, Fernet.generate_key())
        self.assertIsNone(other.known_qcm_answer(user_id, "eleve", "Mois de naissance ?", ["Mars", "Mai"]))
        other.close()
        # Autre compte École Directe : non reconnue non plus.
        self.assertIsNone(self.store.known_qcm_answer(user_id, "autre", "Mois de naissance ?", ["Mars", "Mai"]))
        self.assertEqual(
            self.store.known_qcm_answer(user_id, "eleve", "  mois DE naissance ? ", ["Mai", "Mars"]), "Mars")

    def test_alexa_answers_a_known_question_by_itself(self):
        user_id = self.connect()
        FakeClient.valid_cn = "cn-2"  # session École Directe expirée : QCM reposé
        FakeClient.answers = []
        speech = self.speak(user_id)
        self.assertNotIn("question de sécurité", speech)
        self.assertEqual(FakeClient.answers, ["Mars"])
        self.assertEqual(self.store.ecole_directe_client(user_id).cn, "cn-2")
        self.assertIsNone(self.store.ecole_directe_info(user_id)["qcm_required_at"])

    def test_unknown_question_sends_the_student_to_the_website(self):
        user_id = self.connect()
        FakeClient.valid_cn, FakeClient.question = "cn-2", "Année de naissance ?"
        FakeClient.answers = []
        speech = self.speak(user_id)
        self.assertIn("site EC to Alexa", speech)
        self.assertEqual(FakeClient.answers, [])  # aucune réponse devinée
        self.assertIsNotNone(self.store.ecole_directe_info(user_id)["qcm_required_at"])
        _, page = self.get("/dashboard")
        self.assertIn("Répondre à la question de sécurité", page)
        _, page = self.post("/ecole-directe/verify", csrf=self.csrf(page))
        self.assertIn("Année de naissance ?", page)
        _, page = self.post("/qcm", csrf=self.csrf(page), choix="2010")
        self.assertIn("École Directe est connecté", page)
        self.assertEqual(self.store.qcm_answer_count(user_id), 2)
        self.assertIsNone(self.store.ecole_directe_info(user_id)["qcm_required_at"])
        # La fois suivante, Alexa répond seule à cette question aussi.
        FakeClient.valid_cn = "cn-3"
        self.assertNotIn("question de sécurité", self.speak(user_id))

    def test_rejected_answer_is_forgotten_and_asked_again(self):
        user_id = self.connect()
        original = FakeClient.QUESTIONS
        FakeClient.QUESTIONS = dict(original, **{"Mois de naissance ?": (["Mars", "Mai"], "Mai")})
        try:
            FakeClient.valid_cn = "cn-2"
            self.assertIn("site EC to Alexa", self.speak(user_id))
            self.assertEqual(self.store.qcm_answer_count(user_id), 0)
        finally:
            FakeClient.QUESTIONS = original

    def test_reconnecting_reuses_the_answer_and_it_can_be_forgotten(self):
        user_id = self.connect()
        _, page = self.get("/dashboard")
        self.assertIn("Réponses de sécurité mémorisées", page)
        _, page = self.post("/disconnect-ecole-directe", csrf=self.csrf(page))
        _, page = self.post("/connect-ecole-directe", csrf=self.csrf(page),
                            identifiant="eleve", ecole_password="secret")
        self.assertIn("remplie automatiquement", page)
        _, page = self.post("/ecole-directe/forget-qcm", csrf=self.csrf(page))
        self.assertIn("oubliées", page)
        self.assertEqual(self.store.qcm_answer_count(user_id), 0)
