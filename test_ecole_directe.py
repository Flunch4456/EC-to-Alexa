import json
import unittest
from http.client import RemoteDisconnected
from unittest.mock import patch
from urllib.parse import parse_qs
from uuid import UUID

from ecole_directe import EcoleDirecteClient, EcoleDirecteError, redact_response


class FakeResponse:
    def __init__(self, payload=None, headers=None):
        self.headers = headers or {"Set-Cookie": "GTK=test-gtk; Path=/"}
        self.payload = payload or {"code": 200, "token": "test-token", "data": {}}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


class EcoleDirecteClientTests(unittest.TestCase):
    @patch("ecole_directe.urlopen", return_value=FakeResponse())
    def test_login_transmet_les_identifiants_et_stocke_le_token(self, mock_urlopen):
        client = EcoleDirecteClient("eleve", "secret")

        result = client.login()

        self.assertEqual(result["code"], 200)
        self.assertEqual(client.token, "test-token")
        request = mock_urlopen.call_args.args[0]
        payload = json.loads(parse_qs(request.data.decode())["data"][0])
        self.assertEqual(payload["identifiant"], "eleve")
        self.assertEqual(payload["motdepasse"], "secret")
        self.assertTrue(payload["sesouvenirdemoi"])
        self.assertFalse(payload["isReLogin"])
        self.assertEqual(payload["sesouvenirdemoi"], True)
        UUID(payload["uuid"])
        self.assertEqual(request.get_header("X-gtk"), "test-gtk")

    @patch("ecole_directe.urlopen", side_effect=RemoteDisconnected("serveur ferme la connexion"))
    def test_deconnexion_distante_devient_une_erreur_api(self, mock_urlopen):
        client = EcoleDirecteClient("eleve", "secret")

        with self.assertRaisesRegex(EcoleDirecteError, "Impossible d'obtenir le cookie GTK"):
            client.login()

    def test_redact_response_masque_les_secrets(self):
        result = redact_response({"token": "secret", "data": [{"accessToken": "autre-secret"}]})

        self.assertEqual(result, {"token": "[masque]", "data": [{"accessToken": "[masque]"}]})

    def test_login_recupere_identifiant_de_leleve(self):
        client = EcoleDirecteClient("eleve", "secret")

        student_id = client._find_student_id({"data": {"accounts": [{"id": 12345}]}})

        self.assertEqual(student_id, "12345")

    @patch("ecole_directe.urlopen", return_value=FakeResponse({"code": 200, "token": "new-token", "data": []}))
    def test_une_requete_met_a_jour_le_token(self, mock_urlopen):
        client = EcoleDirecteClient("eleve", "secret")
        client.token = "old-token"

        client.get("eleves/123/absences.awp?verbe=get")

        self.assertEqual(client.token, "new-token")

    @patch(
        "ecole_directe.urlopen",
        side_effect=[
            FakeResponse({"code": 401, "message": "Token invalide !"}),
            FakeResponse(),
            FakeResponse({"code": 200, "token": "login-token", "data": {}}),
            FakeResponse({"code": 200, "token": "refreshed-token", "data": []}),
        ],
    )
    def test_requete_reessaie_apres_token_invalide(self, mock_urlopen):
        client = EcoleDirecteClient("eleve", "secret")
        client.token = "expired-token"

        result = client.get("eleves/123/absences.awp?verbe=get")

        self.assertEqual(result["data"], [])
        self.assertEqual(client.token, "refreshed-token")
        self.assertEqual(mock_urlopen.call_count, 4)

    @patch(
        "ecole_directe.urlopen",
        side_effect=[
            FakeResponse(),
            FakeResponse(
                {"code": 250, "token": "", "data": {}},
                {"Set-Cookie": "GTK=test-gtk; Path=/", "2fa-token": "qcm-token"},
            ),
            FakeResponse({"code": 200, "data": {"question": "UXVlc3Rpb24gPw==", "propositions": ["Y2hvaXg="]}}),
            FakeResponse({"code": 200, "data": {"cn": "cn-value", "cv": "cv-value"}}),
            FakeResponse(),
            FakeResponse({"code": 200, "token": "final-token", "data": {}}),
        ],
    )
    def test_complete_qcm_rejoue_le_login_avec_cn_cv(self, mock_urlopen):
        client = EcoleDirecteClient("eleve", "secret")
        login_result = client.login()
        qcm = client.get_qcm()

        result = client.complete_qcm("choix")

        self.assertEqual(login_result["code"], 250)
        self.assertEqual(qcm["data"]["propositions"], ["choix"])
        self.assertEqual(result["token"], "final-token")
        self.assertEqual(mock_urlopen.call_count, 6)
        answer_request = mock_urlopen.call_args_list[3].args[0]
        answer_payload = json.loads(parse_qs(answer_request.data.decode())["data"][0])
        self.assertEqual(answer_payload, {"choix": "Y2hvaXg="})
        self.assertEqual(answer_request.get_header("2fa-token"), "qcm-token")
        relogin_request = mock_urlopen.call_args_list[5].args[0]
        relogin_payload = json.loads(parse_qs(relogin_request.data.decode())["data"][0])
        self.assertEqual(relogin_payload["cn"], "cn-value")
        self.assertEqual(relogin_payload["cv"], "cv-value")


if __name__ == "__main__":
    unittest.main()