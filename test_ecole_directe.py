import json
import unittest
from http.client import RemoteDisconnected
from unittest.mock import patch
from urllib.parse import parse_qs
from uuid import UUID

from ecole_directe import EcoleDirecteClient, EcoleDirecteError


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
        self.assertFalse(payload["isRelogin"])
        UUID(payload["uuid"])
        self.assertEqual(request.get_header("X-gtk"), "test-gtk")

    @patch("ecole_directe.urlopen", side_effect=RemoteDisconnected("serveur ferme la connexion"))
    def test_deconnexion_distante_devient_une_erreur_api(self, mock_urlopen):
        client = EcoleDirecteClient("eleve", "secret")

        with self.assertRaisesRegex(EcoleDirecteError, "Impossible d'obtenir le cookie GTK"):
            client.login()

    @patch(
        "ecole_directe.urlopen",
        side_effect=[
            FakeResponse(),
            FakeResponse({"code": 250, "token": "qcm-token", "data": {"propositions": ["Y2hvaXg="]}}),
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

        result = client.complete_qcm("Y2hvaXg=")

        self.assertEqual(login_result["code"], 250)
        self.assertEqual(qcm["data"]["propositions"], ["Y2hvaXg="])
        self.assertEqual(result["token"], "final-token")
        self.assertEqual(mock_urlopen.call_count, 6)
        answer_request = mock_urlopen.call_args_list[3].args[0]
        answer_payload = json.loads(parse_qs(answer_request.data.decode())["data"][0])
        self.assertEqual(answer_payload, {"choix": "Y2hvaXg="})
        relogin_request = mock_urlopen.call_args_list[5].args[0]
        relogin_payload = json.loads(parse_qs(relogin_request.data.decode())["data"][0])
        self.assertEqual(relogin_payload["fa"], [{"cn": "cn-value", "cv": "cv-value"}])


if __name__ == "__main__":
    unittest.main()