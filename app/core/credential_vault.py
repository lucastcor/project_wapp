"""Encryption at rest for third-party credentials owned by a company.

Per-company WhatsApp business tokens and Google refresh tokens live in the
database next to the tenant that owns them. Storing them in clear text would
turn any backup, log dump or SQL read into a credential leak, so every
secret goes through this module before it is persisted and is decrypted only
at the moment an outbound call needs it.

The key is a Fernet key (base64 of 32 random bytes). Outside development it
must be provided by ``CREDENTIALS_ENCRYPTION_KEY``; in development a key is
derived from the platform session secret so the local flow works without
extra setup, with a warning in the log so nobody mistakes it for production
posture. A stored value carries a version prefix so a future key rotation can
tell which key encrypted it.
"""

from __future__ import annotations

import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import is_missing_or_placeholder, is_production_like, settings
from app.core.logging import get_logger, log_event

logger = get_logger(__name__)

ENCRYPTED_PREFIX = "enc:v1:"
_DEV_FALLBACK_WARNED = False


class CredentialVaultError(Exception):
    """The vault cannot encrypt or decrypt with the current configuration."""


def generate_key() -> str:
    """Return a fresh Fernet key suitable for ``CREDENTIALS_ENCRYPTION_KEY``."""
    return Fernet.generate_key().decode("ascii")


def vault_is_configured() -> bool:
    """True when an explicit, well-formed key is present in the environment."""
    raw = str(settings.credentials_encryption_key or "").strip()
    if is_missing_or_placeholder(raw):
        return False
    try:
        Fernet(raw.encode("ascii"))
    except (ValueError, TypeError):
        return False
    return True


def _resolve_key() -> bytes:
    global _DEV_FALLBACK_WARNED
    raw = str(settings.credentials_encryption_key or "").strip()
    if not is_missing_or_placeholder(raw):
        try:
            Fernet(raw.encode("ascii"))
        except (ValueError, TypeError) as exc:
            raise CredentialVaultError(
                "CREDENTIALS_ENCRYPTION_KEY must be a Fernet key (base64 of 32 bytes); "
                "generate one with app.core.credential_vault.generate_key()."
            ) from exc
        return raw.encode("ascii")
    if is_production_like():
        raise CredentialVaultError(
            "CREDENTIALS_ENCRYPTION_KEY is required to store company credentials outside development."
        )
    seed = str(settings.platform_session_secret or "project-wapp-development").encode("utf-8")
    derived = base64.urlsafe_b64encode(hashlib.sha256(b"credential-vault:" + seed).digest())
    if not _DEV_FALLBACK_WARNED:
        _DEV_FALLBACK_WARNED = True
        log_event(
            logger,
            logging.WARNING,
            "credential_vault.development_key_fallback",
            detail="CREDENTIALS_ENCRYPTION_KEY not set; deriving a development-only key from PLATFORM_SESSION_SECRET",
        )
    return derived


def is_encrypted(value: str | None) -> bool:
    return str(value or "").startswith(ENCRYPTED_PREFIX)


def encrypt_secret(plaintext: str) -> str:
    """Encrypt ``plaintext`` for storage. Empty input is rejected, never stored blank."""
    text = str(plaintext or "")
    if not text.strip():
        raise CredentialVaultError("Refusing to encrypt an empty secret.")
    token = Fernet(_resolve_key()).encrypt(text.encode("utf-8"))
    return ENCRYPTED_PREFIX + token.decode("ascii")


def decrypt_secret(value: str | None) -> str:
    """Decrypt a value produced by :func:`encrypt_secret`.

    A value without the prefix is treated as an error rather than returned as
    is: silently accepting clear text would let an un-encrypted row masquerade
    as a vaulted one and hide a misconfiguration.
    """
    stored = str(value or "")
    if not is_encrypted(stored):
        raise CredentialVaultError("Stored credential is not in the encrypted format expected by the vault.")
    token = stored[len(ENCRYPTED_PREFIX):].encode("ascii")
    try:
        return Fernet(_resolve_key()).decrypt(token).decode("utf-8")
    except InvalidToken as exc:
        raise CredentialVaultError(
            "Stored credential cannot be decrypted with the current CREDENTIALS_ENCRYPTION_KEY; "
            "the key changed or the value was tampered with."
        ) from exc


def mask_secret(value: str | None, *, keep: int = 4) -> str:
    """Show only the tail of an identifier for operator screens."""
    text = str(value or "")
    if not text:
        return ""
    if len(text) <= keep:
        return "•" * len(text)
    return "•" * min(8, len(text) - keep) + text[-keep:]
