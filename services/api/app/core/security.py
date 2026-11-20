"""Primitives de sécurité de l'identité : mots de passe (Argon2id), jetons opaques et empreintes HMAC.

- Les jetons (sessions, vérification, réinitialisation, invitations) font 256 bits et ne sont
  jamais stockés : seule leur empreinte HMAC-SHA-256 (clé TOKEN_HASH_KEY) l'est.
- La politique de mot de passe suit les recommandations NIST SP 800-63B : longueur, refus des
  mots de passe courants ou dérivés de l'adresse, aucune règle de composition arbitraire.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import unicodedata

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 128

_hasher = PasswordHasher()  # Argon2id, paramètres par défaut d'argon2-cffi (RFC 9106, profil mémoire réduite)
# Empreinte factice : vérifiée quand le compte n'existe pas, pour un temps de réponse comparable.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))

# Mots de passe très courants (FR/EN) et motifs refusés ; liste locale, aucune requête externe.
_COMMON = {
    "password",
    "password123",
    "motdepasse",
    "motdepasse123",
    "azertyuiop",
    "azertyuiop123",
    "qwertyuiop",
    "qwertyuiop123",
    "123456789012",
    "1234567890123",
    "000000000000",
    "111111111111",
    "abcdefghijkl",
    "iloveyou1234",
    "jetaime12345",
    "bonjour12345",
    "soleil123456",
    "infirmiere123",
    "infirmier1234",
    "nurselearn123",
    "nurselearnai",
    "welcome12345",
    "bienvenue123",
    "changeme1234",
    "letmein12345",
    "administrator",
    "passw0rd1234",
    "p@ssw0rd1234",
    "football1234",
    "qwerty123456",
    "azerty123456",
}


def normalize_password(password: str) -> str:
    return unicodedata.normalize("NFKC", password)


def password_problems(password: str, email: str | None = None) -> list[str]:
    """Codes d'erreur de la politique (liste vide = accepté)."""
    pw = normalize_password(password)
    problems: list[str] = []
    if len(pw) < PASSWORD_MIN_LENGTH:
        problems.append("password_too_short")
    if len(pw) > PASSWORD_MAX_LENGTH:
        problems.append("password_too_long")
    lowered = pw.lower()
    if lowered in _COMMON or len(set(lowered)) <= 2:
        problems.append("password_too_common")
    if email:
        local = email.split("@", 1)[0].lower()
        if len(local) >= 4 and local in lowered:
            problems.append("password_contains_email")
    return problems


def hash_password(password: str) -> str:
    return _hasher.hash(normalize_password(password))


def verify_password(stored_hash: str | None, password: str) -> bool:
    """Vérifie en temps comparable, que le compte existe (empreinte réelle) ou non (factice)."""
    try:
        return _hasher.verify(stored_hash or _DUMMY_HASH, normalize_password(password)) and stored_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(stored_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(stored_hash)
    except InvalidHashError:
        return False


def new_token() -> str:
    """Jeton opaque de 256 bits, sûr pour une URL."""
    return secrets.token_urlsafe(32)


def token_digest(token: str, key: bytes) -> str:
    """Empreinte HMAC-SHA-256 stockée à la place du jeton."""
    return hmac.new(key, token.encode(), hashlib.sha256).hexdigest()


def pseudonymize(value: str, key: bytes) -> str:
    """Empreinte courte d'une donnée personnelle pour les journaux (ex. courriel tenté)."""
    return hmac.new(key, f"pseudo:{value.lower()}".encode(), hashlib.sha256).hexdigest()[:16]
