"""Vérification des requêtes envoyées par Alexa à un endpoint HTTPS.

Implémente les règles publiées par Amazon pour un skill hébergé comme service
web (« Host a Custom Skill as a Web Service ») :

* l'URL ``SignatureCertChainUrl`` doit être en ``https``, sur l'hôte
  ``s3.amazonaws.com``, port 443, chemin commençant par ``/echo.api/`` ;
* le certificat de signature doit être valide à la date courante, contenir
  ``echo-api.amazon.com`` dans son SAN et remonter à une autorité racine de
  confiance ;
* la signature ``Signature-256`` (SHA-256, PKCS#1 v1.5) doit correspondre au
  corps brut de la requête ;
* l'horodatage ``request.timestamp`` doit être à moins de 150 secondes ;
* l'identifiant du skill dans le corps doit correspondre à ``ALEXA_SKILL_ID``.
"""

from __future__ import annotations

import base64
import binascii
import posixpath
import threading
import time
import warnings
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.x509.oid import ExtensionOID

SIGNING_DOMAIN = "echo-api.amazon.com"
TIMESTAMP_TOLERANCE = 150
MAX_CERT_CHAIN_SIZE = 65_536
CERT_CACHE_TTL = 3_600


class AlexaVerificationError(PermissionError):
    """La requête ne provient pas d'Alexa ou n'est plus valide."""


def validate_cert_url(url: str) -> str:
    """Retourne l'URL normalisée si elle respecte les règles Amazon."""
    if not url:
        raise AlexaVerificationError("SignatureCertChainUrl manquant.")
    parts = urlsplit(url)
    if parts.scheme.lower() != "https":
        raise AlexaVerificationError("Le certificat Alexa doit être servi en HTTPS.")
    if (parts.hostname or "").lower() != "s3.amazonaws.com":
        raise AlexaVerificationError("Hôte du certificat Alexa non autorisé.")
    try:
        port = parts.port
    except ValueError as error:
        raise AlexaVerificationError("Port du certificat Alexa invalide.") from error
    if port not in (None, 443):
        raise AlexaVerificationError("Port du certificat Alexa non autorisé.")
    raw_path = parts.path or "/"
    while "//" in raw_path:
        raw_path = raw_path.replace("//", "/")
    path = posixpath.normpath(raw_path)
    if not path.startswith("/echo.api/"):
        raise AlexaVerificationError("Chemin du certificat Alexa non autorisé.")
    return f"https://s3.amazonaws.com{path}"


def _load_pem_bundle(data: bytes) -> list[x509.Certificate]:
    """Charge chaque bloc PEM séparément en ignorant ceux qui sont illisibles."""
    certificates = []
    marker = b"-----END CERTIFICATE-----"
    for block in data.split(marker):
        start = block.find(b"-----BEGIN CERTIFICATE-----")
        if start == -1:
            continue
        try:
            with warnings.catch_warnings():
                # Certaines racines historiques ont un numéro de série non conforme.
                warnings.simplefilter("ignore")
                certificates.append(x509.load_pem_x509_certificate(block[start:] + marker))
        except ValueError:
            continue
    return certificates


def load_trusted_roots() -> list[x509.Certificate]:
    """Autorités racines : certifi si disponible, sinon le magasin OpenSSL système."""
    candidates: list[str] = []
    try:
        import certifi

        candidates.append(certifi.where())
    except ImportError:
        pass
    import ssl

    paths = ssl.get_default_verify_paths()
    candidates.extend(p for p in (paths.cafile, paths.openssl_cafile) if p)
    candidates.append("/etc/ssl/certs/ca-certificates.crt")
    for candidate in candidates:
        try:
            with open(candidate, "rb") as file:
                roots = _load_pem_bundle(file.read())
        except OSError:
            continue
        if roots:
            return roots
    raise RuntimeError("Aucun magasin d'autorités racines n'est disponible (installez certifi).")


def _fetch_chain(url: str) -> bytes:
    request = Request(url, headers={"Accept": "application/x-pem-file"})
    with urlopen(request, timeout=5) as response:
        data = response.read(MAX_CERT_CHAIN_SIZE + 1)
    if len(data) > MAX_CERT_CHAIN_SIZE:
        raise AlexaVerificationError("Chaîne de certificats Alexa trop volumineuse.")
    return data


def _is_valid_at(certificate: x509.Certificate, now: datetime) -> bool:
    return certificate.not_valid_before_utc <= now <= certificate.not_valid_after_utc


