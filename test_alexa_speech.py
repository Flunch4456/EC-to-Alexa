import base64
import json
import tempfile
import threading
import unittest
import urllib.request
from datetime import date, datetime, timezone
from http.server import ThreadingHTTPServer

from cryptography.fernet import Fernet

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
from application import AccountStore, AlexaSkill, OAuthAccountLinking
from alexa_verify import AlexaRequestVerifier, AlexaVerificationError
from oauth_server import (
    OAuthHandler,
    alexa_access_token,
    oauth_from_env,
    redirect_uris_from_env,
)

TODAY = date(2026, 11, 2)  # lundi
TIMETABLE = {
    "code": 200,
    "data": [
        {"start_date": "2026-11-02 10:00", "end_date": "2026-11-02 11:00",
         "matiere": "HISTOIRE-GEOGRAPHIE", "salle": "B12", "prof": "M. MARTIN"},
        {"start_date": "2026-11-02 08:00", "end_date": "2026-11-02 09:00",
         "matiere": "MATHEMATIQUES", "salle": "A3", "prof": "Mme DURAND"},
        {"start_date": "2026-11-02 14:00", "end_date": "2026-11-02 16:00",
         "matiere": "EPS", "salle": "GYMNASE"},
        {"start_date": "2026-11-02 16:00", "end_date": "2026-11-02 17:00",
         "matiere": "ANGLAIS", "isAnnule": True},
    ],
}


def b64(text):
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


class SpeechTests(unittest.TestCase):
    def test_timetable_is_sorted_and_mentions_rooms_and_cancellations(self):
        text = describe_timetable(TIMETABLE, TODAY, TODAY)
        self.assertTrue(text.startswith("Aujourd'hui, tu as 3 cours, de 8 heures à 16 heures."))
        self.assertLess(text.index("Mathématiques"), text.index("Histoire"))
        self.assertIn("EPS au gymnase", text)
        self.assertIn("le cours d'Anglais de 16 heures est annulé", text)
        self.assertIn("le cours d'Anglais de 16 heures est annulé", text)

    def test_empty_timetable(self):
        self.assertEqual(
            describe_timetable({"data": []}, date(2026, 11, 3), TODAY),
            "Tu n'as pas cours demain.",
        )

    def test_next_class(self):
        text = describe_next_class(TIMETABLE, datetime(2026, 11, 2, 9, 30))
        self.assertEqual(
            text,
            "Ton prochain cours est Histoire géographie, à 10 heures, en salle B12, avec M. MARTIN.",
        )
        self.assertEqual(
            describe_next_class(TIMETABLE, datetime(2026, 11, 2, 18, 0)),
            "Tu n'as plus cours aujourd'hui.",
        )

    def test_homework_detailed_format_decodes_content(self):
        result = {"data": {"date": "2026-11-03", "matieres": [
            {"matiere": "FRANCAIS", "interrogation": False,
             "aFaire": {"contenu": b64("<p>Lire le chapitre&nbsp;3</p>"), "effectue": False}},
            {"matiere": "SVT", "interrogation": True,
             "aFaire": {"contenu": b64("<p>Réviser la cellule</p>"), "effectue": True}},
        ]}}
        text = describe_homework(result, date(2026, 11, 3), TODAY)
        self.assertIn("Pour demain, tu as 2 devoirs, dont 1 déjà fait.", text)
        self.assertIn("En Français : Lire le chapitre 3.", text)
        self.assertIn("En SVT : Réviser la cellule, c'est déjà fait.", text)
        self.assertIn("il y a une évaluation en SVT", text)

    def test_homework_summary_format_and_empty(self):
        result = {"data": {"2026-11-03": [{"matiere": "ANGLAIS", "effectue": False}]}}
        self.assertIn("En Anglais : un travail est à faire.",
                      describe_homework(result, date(2026, 11, 3), TODAY))
        self.assertEqual(describe_homework({"data": {}}, date(2026, 11, 3), TODAY),
                         "Tu n'as aucun devoir pour demain.")

    def test_absences(self):
        result = {"data": {"absencesRetards": [
            {"typeElement": "Absence", "justifie": True, "date": "2026-10-01",
             "displayDate": "le jeudi 1 octobre"},
            {"typeElement": "Retard", "justifie": False, "date": "2026-10-20",
             "displayDate": "le mardi 20 octobre à 08:05"},
        ]}}
        self.assertEqual(
            describe_absences(result),
            "Cette année, tu as 1 absence et 1 retard. 1 n'est pas encore justifié. "
            "Le plus récent : retard le mardi 20 octobre à 08:05.",
        )
        self.assertIn("aucune absence", describe_absences({"data": {"absencesRetards": []}}))

    def test_messages(self):
        result = {"data": {"messages": {"received": [
            {"subject": "Sortie scolaire", "read": False, "date": "2026-11-01 10:00",
             "from": {"civilite": "M.", "prenom": "Paul", "nom": "DUPONT"}},
            {"subject": "Ancien", "read": True, "date": "2026-10-01 10:00",
             "from": {"name": "Vie scolaire"}},
        ]}}}
        self.assertEqual(
            describe_messages(result),
            "Tu as 1 nouveau message. De M. Paul DUPONT : Sortie scolaire.",
        )
        result["data"]["messages"]["received"][0]["read"] = True
        self.assertIn("aucun nouveau message. Le dernier vient de M. Paul DUPONT",
                      describe_messages(result))

    def test_grades_most_recent_first(self):
        result = {"data": {"notes": [
            {"libelleMatiere": "MATHEMATIQUES", "valeur": "15,5", "noteSur": "20",
             "devoir": "Contrôle chapitre 2", "dateSaisie": "2026-10-20"},
            {"libelleMatiere": "ANGLAIS", "valeur": "Abs", "noteSur": "20",
             "dateSaisie": "2026-10-25"},
        ]}}
        self.assertEqual(
            describe_grades(result),
            "Tes 2 dernières notes : abs en Anglais ; "
            "15,5 sur 20 en Mathématiques, pour Contrôle chapitre 2.",
        )

    def test_calendar_helpers(self):
        self.assertEqual(next_school_day(date(2026, 11, 6)), date(2026, 11, 9))
        self.assertEqual(school_year(date(2026, 11, 2)), "2026-2027")
        self.assertEqual(school_year(date(2027, 3, 1)), "2026-2027")


