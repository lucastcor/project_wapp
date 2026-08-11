"""Resolução confiável de empresa para superfícies públicas.

Regra: em produção o tenant vem de um vínculo verificável entre o canal do
provider e a empresa (`company_channel_bindings`). `company_id`/`company_slug`
enviados pelo chamador são apenas dicas e só são aceitos fora de produção,
onde não há canal real configurado.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.core.config import is_production_like, settings
from app.core.logging import get_logger, log_event
from app.storage.channel_repository import resolve_company_id_for_channel
from app.storage.repository import RuntimeRepository

logger = get_logger(__name__)
repository = RuntimeRepository()


class TenantResolutionError(Exception):
    """Levantada quando não é possível resolver a empresa com segurança."""


@dataclass(frozen=True)
class TenantResolution:
    company_id: int
    company_slug: str | None
    source: str


def _resolution_from_company_id(company_id: int, source: str) -> TenantResolution:
    company = repository.get_company(company_id)
    if not company:
        raise TenantResolutionError("Resolved company does not exist.")
    if not int(company.get("active") or 0):
        raise TenantResolutionError("Resolved company is not active.")
    return TenantResolution(
        company_id=int(company["id"]),
        company_slug=str(company.get("slug") or "") or None,
        source=source,
    )


def resolve_channel_tenant(
    *,
    provider: str,
    external_id: str | None,
    requested_company_id: int | None = None,
    requested_company_slug: str | None = None,
) -> TenantResolution:
    """Resolve a empresa de uma mensagem recebida por um canal de provider."""
    bound_company_id = resolve_company_id_for_channel(provider=provider, external_id=external_id)
    if bound_company_id is not None:
        return _resolution_from_company_id(bound_company_id, "channel_binding")

    if is_production_like() or not settings.allow_untrusted_tenant_hints:
        log_event(
            logger,
            logging.ERROR,
            "tenant.resolution.unbound_channel",
            provider=provider,
            has_external_id=bool(str(external_id or "").strip()),
        )
        raise TenantResolutionError(
            "No active channel binding for this provider channel. "
            "Register the binding before receiving production traffic."
        )

    hinted_company_id = repository.resolve_company_id(
        company_id=requested_company_id,
        company_slug=requested_company_slug,
    )
    if hinted_company_id is None and requested_company_id is None and not requested_company_slug:
        # Fora de produção mantemos a ergonomia histórica de bootstrap local.
        hinted_company_id = repository.ensure_default_company()
    if hinted_company_id is None:
        raise TenantResolutionError("Unable to resolve company from request hints.")

    log_event(
        logger,
        logging.WARNING,
        "tenant.resolution.untrusted_hint_used",
        provider=provider,
        company_id=hinted_company_id,
    )
    return _resolution_from_company_id(hinted_company_id, "untrusted_hint")


def resolve_api_tenant(
    *,
    requested_company_id: int | None = None,
    requested_company_slug: str | None = None,
) -> TenantResolution:
    """Resolve a empresa de uma chamada direta à API interna de mensagens.

    Em produção exige um identificador explícito e existente; não há queda
    silenciosa para "primeira empresa ativa".
    """
    if requested_company_id is None and not requested_company_slug:
        if is_production_like():
            raise TenantResolutionError("company_id or company_slug is required in production.")
        default_company_id = repository.resolve_company_id() or repository.ensure_default_company()
        return _resolution_from_company_id(default_company_id, "default_company")

    resolved_company_id = repository.resolve_company_id(
        company_id=requested_company_id,
        company_slug=requested_company_slug,
    )
    if resolved_company_id is None:
        raise TenantResolutionError("company_id or company_slug does not match an existing company.")

    return _resolution_from_company_id(resolved_company_id, "explicit_request")
