"""Vérification des requêtes Alexa avec une chaîne de certificats générée localement."""

import base64
import json
import unittest
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID

from alexa_verify import AlexaRequestVerifier, AlexaVerificationError, validate_cert_url

NOW = datetime(2026, 11, 2, 17, 0, tzinfo=timezone.utc)
CERT_URL = "https://s3.amazonaws.com/echo.api/echo-api-cert.pem"


def _key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _cert(subject, issuer, public_key, signing_key, *, ca, san=None, days=30):
    builder = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject)]))
        .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, issuer)]))
        .public_key(public_key)
        .serial_number(x509.random_serial_number())
        .not_valid_before(NOW - timedelta(days=1))
        .not_valid_after(NOW + timedelta(days=days))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
    )
    if san:
        builder = builder.add_extension(
            x509.SubjectAlternativeName([x509.DNSName(name) for name in san]), critical=False
        )
    return builder.sign(signing_key, hashes.SHA256())


def _pem(*certs):
    return b"".join(cert.public_bytes(serialization.Encoding.PEM) for cert in certs)


class Chain:
    def __init__(self, san=("echo-api.amazon.com",)):
        self.root_key, self.inter_key, self.leaf_key = _key(), _key(), _key()
        self.root = _cert("Test Root", "Test Root", self.root_key.public_key(), self.root_key, ca=True)
        self.inter = _cert("Test Inter", "Test Root", self.inter_key.public_key(), self.root_key, ca=True)
        self.leaf = _cert(
            "echo-api.amazon.com", "Test Inter", self.leaf_key.public_key(), self.inter_key,
            ca=False, san=san,
        )

    def sign(self, body: bytes) -> str:
        return base64.b64encode(
            self.leaf_key.sign(body, padding.PKCS1v15(), hashes.SHA256())
        ).decode()


def envelope(skill="amzn1.ask.skill.test", timestamp=NOW):
    return {
        "version": "1.0",
        "context": {"System": {"application": {"applicationId": skill}}},
        "request": {"type": "LaunchRequest", "timestamp": timestamp.isoformat()},
    }


class AlexaSignatureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chain = Chain()

    def verifier(self, chain=None, roots=None):
        chain = chain or self.chain
        self.fetched = []

        def fetch(url):
            self.fetched.append(url)
            return _pem(chain.leaf, chain.inter)

        return AlexaRequestVerifier(
            "amzn1.ask.skill.test",
            fetch=fetch,
            roots=roots if roots is not None else [chain.root],
            clock=NOW.timestamp,
        )

    def request(self, chain=None, **kwargs):
        chain = chain or self.chain
        body = json.dumps(envelope(**kwargs)).encode()
        headers = {"Signature-256": chain.sign(body), "SignatureCertChainUrl": CERT_URL}
        return headers, body, json.loads(body)

    def test_valid_request_is_accepted_and_certificate_cached(self):
        verifier = self.verifier()
        verifier.verify(*self.request())
        verifier.verify(*self.request())
        self.assertEqual(self.fetched, [CERT_URL])

    def test_tampered_body_is_rejected(self):
        headers, body, parsed = self.request()
        with self.assertRaises(AlexaVerificationError):
            self.verifier().verify(headers, body.replace(b"Launch", b"Intent"), parsed)

    def test_missing_signature_is_rejected(self):
        headers, body, parsed = self.request()
        del headers["Signature-256"]
        with self.assertRaises(AlexaVerificationError):
            self.verifier().verify(headers, body, parsed)

    def test_untrusted_root_is_rejected(self):
        other = Chain()
        with self.assertRaises(AlexaVerificationError):
            self.verifier(roots=[other.root]).verify(*self.request())

    def test_certificate_must_cover_echo_api_domain(self):
        wrong = Chain(san=("example.com",))
        with self.assertRaises(AlexaVerificationError):
            self.verifier(chain=wrong).verify(*self.request(chain=wrong))

    def test_other_skill_and_stale_request_are_rejected(self):
        with self.assertRaises(AlexaVerificationError):
            self.verifier().verify(*self.request(skill="amzn1.ask.skill.autre"))
        with self.assertRaises(AlexaVerificationError):
            self.verifier().verify(*self.request(timestamp=NOW - timedelta(seconds=200)))


class CertUrlTests(unittest.TestCase):
    def test_accepted_urls_are_normalised(self):
        for url in (
            "https://s3.amazonaws.com/echo.api/echo-api-cert.pem",
            "HTTPS://s3.amazonaws.com/echo.api/echo-api-cert.pem",
            "https://s3.amazonaws.com:443/echo.api/echo-api-cert.pem",
            "https://s3.amazonaws.com/echo.api/../echo.api/echo-api-cert.pem",
        ):
            self.assertEqual(validate_cert_url(url), CERT_URL)

    def test_rejected_urls(self):
        for url in (
            "http://s3.amazonaws.com/echo.api/echo-api-cert.pem",
            "https://notamazon.com/echo.api/echo-api-cert.pem",
            "https://s3.amazonaws.com/EcHo.aPi/echo-api-cert.pem",
            "https://s3.amazonaws.com/invalid.path/echo-api-cert.pem",
            "https://s3.amazonaws.com:563/echo.api/echo-api-cert.pem",
            "https://s3.amazonaws.com/echo.api/../evil/cert.pem",
        ):
            with self.assertRaises(AlexaVerificationError, msg=url):
                validate_cert_url(url)


if __name__ == "__main__":
    unittest.main()