class FakeService:
    def __init__(self):
        self.days = []

    def timetable(self, start, end):
        self.days.append(start)
        return TIMETABLE

    def homework(self, day):
        self.days.append(day)
        return {"data": {}}


class AlexaSkillFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self.store = AccountStore(self.tmp.name, Fernet.generate_key())
        self.user_id = self.store.register("a@example.test", "a" * 12)
        self.token = self.store.create_alexa_token(self.user_id)
        self.service = FakeService()
        self.skill = AlexaSkill(
            self.store, {self.user_id: self.service},
            clock=lambda: datetime(2026, 11, 2, 9, 30),
        )

    def tearDown(self):
        self.store.close()

    def speech(self, response):
        return response["response"]["outputSpeech"]["ssml"]

    def intent(self, name, slots=None):
        return {"request": {"type": "IntentRequest",
                            "intent": {"name": name, "slots": slots or {}}}}

    def test_missing_token_asks_for_account_linking(self):
        response = self.skill.handle(None, self.intent("GetHomeworkIntent"))
        self.assertEqual(response["response"]["card"], {"type": "LinkAccount"})

    def test_launch_keeps_session_open(self):
        response = self.skill.handle(self.token, {"request": {"type": "LaunchRequest"}})
        self.assertIn("Bienvenue", self.speech(response))
        self.assertFalse(response["response"]["shouldEndSession"])
        self.assertIn("reprompt", response["response"])

    def test_homework_defaults_to_next_school_day_and_uses_slot(self):
        self.skill.handle(self.token, self.intent("GetHomeworkIntent"))
        self.skill.handle(self.token, self.intent(
            "GetHomeworkIntent", {"jour": {"name": "jour", "value": "2026-11-05"}}))
        self.assertEqual(self.service.days, [date(2026, 11, 3), date(2026, 11, 5)])

    def test_next_class_answer_is_spoken_and_escaped(self):
        response = self.skill.handle(self.token, self.intent("GetNextClassIntent"))
        self.assertIn("Ton prochain cours est Histoire géographie", self.speech(response))
        self.assertTrue(response["response"]["shouldEndSession"])

    def test_stop_and_session_end(self):
        self.assertIn("À bientôt", self.speech(
            self.skill.handle(None, self.intent("AMAZON.StopIntent"))))
        self.assertEqual(
            self.skill.handle(None, {"request": {"type": "SessionEndedRequest"}}),
            {"version": "1.0", "response": {}},
        )

    def test_linked_account_is_loaded_after_startup(self):
        skill = AlexaSkill.from_store(self.store)
        self.store.save_ecole_directe_connection(self.user_id, "eleve", "secret", "123")
        self.assertIsNotNone(skill._service_for(self.user_id))