class AlexaRequestVerifier:
    """Vérifie signature, certificat, horodatage et skill ID d'une requête Alexa."""

    def __init__(
        self,
        skill_id: str,
        *,
        verify_signature: bool = True,
        fetch: Callable[[str], bytes] = _fetch_chain,
        roots: list[x509.Certificate] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not skill_id:
            raise ValueError("ALEXA_SKILL_ID est requis pour accepter des requêtes Alexa.")
        self.skill_id = skill_id
        self.verify_signature = verify_signature
        self._fetch = fetch
        self._roots = roots
        self._clock = clock
        self._cache: dict[str, tuple[float, x509.Certificate]] = {}
        self._lock = threading.Lock()

    # -- Point d'entrée -------------------------------------------------
    def verify(self, headers: Any, body: bytes, envelope: dict[str, Any]) -> None:
        self._check_skill_id(envelope)
        self._check_timestamp(envelope)
        if self.verify_signature:
            signature = headers.get("Signature-256", "")
            if not signature:
                raise AlexaVerificationError("En-tête Signature-256 manquant.")
            certificate = self._signing_certificate(headers.get("SignatureCertChainUrl", ""))
            self._check_signature(certificate, signature, body)

    # -- Contrôles du corps ---------------------------------------------
    def _check_skill_id(self, envelope: dict[str, Any]) -> None:
        application_id = (
            ((envelope.get("context") or {}).get("System") or {}).get("application") or {}
        ).get("applicationId") or ((envelope.get("session") or {}).get("application") or {}).get(
            "applicationId"
        )
        if application_id != self.skill_id:
            raise AlexaVerificationError("Identifiant de skill inattendu.")

    def _check_timestamp(self, envelope: dict[str, Any]) -> None:
        raw = (envelope.get("request") or {}).get("timestamp", "")
        try:
            stamp = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError as error:
            raise AlexaVerificationError("Horodatage Alexa invalide.") from error
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        if abs(self._clock() - stamp.timestamp()) > TIMESTAMP_TOLERANCE:
            raise AlexaVerificationError("Requête Alexa périmée.")

    # -- Certificat -----------------------------------------------------
    def _signing_certificate(self, url: str) -> x509.Certificate:
        normalized = validate_cert_url(url)
        now_ts = self._clock()
        with self._lock:
            cached = self._cache.get(normalized)
        now = datetime.fromtimestamp(now_ts, timezone.utc)
        if cached and cached[0] > now_ts and _is_valid_at(cached[1], now):
            return cached[1]
        try:
            chain = _load_pem_bundle(self._fetch(normalized))
        except (HTTPError, URLError, TimeoutError, OSError) as error:
            raise AlexaVerificationError("Certificat Alexa inaccessible.") from error
        if not chain:
            raise AlexaVerificationError("Chaîne de certificats Alexa vide.")
        self._validate_chain(chain, now)
        with self._lock:
            self._cache[normalized] = (now_ts + CERT_CACHE_TTL, chain[0])
        return chain[0]

    def _validate_chain(self, chain: list[x509.Certificate], now: datetime) -> None:
        leaf = chain[0]
        try:
            san = leaf.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME)
            names = san.value.get_values_for_type(x509.DNSName)
        except x509.ExtensionNotFound as error:
            raise AlexaVerificationError("Certificat Alexa sans SAN.") from error
        if SIGNING_DOMAIN not in names:
            raise AlexaVerificationError("Le certificat ne couvre pas echo-api.amazon.com.")
        for certificate in chain:
            if not _is_valid_at(certificate, now):
                raise AlexaVerificationError("Certificat Alexa expiré ou pas encore valide.")
        try:
            for child, parent in zip(chain, chain[1:]):
                child.verify_directly_issued_by(parent)
        except (ValueError, TypeError, InvalidSignature) as error:
            raise AlexaVerificationError("Chaîne de certificats Alexa incohérente.") from error
        roots = self._roots if self._roots is not None else load_trusted_roots()
        last = chain[-1]
        fingerprints = {root.fingerprint(hashes.SHA256()) for root in roots}
        if last.fingerprint(hashes.SHA256()) in fingerprints:
            return
        for root in roots:
            if root.subject != last.issuer or not _is_valid_at(root, now):
                continue
            try:
                last.verify_directly_issued_by(root)
                return
            except (ValueError, TypeError, InvalidSignature):
                continue
        raise AlexaVerificationError("La chaîne Alexa ne remonte pas à une autorité de confiance.")

    @staticmethod
    def _check_signature(certificate: x509.Certificate, signature: str, body: bytes) -> None:
        try:
            certificate.public_key().verify(  # type: ignore[union-attr]
                base64.b64decode(signature, validate=True),
                body,
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
        except (InvalidSignature, binascii.Error, ValueError, TypeError) as error:
            raise AlexaVerificationError("Signature Alexa invalide.") from error
