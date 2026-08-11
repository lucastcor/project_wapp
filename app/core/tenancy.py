"""Invariantes de escopo por empresa (tenant).

Toda leitura ou escrita de dado privado precisa de um `company_id` resolvido.
Este módulo concentra a invariante para que o isolamento seja explícito no
contrato dos serviços e das queries, e não uma filtragem posterior em memória.
"""

from __future__ import annotations


class TenantScopeError(Exception):
    """Levantada quando um acesso tenant-scoped não recebeu company_id válido."""


def require_company_id(company_id: int | None, *, context: str) -> int:
    """Valida e normaliza o company_id de um acesso tenant-scoped."""
    if company_id is None:
        raise TenantScopeError(f"company_id is required for {context}.")

    try:
        normalized = int(company_id)
    except (TypeError, ValueError) as exc:
        raise TenantScopeError(f"company_id must be an integer for {context}.") from exc

    if normalized <= 0:
        raise TenantScopeError(f"company_id must be a positive integer for {context}.")

    return normalized


def optional_company_id(company_id: int | None) -> int | None:
    """Normaliza um company_id opcional usado em consultas administrativas."""
    if company_id is None:
        return None

    try:
        normalized = int(company_id)
    except (TypeError, ValueError):
        return None

    return normalized if normalized > 0 else None
