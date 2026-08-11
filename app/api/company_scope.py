"""Autorização central de escopo de empresa nas rotas administrativas.

Resolver `company_id` não é autorização. Este módulo é o único ponto onde a
associação entre o principal autenticado e a empresa é verificada, e roda como
dependência de router — cobre todas as rotas administrativas sem depender de
cada handler lembrar de checar.

Duas mudanças em relação à versão anterior:

1. **Deny-by-default por política declarada.** A allowlist ampla por prefixo
   deixava `/admin/company-context`, `/admin/audit-events` e
   `/admin/system/onboarding` fora da autorização, embora todas aceitem ou
   devolvam dimensão de empresa. Agora cada grupo de rotas declara sua
   política em `ROUTE_POLICIES`, e `tests/test_phase_zero_blockers.py`
   enumera as rotas mutáveis para provar que nenhuma ficou sem declaração.

2. **O escopo autorizado é a fonte única.** `request.state` passa a carregar
   `authorized_company_id` (empresa única) e `authorized_company_ids`
   (conjunto permitido em leituras amplas). Nenhum handler pode refazer a
   resolução e cair na primeira empresa ativa.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request

from app.api.admin_security import get_request_principal
from app.core.logging import get_request_context, log_event
from app.services.admin_principal_service import AdminPrincipal

# Sem dimensão de empresa: catálogo estático, observabilidade e diagnóstico
# global do processo.
POLICY_UNSCOPED = "unscoped"
# Operação da plataforma: exige superadmin **global**, não superadmin de um
# tenant. Administrar identidades entra aqui.
POLICY_PLATFORM_GLOBAL = "platform_global"
# Dado tenant-scoped: exige empresa autorizada.
POLICY_TENANT = "tenant"

MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Prefixo -> política. O prefixo mais específico vence, então
# `/admin/billing/plans` (catálogo estático) convive com `/admin/billing`
# (dados da empresa). Métodos opcionais permitem que criar uma empresa seja
# operação de plataforma enquanto listá-las continua tenant-scoped.
ROUTE_POLICIES: tuple[tuple[str, frozenset[str] | None, str], ...] = (
    # --- sem dimensão de empresa: catálogo estático, igual para todo mundo ---
    #
    # `unscoped` quer dizer "esta leitura não tem dimensão de empresa", e não
    # "qualquer principal pode ler". A confusão entre as duas coisas foi o T-07:
    # `/admin/state` e `/admin/observability/*` estavam aqui e devolviam
    # contagem de empresas, mensagens e contatos da plataforma inteira para
    # qualquer viewer de tenant. As rotas abaixo são catálogo — taxonomia,
    # pacotes verticais, planos, presets e providers de calendário são os
    # mesmos para todos os tenants e não revelam operação de ninguém.
    ("/admin/taxonomy", None, POLICY_UNSCOPED),
    ("/admin/vertical-packs", None, POLICY_UNSCOPED),
    ("/admin/billing/plans", None, POLICY_UNSCOPED),
    ("/admin/onboarding/presets", None, POLICY_UNSCOPED),
    ("/admin/calendar/providers", None, POLICY_UNSCOPED),
    # --- plataforma: exige superadmin global ---
    #
    # T-07/T-08: postura operacional agregada. Não é dado de um tenant, então
    # não cabe em `tenant`; mas revela o tamanho da carteira e o volume de
    # operação dos concorrentes por diferença, então também não é `unscoped`.
    ("/admin/state", None, POLICY_PLATFORM_GLOBAL),
    ("/admin/observability", None, POLICY_PLATFORM_GLOBAL),
    ("/admin/system/migrations", None, POLICY_PLATFORM_GLOBAL),
    ("/admin/system/profile", None, POLICY_PLATFORM_GLOBAL),
    ("/admin/system/readiness", None, POLICY_PLATFORM_GLOBAL),
    ("/admin/principals", None, POLICY_PLATFORM_GLOBAL),
    ("/admin/companies", frozenset({"POST"}), POLICY_PLATFORM_GLOBAL),
    ("/admin/audit-events/cleanup", None, POLICY_PLATFORM_GLOBAL),
    # T-05: `setup_company` cria empresa. Criar empresa é operação de
    # plataforma — deixá-la sob POLICY_TENANT permitia que um superadmin
    # de um tenant povoasse o catálogo e ocupasse o slug de um concorrente.
    ("/admin/onboarding/setup", frozenset({"POST"}), POLICY_PLATFORM_GLOBAL),
    ("/admin/reminders/dispatch-due", None, POLICY_PLATFORM_GLOBAL),
    ("/admin/outbound-messages/dispatch-pending", None, POLICY_PLATFORM_GLOBAL),
    ("/admin/automations/worker", None, POLICY_PLATFORM_GLOBAL),
    # --- tenant-scoped ---
    # Introspecção do escopo: passa pela mesma resolução que as demais rotas
    # tenant, porque é justamente isso que ela precisa reportar.
    ("/admin/access/scope", None, POLICY_TENANT),
    ("/admin/analytics", None, POLICY_TENANT),
    ("/admin/audit-events", None, POLICY_TENANT),
    ("/admin/automations", None, POLICY_TENANT),
    ("/admin/billing", None, POLICY_TENANT),
    ("/admin/calendar", None, POLICY_TENANT),
    ("/admin/channel-bindings", None, POLICY_TENANT),
    ("/admin/companies", None, POLICY_TENANT),
    ("/admin/company-config", None, POLICY_TENANT),
    ("/admin/company-context", None, POLICY_TENANT),
    ("/admin/contacts", None, POLICY_TENANT),
    ("/admin/conversation-import", None, POLICY_TENANT),
    ("/admin/dataset-versions", None, POLICY_TENANT),
    ("/admin/evaluation", None, POLICY_TENANT),
    ("/admin/finetuning", None, POLICY_TENANT),
    ("/admin/finetuning-dataset", None, POLICY_TENANT),
    ("/admin/knowledge", None, POLICY_TENANT),
    ("/admin/knowledge-entries", None, POLICY_TENANT),
    ("/admin/message-examples", None, POLICY_TENANT),
    ("/admin/messages", None, POLICY_TENANT),
    ("/admin/models", None, POLICY_TENANT),
    ("/admin/onboarding", None, POLICY_TENANT),
    ("/admin/outbound-messages", None, POLICY_TENANT),
    ("/admin/pending-questions", None, POLICY_TENANT),
    ("/admin/reminders", None, POLICY_TENANT),
    ("/admin/system/onboarding", None, POLICY_TENANT),
    ("/admin/tenant-credentials", None, POLICY_TENANT),
    ("/admin/training-candidates", None, POLICY_TENANT),
    ("/admin/training-dataset", None, POLICY_TENANT),
    ("/admin/training-import", None, POLICY_TENANT),
)


def describe_route_policy(path: str, method: str | None = None) -> str | None:
    """Política declarada para o caminho, ou `None` se não houver nenhuma.

    `None` é uma lacuna de política, não uma permissão: em runtime o caminho
    não declarado é tratado como tenant-scoped (o mais restritivo) e
    registrado. O teste de cobertura existe para que a lacuna apareça na
    suíte antes de aparecer em produção.
    """
    normalized_method = (method or "").upper()
    best_prefix = ""
    best_policy: str | None = None

    for prefix, methods, policy in ROUTE_POLICIES:
        if not path.startswith(prefix):
            continue
        if methods is not None and normalized_method and normalized_method not in methods:
            continue
        # Empate de comprimento é resolvido a favor da regra com método
        # declarado, que é sempre a mais específica.
        if len(prefix) > len(best_prefix) or (
            len(prefix) == len(best_prefix) and methods is not None
        ):
            best_prefix = prefix
            best_policy = policy

    return best_policy


def resolve_route_policy(path: str, method: str) -> str:
    """Política efetiva. Caminho não declarado cai no mais restritivo."""
    return describe_route_policy(path, method) or POLICY_TENANT


def _parse_company_id(raw: str | None) -> int | None:
    if raw is None:
        return None
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


@dataclass(frozen=True)
class AuthorizedScope:
    """O escopo que a plataforma autorizou para esta requisição.

    Objeto explícito, e não um `company_id` solto, porque a decisão tem quatro
    dimensões e cada uma já foi motivo de defeito:

    - `company_id`: a empresa única, quando a operação exige uma;
    - `company_ids`: o conjunto permitido em leitura ampla (`None` = global);
    - `effective_role`: o papel **na empresa**, não o papel global;
    - `is_global`: se este principal opera a plataforma.

    `company_id=None` **nunca** significa "todas" para um principal
    tenant-scoped. Significa "a operação ainda não escolheu uma empresa", e
    quem precisa de uma chama `require_authorized_company_id`.
    """

    principal: AdminPrincipal
    company_id: int | None
    company_ids: frozenset[int] | None
    effective_role: str
    is_global: bool

    def allows(self, company_id: int) -> bool:
        return self.principal.can_access_company(int(company_id))

    def as_audit_metadata(self) -> dict[str, Any]:
        return {
            **self.principal.as_audit_metadata(),
            "authorized_company_id": self.company_id,
            "effective_role": self.effective_role,
        }


def _set_scope(
    request: Request,
    *,
    principal: AdminPrincipal,
    company_id: int | None,
    company_ids: frozenset[int] | None,
) -> None:
    scope = AuthorizedScope(
        principal=principal,
        company_id=company_id,
        company_ids=company_ids,
        effective_role=principal.role_in_company(company_id),
        is_global=principal.is_global,
    )
    request.state.authorized_scope = scope
    # Campos planos mantidos para o código existente.
    request.state.authorized_company_id = company_id
    request.state.authorized_company_ids = company_ids


def build_require_company_scope(
    *,
    logger: logging.Logger,
    repository_getter: Callable[[], Any],
):
    def _audit_denial(
        request: Request,
        principal: AdminPrincipal,
        company_id: int | None,
        reason: str,
    ) -> None:
        log_event(
            logger,
            logging.WARNING,
            "admin.company_scope.denied",
            actor_id=principal.actor,
            principal_id=principal.principal_id,
            admin_role=principal.global_role,
            requested_company_id=company_id,
            denial_reason=reason,
        )
        repository_getter().create_audit_event(
            actor_id=principal.actor,
            admin_role=principal.global_role,
            auth_mode=principal.auth_mode,
            action="authorize_company",
            resource_type="admin_access",
            company_id=company_id,
            status="denied",
            request_id=str(get_request_context().get("request_id") or ""),
            http_method=request.method,
            http_path=request.url.path,
            metadata={**principal.as_audit_metadata(), "denial_reason": reason},
        )

    def _deny(
        request: Request,
        principal: AdminPrincipal,
        company_id: int | None,
        reason: str = "company_not_in_scope",
    ) -> None:
        _audit_denial(request, principal, company_id, reason)
        # Mesma resposta para "não existe" e "não autorizado": diferenciar
        # permitiria enumerar empresas de outros tenants.
        raise HTTPException(status_code=403, detail="Company access denied for this principal.")

    def require_company_scope(request: Request) -> None:
        path = request.url.path
        policy = resolve_route_policy(path, request.method)

        if policy == POLICY_UNSCOPED:
            request.state.authorized_scope = None
            request.state.authorized_company_id = None
            request.state.authorized_company_ids = None
            return

        principal = get_request_principal(request)

        if policy == POLICY_PLATFORM_GLOBAL:
            if not principal.is_global:
                _audit_denial(request, principal, None, "platform_operation_requires_global")
                raise HTTPException(
                    status_code=403,
                    detail="This operation requires a global superadmin principal.",
                )
            _set_scope(request, principal=principal, company_id=None, company_ids=None)
            return

        if describe_route_policy(path, request.method) is None:
            # Lacuna de política: o caminho segue como tenant-scoped, mas
            # fica registrado para ser declarado.
            log_event(
                logger,
                logging.WARNING,
                "admin.company_scope.undeclared_route",
                http_path=path,
                http_method=request.method,
            )

        repository = repository_getter()
        raw_company_id = request.query_params.get("company_id")
        raw_company_slug = request.query_params.get("company_slug")
        requested_company_id = _parse_company_id(raw_company_id)
        requested_company_slug = (raw_company_slug or "").strip() or None

        if raw_company_id is not None and requested_company_id is None:
            raise HTTPException(status_code=400, detail="company_id must be a positive integer.")

        if requested_company_id is None and requested_company_slug is None:
            if principal.is_global:
                _set_scope(request, principal=principal, company_id=None, company_ids=None)
                return
            if not principal.allowed_company_ids:
                _deny(request, principal, None, "principal_has_no_membership")
                return
            if len(principal.allowed_company_ids) == 1:
                # Escopo único: resolver implicitamente é seguro e ergonômico.
                only_company_id = next(iter(principal.allowed_company_ids))
                _set_scope(
                    request,
                    principal=principal,
                    company_id=only_company_id,
                    company_ids=principal.allowed_company_ids,
                )
                return
            if request.method in MUTATING_METHODS:
                # Escrever exige dizer em qual empresa. Escolher por conta
                # própria seria adivinhar a intenção do operador.
                raise HTTPException(
                    status_code=400,
                    detail="company_id or company_slug is required for a company-scoped principal.",
                )
            # Leitura sem empresa nomeada: escopo é o conjunto da membership.
            _set_scope(request, principal=principal, company_id=None, company_ids=principal.allowed_company_ids)
            return

        resolver = getattr(repository, "resolve_company_id", None)
        raw_resolved = (
            resolver(company_id=requested_company_id, company_slug=requested_company_slug)
            if callable(resolver)
            else requested_company_id
        )

        if principal.is_global:
            # Um principal global é autorizado em qualquer empresa. Se ela não
            # existir, quem responde é a rota (404/400), não a autorização:
            # para ele não há nada a enumerar.
            _set_scope(
                request,
                principal=principal,
                company_id=int(raw_resolved) if raw_resolved is not None else None,
                company_ids=None,
            )
            return

        if raw_resolved is None:
            _deny(request, principal, requested_company_id, "company_not_found")
            return

        resolved_company_id = int(raw_resolved)
        if not principal.can_access_company(resolved_company_id):
            _deny(request, principal, resolved_company_id, "company_not_in_scope")
            return

        _set_scope(
            request,
            principal=principal,
            company_id=resolved_company_id,
            company_ids=principal.allowed_company_ids,
        )

    return require_company_scope


def get_authorized_company_id(request: Request) -> int | None:
    """Empresa única já autorizada. `None` significa escopo amplo."""
    return getattr(request.state, "authorized_company_id", None)


def get_authorized_company_ids(request: Request) -> frozenset[int] | None:
    """Conjunto autorizado em leituras amplas. `None` significa global."""
    return getattr(request.state, "authorized_company_ids", None)


def get_authorized_scope(request: Request) -> AuthorizedScope | None:
    """Escopo autorizado da requisição, ou `None` em rota sem empresa."""
    return getattr(request.state, "authorized_scope", None)


def require_company_filter(request: Request) -> int | None:
    """Empresa para filtrar uma leitura. `None` **só** para principal global.

    A distinção é a correção do defeito: para um principal tenant-scoped,
    ausência de empresa nunca pode virar `WHERE (NULL IS NULL OR ...)`, que no
    SQL significa **todas as empresas**. Para um principal global, ver tudo é
    a intenção declarada e continua permitido.

    Um principal com várias memberships precisa nomear a empresa: escolher uma
    por ele seria adivinhar, e devolver todas seria vazamento.
    """
    scope = get_authorized_scope(request)
    if scope is None:
        raise HTTPException(
            status_code=400,
            detail="company_id or company_slug is required for this operation.",
        )
    if scope.company_id is not None:
        return int(scope.company_id)
    if scope.is_global:
        return None
    raise HTTPException(
        status_code=400,
        detail="company_id or company_slug is required for a company-scoped principal.",
    )


def require_authorized_company_id(request: Request) -> int:
    """Empresa única obrigatória para a operação.

    Substitui qualquer resolução própria do handler. Nunca cai em empresa
    default: sem escopo resolvido, a resposta é 400, não uma empresa
    arbitrária.
    """
    company_id = get_authorized_company_id(request)
    if company_id is None:
        raise HTTPException(
            status_code=400,
            detail="company_id or company_slug is required for this operation.",
        )
    return int(company_id)


def authorize_company(request: Request, company_id: int) -> int:
    """Autoriza um company_id derivado do corpo/caminho, não da query string."""
    principal = get_request_principal(request)
    if not principal.can_access_company(int(company_id)):
        raise HTTPException(status_code=403, detail="Company access denied for this principal.")
    return int(company_id)
