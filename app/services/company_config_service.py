"""Configuração efetiva de uma empresa.

Fonte operacional é a versão publicada em `company_context_versions`. Os JSONs
em `app/data/company_contexts/` são seeds de bootstrap: são importados uma vez
para o banco e deixam de ser a fonte mutável.

Nunca há queda silenciosa para o contexto global genérico em produção: uma
empresa sem contexto próprio responderia com identidade errada.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from app.core.company_context import (
    CompanyContextError,
    get_seed_company_context,
    has_seed_company_context,
    validate_company_context,
)
from app.core.config import is_production_like
from app.core.logging import get_logger, log_event
from app.core.tenancy import require_company_id
from app.core.vertical_packs import (
    merge_capabilities,
    merge_policies,
    resolve_vertical_pack,
)
from app.storage import company_config_repository as config_repository
from app.storage.repository import RuntimeRepository

logger = get_logger(__name__)
repository = RuntimeRepository()

CACHE_TTL_SECONDS = 30


class CompanyConfigError(Exception):
    pass


@dataclass(frozen=True)
class CompanyConfig:
    company_id: int
    company_slug: str | None
    company_name: str | None
    business_type: str | None
    vertical_pack: str
    version: int | None
    status: str
    source: str
    context: dict[str, Any] = field(default_factory=dict)
    capabilities: dict[str, bool] = field(default_factory=dict)
    policies: dict[str, Any] = field(default_factory=dict)

    def has_capability(self, name: str) -> bool:
        return bool(self.capabilities.get(name, False))

    def policy(self, name: str, default: Any = None) -> Any:
        return self.policies.get(name, default)


_CACHE: dict[int, tuple[float, CompanyConfig]] = {}
_CACHE_LOCK = threading.Lock()


def invalidate_company_config(company_id: int | None = None) -> None:
    """Invalida o cache local. Chamado após publicar uma versão."""
    with _CACHE_LOCK:
        if company_id is None:
            _CACHE.clear()
            return
        _CACHE.pop(int(company_id), None)


def _cached(company_id: int) -> CompanyConfig | None:
    with _CACHE_LOCK:
        entry = _CACHE.get(company_id)
    if not entry:
        return None
    stored_at, config = entry
    # TTL curto porque o banco é compartilhado entre réplicas: invalidação
    # local sozinha não alcançaria os outros workers.
    if time.monotonic() - stored_at > CACHE_TTL_SECONDS:
        return None
    return config


def _store(company_id: int, config: CompanyConfig) -> None:
    with _CACHE_LOCK:
        _CACHE[company_id] = (time.monotonic(), config)


def _build_config(
    *,
    company: dict[str, Any],
    version_row: dict[str, Any] | None,
    context: dict[str, Any],
    capabilities_override: dict[str, Any] | None,
    policies_override: dict[str, Any] | None,
    source: str,
) -> CompanyConfig:
    business_type = (
        str(company.get("business_type") or "").strip()
        or str(context.get("business_identity", {}).get("business_type") or "").strip()
        or None
    )
    pack = resolve_vertical_pack(business_type)
    return CompanyConfig(
        company_id=int(company["id"]),
        company_slug=str(company.get("slug") or "") or None,
        company_name=str(company.get("name") or "") or None,
        business_type=business_type,
        vertical_pack=pack.key,
        version=int(version_row["version"]) if version_row else None,
        status=str(version_row["status"]) if version_row else "unversioned",
        source=source,
        context=context,
        capabilities=merge_capabilities(business_type, capabilities_override),
        policies=merge_policies(business_type, policies_override),
    )


def bootstrap_company_config(
    *,
    company_id: int,
    author: str = "bootstrap",
    publish: bool = True,
) -> CompanyConfig:
    """Importa o seed JSON da empresa para o banco, se ainda não houver versão.

    Idempotente: se já existir versão publicada, ela é devolvida sem escrita.
    """
    resolved_company_id = require_company_id(company_id, context="company config bootstrap")
    company = repository.get_company(resolved_company_id)
    if not company:
        raise CompanyConfigError(f"Company {resolved_company_id} not found.")

    published = config_repository.get_published_context(company_id=resolved_company_id)
    if published:
        return _config_from_version(company, published, "database")

    company_slug = str(company.get("slug") or "") or None
    if not company_slug or not has_seed_company_context(company_slug):
        raise CompanyConfigError(
            f"No company context seed found for company_id={resolved_company_id}. "
            "Publish a context version before serving traffic."
        )

    context = get_seed_company_context(company_slug)
    if context is None:
        # `has_seed_company_context` já respondeu que existe; chegar aqui com
        # None significa que o arquivo sumiu entre as duas chamadas. Recusar é
        # o comportamento certo — servir tráfego com contexto vazio faria a
        # empresa responder com identidade genérica.
        raise CompanyConfigError(
            f"Company context seed for {company_slug!r} disappeared while bootstrapping."
        )
    business_type = str(company.get("business_type") or "") or None
    capabilities = merge_capabilities(business_type, context.get("capabilities"))
    policies = merge_policies(business_type, context.get("policies"))

    stored = config_repository.create_context_version(
        company_id=resolved_company_id,
        context=context,
        capabilities=capabilities,
        policies=policies,
        vertical_pack=resolve_vertical_pack(business_type).key,
        author=author,
        change_note="Bootstrap from seed JSON",
        status=config_repository.STATUS_PUBLISHED if publish else config_repository.STATUS_DRAFT,
    )
    invalidate_company_config(resolved_company_id)
    log_event(
        logger,
        logging.INFO,
        "company_config.bootstrapped",
        company_id=resolved_company_id,
        version=stored["version"],
    )
    return _config_from_version(company, stored, "seed_bootstrap")


def _config_from_version(
    company: dict[str, Any],
    version_row: dict[str, Any],
    source: str,
) -> CompanyConfig:
    return _build_config(
        company=company,
        version_row=version_row,
        context=version_row["context"],
        capabilities_override=version_row.get("capabilities"),
        policies_override=version_row.get("policies"),
        source=source,
    )


def get_company_config(company_id: int, *, use_cache: bool = True) -> CompanyConfig:
    """Configuração publicada da empresa."""
    resolved_company_id = require_company_id(company_id, context="company config read")

    if use_cache:
        cached = _cached(resolved_company_id)
        if cached is not None:
            return cached

    company = repository.get_company(resolved_company_id)
    if not company:
        raise CompanyConfigError(f"Company {resolved_company_id} not found.")

    published = config_repository.get_published_context(company_id=resolved_company_id)
    if published:
        config = _config_from_version(company, published, "database")
        _store(resolved_company_id, config)
        return config

    company_slug = str(company.get("slug") or "") or None
    if company_slug and has_seed_company_context(company_slug):
        config = bootstrap_company_config(company_id=resolved_company_id)
        _store(resolved_company_id, config)
        return config

    if is_production_like():
        log_event(
            logger,
            logging.ERROR,
            "company_config.missing_in_production",
            company_id=resolved_company_id,
        )
        raise CompanyConfigError(
            f"Company {resolved_company_id} has no published company context. "
            "Refusing to answer with a generic identity in production."
        )

    # Fora de produção mantemos o template global para permitir simulação,
    # marcado explicitamente na origem para não ser confundido com dado real.
    log_event(
        logger,
        logging.WARNING,
        "company_config.global_template_used",
        company_id=resolved_company_id,
    )
    context = get_seed_company_context(None)
    if context is None:
        raise CompanyConfigError(
            "No global company context template is available. "
            "Publish a context version for this company before serving traffic."
        )
    config = _build_config(
        company=company,
        version_row=None,
        context=context,
        capabilities_override=None,
        policies_override=None,
        source="global_template",
    )
    _store(resolved_company_id, config)
    return config


def get_company_context_for(company_id: int | None) -> dict[str, Any]:
    """Contexto publicado de uma empresa, para consumidores legados."""
    if company_id is None:
        template = get_seed_company_context(None)
        if template is None:
            raise CompanyConfigError("No global company context template is available.")
        return template
    return get_company_config(int(company_id)).context


def save_company_config(
    *,
    company_id: int,
    context: dict[str, Any],
    capabilities: dict[str, Any] | None = None,
    policies: dict[str, Any] | None = None,
    author: str | None = None,
    change_note: str | None = None,
    publish: bool = True,
) -> dict[str, Any]:
    """Cria uma nova versão do contexto. Nunca sobrescreve versão anterior."""
    resolved_company_id = require_company_id(company_id, context="company config write")
    company = repository.get_company(resolved_company_id)
    if not company:
        raise CompanyConfigError(f"Company {resolved_company_id} not found.")

    try:
        validate_company_context(context)
    except CompanyContextError as exc:
        raise CompanyConfigError(str(exc)) from exc

    business_type = str(company.get("business_type") or "") or None
    merged_capabilities = merge_capabilities(
        business_type,
        capabilities if capabilities is not None else context.get("capabilities"),
    )
    merged_policies = merge_policies(
        business_type,
        policies if policies is not None else context.get("policies"),
    )

    stored = config_repository.create_context_version(
        company_id=resolved_company_id,
        context=context,
        capabilities=merged_capabilities,
        policies=merged_policies,
        vertical_pack=resolve_vertical_pack(business_type).key,
        author=author,
        change_note=change_note,
        status=(
            config_repository.STATUS_PUBLISHED if publish else config_repository.STATUS_DRAFT
        ),
    )
    invalidate_company_config(resolved_company_id)
    return stored


def publish_company_config(
    *,
    company_id: int,
    version_id: int,
    author: str | None = None,
) -> dict[str, Any]:
    resolved_company_id = require_company_id(company_id, context="company config publish")
    stored = config_repository.publish_context_version(
        company_id=resolved_company_id,
        version_id=version_id,
        author=author,
    )
    if stored is None:
        raise CompanyConfigError(
            f"Context version {version_id} not found for company_id={resolved_company_id}."
        )
    invalidate_company_config(resolved_company_id)
    return stored


def list_company_config_versions(*, company_id: int, limit: int = 50) -> list[dict[str, Any]]:
    resolved_company_id = require_company_id(company_id, context="company config history")
    return config_repository.list_context_versions(company_id=resolved_company_id, limit=limit)


def get_company_config_version(*, company_id: int, version_id: int) -> dict[str, Any] | None:
    resolved_company_id = require_company_id(company_id, context="company config read")
    return config_repository.get_context_version(
        company_id=resolved_company_id,
        version_id=version_id,
    )
