"""Credentials owned by a company never reach the database in clear text."""

import pytest
from cryptography.fernet import Fernet

from app.core import credential_vault
from app.core.config import settings
from app.core.credential_vault import (
    ENCRYPTED_PREFIX,
    CredentialVaultError,
    decrypt_secret,
    encrypt_secret,
    generate_key,
    is_encrypted,
    mask_secret,
    vault_is_configured,
)


def test_roundtrip_with_explicit_key(monkeypatch) -> None:
    monkeypatch.setattr(settings, "credentials_encryption_key", generate_key())
    stored = encrypt_secret("EAAB-business-token")
    assert stored.startswith(ENCRYPTED_PREFIX)
    assert "EAAB" not in stored
    assert is_encrypted(stored)
    assert decrypt_secret(stored) == "EAAB-business-token"
    assert vault_is_configured()


def test_development_falls_back_to_a_derived_key_but_production_refuses(monkeypatch) -> None:
    monkeypatch.setattr(settings, "credentials_encryption_key", "")
    monkeypatch.setattr(settings, "platform_session_secret", "dev-secret")
    monkeypatch.setattr(settings, "environment", "development")
    monkeypatch.setattr(settings, "strict_runtime_validation", False)
    monkeypatch.setattr(credential_vault, "_DEV_FALLBACK_WARNED", False)
    stored = encrypt_secret("refresh-token")
    assert decrypt_secret(stored) == "refresh-token"
    assert not vault_is_configured()

    monkeypatch.setattr(settings, "environment", "production")
    with pytest.raises(CredentialVaultError):
        encrypt_secret("refresh-token")
    with pytest.raises(CredentialVaultError):
        decrypt_secret(stored)


def test_wrong_key_and_clear_text_are_rejected(monkeypatch) -> None:
    monkeypatch.setattr(settings, "credentials_encryption_key", generate_key())
    stored = encrypt_secret("secret")
    monkeypatch.setattr(settings, "credentials_encryption_key", Fernet.generate_key().decode())
    with pytest.raises(CredentialVaultError):
        decrypt_secret(stored)
    with pytest.raises(CredentialVaultError):
        decrypt_secret("secret-in-clear-text")
    with pytest.raises(CredentialVaultError):
        encrypt_secret("   ")


def test_malformed_key_is_reported_not_used(monkeypatch) -> None:
    monkeypatch.setattr(settings, "credentials_encryption_key", "not-a-fernet-key")
    assert not vault_is_configured()
    with pytest.raises(CredentialVaultError):
        encrypt_secret("secret")


def test_mask_secret_keeps_only_the_tail() -> None:
    assert mask_secret("123456789012", keep=4) == "••••••••9012"
    assert mask_secret("abc") == "•••"
    assert mask_secret("") == ""