class AlexaEnvelopeTests(unittest.TestCase):
    def envelope(self, timestamp="2026-11-02T08:30:00Z", app="amzn1.ask.skill.test"):
        return {
            "context": {"System": {"application": {"applicationId": app},
                                   "user": {"accessToken": "jeton"}}},
            "request": {"timestamp": timestamp},
        }

    def test_access_token_comes_from_body(self):
        self.assertEqual(alexa_access_token(self.envelope()), "jeton")

    def test_skill_id_and_timestamp_are_checked(self):
        now = datetime(2026, 11, 2, 8, 31, tzinfo=timezone.utc).timestamp()
        verifier = AlexaRequestVerifier(
            "amzn1.ask.skill.test", verify_signature=False, clock=lambda: now
        )
        verifier.verify({}, b"", self.envelope())
        with self.assertRaises(AlexaVerificationError):
            AlexaRequestVerifier("autre", verify_signature=False, clock=lambda: now).verify(
                {}, b"", self.envelope())
        with self.assertRaises(AlexaVerificationError):
            verifier.verify({}, b"", self.envelope("2026-11-02T08:00:00Z"))

    def test_webhook_answers_over_http(self):
        # Régression : le corps était lu deux fois et la requête restait bloquée.
        with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as tmp:
            store = AccountStore(tmp.name)
        OAuthHandler.oauth = OAuthAccountLinking(store, "alexa", "https://example.test/cb", "s")
        OAuthHandler.skill = AlexaSkill(store)
        OAuthHandler.verifier = AlexaRequestVerifier(
            "amzn1.ask.skill.test", verify_signature=False
        )
        server = ThreadingHTTPServer(("127.0.0.1", 0), OAuthHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            body = self.envelope(datetime.now(timezone.utc).isoformat())
            body["request"]["type"] = "LaunchRequest"
            del body["context"]["System"]["user"]["accessToken"]
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/alexa",
                data=json.dumps(body).encode(), method="POST",
                headers={"Content-Type": "application/json"},
            )
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(request, timeout=5) as response:
                payload = json.load(response)
            self.assertEqual(payload["response"]["card"], {"type": "LinkAccount"})
        finally:
            OAuthHandler.verifier = None
            server.shutdown()
            server.server_close()
            store.close()


