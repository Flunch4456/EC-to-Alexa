import tempfile
import unittest
from cryptography.fernet import Fernet

from application import AccountStore, AlexaSkill, OAuthAccountLinking


class AccountStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self.store = AccountStore(self.tmp.name)

    def tearDown(self):
        self.store.close()

    def test_passwords_are_hashed_and_tokens_are_scoped(self):
        user_id = self.store.register("a@example.test", "a" * 12)
        row = self.store.db.execute("SELECT password_hash FROM users").fetchone()
        self.assertNotIn("a" * 12, row[0])
        token = self.store.login("a@example.test", "a" * 12)
        self.assertEqual(self.store.user_for_token(token), user_id)
        with self.assertRaises(PermissionError):
            self.store.user_for_token("not-a-token")

    def test_alexa_token_is_separate_from_web_session(self):
        user_id = self.store.register("a@example.test", "a" * 12)
        token = self.store.create_alexa_token(user_id)
        self.assertEqual(self.store.user_for_token(token, "alexa_tokens"), user_id)
        with self.assertRaises(PermissionError):
            self.store.user_for_token(token)

    def test_ecole_directe_credentials_are_encrypted_at_rest(self):
        key = Fernet.generate_key()
        with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as tmp:
            store = AccountStore(tmp.name, key)
            user_id = store.register("a@example.test", "a" * 12)
            store.save_ecole_directe_connection(user_id, "eleve", "secret", "123")
            raw = store.db.execute(
                "SELECT encrypted_credentials FROM ecole_directe_connections"
            ).fetchone()[0]
            self.assertNotIn(b"secret", raw)
            client = store.ecole_directe_client(user_id)
            self.assertEqual(client.identifiant, "eleve")
            self.assertEqual(client.mot_de_passe, "secret")
            store.close()

    def test_account_export_and_delete_remove_private_connections(self):
        key = Fernet.generate_key()
        with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as tmp:
            store = AccountStore(tmp.name, key)
            user_id = store.register("a@example.test", "a" * 12)
            store.save_ecole_directe_connection(user_id, "eleve", "secret", "123")
            self.assertTrue(store.export_account(user_id)["ecole_directe_connected"])
            store.delete_account(user_id)
            with self.assertRaises(PermissionError):
                store.export_account(user_id)
            self.assertEqual(
                store.db.execute("SELECT COUNT(*) FROM ecole_directe_connections").fetchone()[0],
                0,
            )
            store.close()

    def test_student_identity_isolated_per_internal_account(self):
        key = Fernet.generate_key()
        with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as tmp:
            store = AccountStore(tmp.name, key)
            first = store.register("first@example.test", "a" * 12)
            second = store.register("second@example.test", "b" * 12)
            store.save_ecole_directe_connection(first, "first", "secret1", "101")
            store.save_ecole_directe_connection(second, "second", "secret2", "202")
            self.assertEqual(store.ecole_directe_info(first)["student_id"], "101")
            self.assertEqual(store.ecole_directe_info(second)["student_id"], "202")
            store.close()


class AlexaSkillTests(unittest.TestCase):
    def test_unknown_or_unconnected_account_does_not_leak_data(self):
        with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as tmp:
            store = AccountStore(tmp.name)
            user_id = store.register("a@example.test", "a" * 12)
            token = store.create_alexa_token(user_id)
            response = AlexaSkill(store, {}).handle(
                token, {"request": {"intent": {"name": "GetMessagesIntent"}}}
            )
            self.assertIn("n'est pas connecté", response["response"]["outputSpeech"]["ssml"])
            store.close()


class OAuthAccountLinkingTests(unittest.TestCase):
    def test_authorization_code_is_single_use_and_user_scoped(self):
        with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as tmp:
            store = AccountStore(tmp.name)
            user_id = store.register("a@example.test", "a" * 12)
            oauth = OAuthAccountLinking(
                store, "alexa-client", "https://example.test/oauth/callback"
            )
            code = oauth.authorize("a@example.test", "a" * 12)
            token = oauth.token(
                code, "alexa-client", "https://example.test/oauth/callback"
            )["access_token"]
            self.assertEqual(store.user_for_token(token, "alexa_tokens"), user_id)
            with self.assertRaises(PermissionError):
                oauth.token(code, "alexa-client", "https://example.test/oauth/callback")
            store.close()

    def test_oauth_rejects_non_https_redirect_uri(self):
        with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as tmp:
            store = AccountStore(tmp.name)
            with self.assertRaises(ValueError):
                OAuthAccountLinking(store, "client", "http://localhost/callback")
            store.close()


if __name__ == "__main__":
    unittest.main()
