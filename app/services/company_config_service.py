"""Configuração efetiva de uma empresa.

Fonte operacional é a versão publicada em `company_context_versions`. Os JSONs
em `app/data/company_contexts/` são seeds de bootstrap: são importados uma vez
para o banco e deixam de ser a fonte mutável.

Nunca há queda silenciosa para o contexto global genérico em produção: uma
empresa sem contexto próprio responderia com identidade errada.

É a única leitura de contexto em runtime (orquestrador, workflow, agenda,
lembretes, respostas) — ver D12 em `docs/piloto/DECISOES.md`. Ordem de
resolução: versão publicada → linha legada `company_contexts` da própria
empresa → seed da própria empresa (fora de produção) → template global
marcado (fora de produção). Rascunho nunca é lido.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from typing import Any

from app.core.company_context import (
    CompanyContextError,
    context_storage_key,
    get_seed_company_context,
    has_seed_company_context,
    seed_contexts_enabled,
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
from app.services.company_policy_service import (
    CompanyPolicyError,
    validate_context_policy_fields,
    validate_policies,
)
from app.storage import company_config_repository as config_repository
from app.storage.repository import RuntimeRepository

logger = get_logger(__name__)
repository = RuntimeRepository()

# Mantido por compatibilidade de importação. O cache não expira mais por
# tempo: cada leitura confere o marcador da versão publicada no banco.
CACHE_TTL_SECONDS = 30


class CompanyConfigError(Exception):
    pass


class CompanyConfigInvalidError(CompanyConfigError):
    """Versão existe, mas não passa na validação para publicação."""


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


# company_id -> (marcador da versão publicada, config)
_CACHE: dict[int, tuple[str, CompanyConfig]] = {}
_CACHE_LOCK = threading.Lock()


def invalidate_company_config(company_id: int | None = None) -> None:
    """Invalida o cache local. Chamado após publicar uma versão."""
    with _CACHE_LOCK:
        if company_id is None:
            _CACHE.clear()
            return
        _CACHE.pop(int(company_id), None)


def _cached(company_id: int, marker: str | None) -> CompanyConfig | None:
    if marker is None:
        return None
    with _CACHE_LOCK:
        entry = _CACHE.get(company_id)
    if not entry:
        return None
    cached_marker, config = entry
    # O banco é compartilhado entre API e workers: invalidação local não
    # alcança os outros processos, então a validade é o marcador publicado.
    if cached_marker != marker:
        return None
    return config


def _store(company_id: int, marker: str, config: CompanyConfig) -> None:
    with _CACHE_LOCK:
        _CACHE[company_id] = (marker, config)


def _marker_for(version_row: dict[str, Any]) -> str:
    return f"{int(version_row['id'])}:{version_row.get('content_hash') or ''}"


def validate_company_config_input(
    context: dict[str, Any],
    policies: dict[str, Any] | None = None,
) -> None:
    """Valida contexto e override de política antes de qualquer escrita."""
    try:
        validate_company_context(context)
        validate_context_policy_fields(context)
        validate_policies(policies if policies is not None else context.get("policies"))
    except (CompanyContextError, CompanyPolicyError) as exc:
        raise CompanyConfigError(str(exc)) from exc


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


def _legacy_context_for(company: dict[str, Any]) -> dict[str, Any] | None:
    """Contexto gravado pela API legada para esta empresa, se houver.

    Só a linha da própria empresa: a linha global (`__global__`) nunca
    empresta identidade a um tenant por este caminho.
    """
    company_slug = str(company.get("slug") or "").strip()
    if not company_slug:
        return None
    try:
        storage_key = context_storage_key(company_slug)
    except CompanyContextError:
        return None
    row = repository.get_company_context_record(storage_key)
    if not row:
        return None
    # A chave legada é o slug normalizado, e slugs distintos colidem nela
    # ("Acme" e "acme", "acme.co" e "acme-co"). Só vale a linha gravada para
    # esta empresa, ou uma linha sem vínculo cujo slug é exatamente o dela.
    row_company_id = row.get("company_id")
    belongs_to_company = (
        int(row_company_id) == int(company["id"])
        if row_company_id is not None
        else storage_key == company_slug
    )
    if not belongs_to_company:
        log_event(
            logger,
            logging.WARNING,
            "company_config.legacy_context_slug_collision",
            company_id=company["id"],
        )
        return None
    try:
        data = json.loads(row["context_json"])
    except (TypeError, ValueError) as exc:
        raise CompanyConfigError(
            f"Legacy company context for company_id={company['id']} is not valid JSON."
        ) from exc
    if not isinstance(data, dict):
        raise CompanyConfigError(
            f"Legacy company context for company_id={company['id']} must be a JSON object."
        )
    try:
        validate_company_context(data)
    except CompanyContextError as exc:
        raise CompanyConfigError(str(exc)) from exc
    return data


def get_company_config(company_id: int, *, use_cache: bool = True) -> CompanyConfig:
    """Configuração publicada da empresa."""
    resolved_company_id = require_company_id(company_id, context="company config read")

    if use_cache:
        cached = _cached(
            resolved_company_id,
            config_repository.get_published_marker(company_id=resolved_company_id),
        )
        if cached is not None:
            return cached

    company = repository.get_company(resolved_company_id)
    if not company:
        raise CompanyConfigError(f"Company {resolved_company_id} not found.")

    published = config_repository.get_published_context(company_id=resolved_company_id)
    if published:
        config = _config_from_version(company, published, "database")
        _store(resolved_company_id, _marker_for(published), config)
        return config

    legacy_context = _legacy_context_for(company)
    if legacy_context is not None:
        # Compatibilidade: empresa configurada antes das versões. Sem escrita
        # na leitura e sem cache — a próxima escrita legada publica versão.
        log_event(
            logger,
            logging.WARNING,
            "company_config.legacy_context_used",
            company_id=resolved_company_id,
        )
        return _build_config(
            company=company,
            version_row=None,
            context=legacy_context,
            capabilities_override=legacy_context.get("capabilities"),
            policies_override=legacy_context.get("policies"),
            source="legacy_context",
        )

    company_slug = str(company.get("slug") or "") or None
    if company_slug and seed_contexts_enabled() and has_seed_company_context(company_slug):
        config = bootstrap_company_config(company_id=resolved_company_id)
        published = config_repository.get_published_context(company_id=resolved_company_id)
        if published:
            _store(resolved_company_id, _marker_for(published), config)
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
    return _build_config(
        company=company,
        version_row=None,
        context=context,
        capabilities_override=None,
        policies_override=None,
        source="global_template",
    )


def get_company_context_for(company_id: int | None) -> dict[str, Any]:
    """Contexto publicado de uma empresa, para consumidores legados."""
    return get_runtime_company_context(company_id)


def get_runtime_company_context(company_id: int | None) -> dict[str, Any]:
    """Contexto de runtime de uma empresa (ver ordem no docstring do módulo)."""
    if company_id is None:
        if is_production_like():
            raise CompanyConfigError(
                "Runtime company context requires a company in production."
            )
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

    validate_company_config_input(context, policies)

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


def publish_legacy_context_write(
    *,
    company_slug: str | None,
    context: dict[str, Any],
    author: str | None,
    change_note: str,
) -> dict[str, Any] | None:
    """Publica como versão um contexto recebido por um caminho de escrita legado.

    Mantém capacidades e políticas da versão publicada atual: a API legada só
    conhece o contexto e não pode zerar a política da empresa por omissão.
    Contexto global (sem empresa) não gera versão.
    """
    if not company_slug:
        return None
    company = repository.get_company_by_slug(company_slug)
    if not company:
        return None
    current = config_repository.get_published_context(company_id=int(company["id"]))
    return save_company_config(
        company_id=int(company["id"]),
        context=context,
        capabilities=current["capabilities"] if current else None,
        policies=current["policies"] if current else None,
        author=author,
        change_note=change_note,
        publish=True,
    )


def publish_company_config(
    *,
    company_id: int,
    version_id: int,
    author: str | None = None,
) -> dict[str, Any]:
    resolved_company_id = require_company_id(company_id, context="company config publish")
    candidate = config_repository.get_context_version(
        company_id=resolved_company_id,
        version_id=version_id,
    )
    if candidate is None:
        raise CompanyConfigError(
            f"Context version {version_id} not found for company_id={resolved_company_id}."
        )
    # Rascunho gravado antes da validação existir não entra em produção sem
    # passar por ela.
    try:
        validate_company_context(candidate["context"])
        validate_context_policy_fields(candidate["context"])
        validate_policies(candidate.get("policies"))
    except (CompanyContextError, CompanyPolicyError) as exc:
        raise CompanyConfigInvalidError(str(exc)) from exc

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