class OAuthConfigTests(unittest.TestCase):
    def test_vendor_id_expands_to_every_amazon_region(self):
        uris = redirect_uris_from_env({"ALEXA_VENDOR_ID": "M2TEST"})
        self.assertEqual(uris, [
            "https://layla.amazon.com/api/skill/link/M2TEST",
            "https://pitangui.amazon.com/api/skill/link/M2TEST",
            "https://alexa.amazon.co.jp/api/skill/link/M2TEST",
        ])

    def test_server_starts_without_alexa_configuration(self):
        with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as tmp:
            store = AccountStore(tmp.name)
        self.assertIsNone(oauth_from_env(store, {"ALEXA_OAUTH_CLIENT_ID": "ec-to-alexa"}))
        oauth = oauth_from_env(store, {
            "ALEXA_OAUTH_CLIENT_ID": "ec-to-alexa",
            "ALEXA_VENDOR_ID": "M2TEST",
            "ALEXA_OAUTH_CLIENT_SECRET": "secret",
        })
        self.assertEqual(len(oauth.redirect_uris), 3)
        self.assertEqual(oauth.client_secret, "secret")
        store.close()

    def test_token_endpoint_accepts_http_basic_credentials(self):
        with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as tmp:
            store = AccountStore(tmp.name)
        store.register("a@example.test", "a" * 12)
        uri = "https://layla.amazon.com/api/skill/link/M2TEST"
        OAuthHandler.oauth = OAuthAccountLinking(store, "ec-to-alexa", uri, "s3cret")
        OAuthHandler.skill = AlexaSkill(store)
        code = OAuthHandler.oauth.authorize("a@example.test", "a" * 12, uri)
        server = ThreadingHTTPServer(("127.0.0.1", 0), OAuthHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            body = f"grant_type=authorization_code&code={code}&redirect_uri={uri}".encode()
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/oauth/token", data=body, method="POST",
                headers={"Authorization": "Basic " + b64("ec-to-alexa:s3cret")},
            )
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(request, timeout=5) as response:
                self.assertIn("access_token", json.load(response))
        finally:
            server.shutdown()
            server.server_close()
            store.close()


if __name__ == "__main__":
    unittest.main()


class AssessmentTests(unittest.TestCase):
    def test_assessments_within_horizon_are_sorted(self):
        from alexa_speech import describe_assessments
        result = {"data": {
            "2026-11-05": [{"matiere": "MATHEMATIQUES", "interrogation": True},
                           {"matiere": "ANGLAIS", "interrogation": False}],
            "2026-11-03": [{"matiere": "HISTOIRE-GEOGRAPHIE", "interrogation": True}],
            "2026-12-20": [{"matiere": "SVT", "interrogation": True}],
        }}
        text = describe_assessments(result, TODAY)
        self.assertTrue(text.startswith("Tu as 2 évaluations"))
        self.assertLess(text.index("Histoire"), text.index("Mathématiques"))
        self.assertNotIn("Svt", text)
        self.assertIn("Aucune évaluation", describe_assessments({"data": {}}, TODAY))


class RealLabelTests(unittest.TestCase):
    """Intitulés et salles tels que renvoyés par un vrai compte École Directe (bac pro)."""

    def test_abbreviated_subjects_and_rooms_are_speakable(self):
        result = {"data": [
            {"start_date": "2026-11-03 08:10", "end_date": "2026-11-03 10:10",
             "matiere": "Pratique.profession.", "salle": "C305 BAC PRO CIEL"},
            {"start_date": "2026-11-03 10:20", "end_date": "2026-11-03 12:20",
             "matiere": "Francais", "salle": "B309 38pl"},
            {"start_date": "2026-11-03 14:00", "end_date": "2026-11-03 15:00",
             "matiere": "Economie-gestion", "salle": "B309 38pl", "isAnnule": True},
            {"start_date": "2026-11-03 15:40", "end_date": "2026-11-03 17:00",
             "matiere": "Prevent.-sante-env.", "salle": "B309 38pl"},
        ]}
        text = describe_timetable(result, date(2026, 11, 3), TODAY)
        self.assertIn("À 8 heures 10, Pratique professionnelle en salle C305", text)
        self.assertIn("Français en salle B309", text)
        self.assertIn("Prévention santé environnement en salle B309", text)
        self.assertIn("le cours d'Économie gestion de 14 heures est annulé", text)
        self.assertNotIn("38pl", text)
        self.assertNotIn("BAC PRO", text)
