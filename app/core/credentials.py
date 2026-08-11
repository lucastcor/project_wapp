"""Primitivas de credencial.

Regras:
- o token em claro existe apenas no instante da emissão e é devolvido uma
  única vez ao operador;
- só o hash é persistido;
- comparação sempre em tempo constante;
- o prefixo guardado serve para identificar a credencial em auditoria sem
  permitir reconstruir o segredo.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass

TOKEN_BYTES = 32
TOKEN_PREFIX_LENGTH = 8


@dataclass(frozen=True)
class IssuedToken:
    """Resultado da emissão. `token` nunca deve ser persistido nem logado."""

    token: str
    token_hash: str
    token_prefix: str


def hash_token(token: str) -> str:
    """Hash do token.

    Tokens são gerados com 256 bits de entropia, então SHA-256 direto é
    adequado: não há espaço de busca que torne um KDF lento necessário.
    """
    return hashlib.sha256(str(token or "").strip().encode("utf-8")).hexdigest()


def issue_token(*, prefix: str) -> IssuedToken:
    secret = secrets.token_urlsafe(TOKEN_BYTES)
    token = f"{prefix}_{secret}" if prefix else secret
    return IssuedToken(
        token=token,
        token_hash=hash_token(token),
        token_prefix=token[:TOKEN_PREFIX_LENGTH],
    )


def tokens_match(candidate: str | None, expected_hash: str | None) -> bool:
    if not candidate or not expected_hash:
        return False
    return hmac.compare_digest(hash_token(candidate), str(expected_hash))


def mask_secret(value: str | None, *, keep: int = 4) -> str:
    """Representação segura de um identificador sensível para exibição."""
    normalized = str(value or "")
    if not normalized:
        return ""
    if len(normalized) <= keep:
        return "*" * len(normalized)
    return f"{'*' * (len(normalized) - keep)}{normalized[-keep:]}"


def extract_bearer_token(authorization: str | None) -> str | None:
    normalized = str(authorization or "").strip()
    if not normalized.lower().startswith("bearer "):
        return None
    token = normalized[7:].strip()
    return token or None
