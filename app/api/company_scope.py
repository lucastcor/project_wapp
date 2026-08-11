"""Política central de escopo de empresa das rotas administrativas.

Resolver `company_id` não é autorizar. Este módulo é o único lugar onde se
decide **se** uma rota exige empresa, e roda dentro da dependência de
autenticação, cobrindo todas as rotas administrativas sem depender de cada
handler lembrar de checar.

## Por que uma tabela de políticas, e não uma lista de prefixos

A versão anterior decidia por allowlist: `tenant_scoped_prefixes` enumerava os
caminhos que exigiam empresa, e tudo que não casasse passava sem exigir
nenhuma. Isso inverte o ônus — a rota nasce liberada e só é protegida quando
alguém lembra de acrescentar o prefixo. `/admin/simulator` é o caso concreto:
aceita `company_id`, processa uma mensagem para aquela empresa e não constava
da tupla, então a exigência de empresa era pulada silenciosamente.

Aqui o default é o oposto. `resolve_route_policy` devolve `POLICY_TENANT` para
qualquer caminho não declarado, de modo que uma rota nova nasce exigindo
empresa e o erro possível é uma rota de catálogo pedir empresa a mais — barulho
visível em teste — em vez de uma rota tenant-scoped não pedir nenhuma, que é
vazamento silencioso entre empresas.

`tests/test_company_scope_policy.py` enumera as rotas reais do app e falha
quando alguma não tem política declarada, para que a lacuna apareça na suíte
antes de aparecer em produção.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import Request

# Sem dimensão de empresa: catálogo estático, igual para todos os tenants.
#
# `unscoped` quer dizer "esta leitura não tem dimensão de empresa", e não
# "qualquer um pode ler". Confundir as duas coisas é como dados agregados da
# plataforma acabam expostos a um viewer de um único tenant. As rotas abaixo
# são catálogo — planos, presets de onboarding e providers de calendário são
# os mesmos para todo mundo e não revelam a operação de ninguém.
POLICY_UNSCOPED = "unscoped"

# Operação da plataforma: exige o principal global (token de break-glass), não
# um superadmin de tenant. Administrar o catálogo de empresas entra aqui.
POLICY_PLATFORM_GLOBAL = "platform_global"

# Dado tenant-scoped: exige uma empresa autorizada. É o default.
POLICY_TENANT = "tenant"

# Leitura de coleção que atravessa as empresas do próprio principal: listar
# "as empresas de que eu participo" não tem uma empresa única a exigir, mas
# também não é global. Não exige `company_id`; em compensação o handler é
# obrigado a filtrar por `authorized_company_ids`, que fica em request.state.
POLICY_TENANT_COLLECTION = "tenant_collection"

MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# (padrão, métodos ou None para todos, política).
#
# O prefixo mais específico vence, então `/admin/billing/plans` (catálogo) e
# `/admin/billing` (dado da empresa) convivem.
#
# Um padrão terminado em `$` casa o caminho **exato**, e não o prefixo. É o que
# separa `/admin/companies` (listar as minhas) de `/admin/companies/{id}` (ler
# uma): sem isso as duas regras empatam no comprimento do prefixo e ler uma
# empresa herdaria a política da listagem.
ROUTE_POLICIES: tuple[tuple[str, frozenset[str] | None, str], ...] = (
    # --- catálogo estático ---
    ("/admin/billing/plans", frozenset({"GET"}), POLICY_UNSCOPED),
    ("/admin/onboarding/presets", frozenset({"GET"}), POLICY_UNSCOPED),
    ("/admin/calendar/providers", frozenset({"GET"}), POLICY_UNSCOPED),
    # Criação de empresa. No momento da chamada ainda não existe empresa a
    # que escopar, então não há `company_id` a exigir — daí `unscoped`.
    #
    # RISCO ACEITO, e deliberadamente visível: sob esta política um superadmin
    # de tenant pode criar empresas e ocupar o slug de um concorrente. A
    # alternativa (exigir credencial de plataforma) quebraria o wizard de
    # onboarding, que é hoje o único caminho de criação e roda em sessão de
    # identidade. A mitigação atual é o guard `require_admin_role("superadmin")`
    # na própria rota. Endurecer isto é decisão de produto, não de refactor:
    # ver o relatório da Release 1, seção de riscos remanescentes.
    ("/admin/onboarding/setup", frozenset({"POST"}), POLICY_UNSCOPED),
    # --- operação da plataforma ---
    ("/admin/system/migrations", None, POLICY_PLATFORM_GLOBAL),
    ("/admin/system/profile", None, POLICY_PLATFORM_GLOBAL),
    ("/admin/system/readiness", None, POLICY_PLATFORM_GLOBAL),
    # --- tenant-scoped ---
    ("/admin/analytics", None, POLICY_TENANT),
    ("/admin/audit-events", None, POLICY_TENANT),
    ("/admin/billing", None, POLICY_TENANT),
    ("/admin/calendar", None, POLICY_TENANT),
    # Listar as próprias empresas atravessa memberships; ler ou alterar uma
    # delas (`/admin/companies/{id}`) cai no prefixo mais longo abaixo.
    ("/admin/companies$", frozenset({"GET"}), POLICY_TENANT_COLLECTION),
    ("/admin/companies", None, POLICY_TENANT),
    ("/admin/company-context", None, POLICY_TENANT),
    ("/admin/contacts", None, POLICY_TENANT),
    ("/admin/finetuning", None, POLICY_TENANT),
    ("/admin/finetuning-dataset", None, POLICY_TENANT),
    ("/admin/knowledge-entries", None, POLICY_TENANT),
    ("/admin/message-examples", None, POLICY_TENANT),
    ("/admin/messages", None, POLICY_TENANT),
    ("/admin/observability", None, POLICY_TENANT),
    ("/admin/onboarding", None, POLICY_TENANT),
    ("/admin/outbound-messages", None, POLICY_TENANT),
    ("/admin/pending-questions", None, POLICY_TENANT),
    ("/admin/reminders", None, POLICY_TENANT),
    # Processa uma mensagem em nome de uma empresa. Era a lacuna que motivou
    # trocar a allowlist por deny-by-default.
    ("/admin/simulator", None, POLICY_TENANT),
    ("/admin/state", None, POLICY_TENANT),
    ("/admin/system/onboarding", None, POLICY_TENANT),
    ("/admin/training-dataset", None, POLICY_TENANT),
    ("/admin/training-import", None, POLICY_TENANT),
    ("/admin/whatsapp", None, POLICY_TENANT),
)


def describe_route_policy(path: str, method: str | None = None) -> str | None:
    """Política declarada para o caminho, ou `None` se não houver nenhuma.

    `None` é uma lacuna de declaração, não uma permissão: em runtime o caminho
    não declarado cai no mais restritivo, via `resolve_route_policy`. Esta
    função existe separada para que o teste de cobertura consiga distinguir
    "declarada como tenant" de "não declarada".
    """
    normalized_method = (method or "").upper()
    best_prefix = ""
    best_policy: str | None = None
    best_had_methods = False

    for pattern, methods, policy in ROUTE_POLICIES:
        if pattern.endswith("$"):
            prefix = pattern[:-1]
            if path.rstrip("/") != prefix:
                continue
        else:
            prefix = pattern
            if not path.startswith(prefix):
                continue
        if methods is not None and normalized_method and normalized_method not in methods:
            continue
        # Empate de comprimento é resolvido a favor da regra com método
        # declarado, que é sempre a mais específica.
        exact = pattern.endswith("$")
        if (
            exact
            or len(prefix) > len(best_prefix)
            or (len(prefix) == len(best_prefix) and methods is not None and not best_had_methods)
        ):
            best_prefix = prefix
            best_policy = policy
            best_had_methods = methods is not None
            if exact:
                # Nada é mais específico que o caminho exato.
                break

    return best_policy


def resolve_route_policy(path: str, method: str) -> str:
    """Política efetiva. Caminho não declarado cai no mais restritivo."""
    return describe_route_policy(path, method) or POLICY_TENANT


@dataclass(frozen=True)
class AuthorizedScope:
    """O escopo que a plataforma autorizou para esta requisição.

    Objeto explícito, e não um `company_id` solto, porque a decisão tem mais de
    uma dimensão:

    - `company_id`: a empresa única, quando a operação exige uma;
    - `company_ids`: o conjunto permitido em leitura ampla (`None` = global);
    - `effective_role`: o papel **na empresa**, não um papel global;
    - `is_global`: se este principal opera a plataforma.

    `company_id=None` **nunca** significa "todas" para um principal
    tenant-scoped. Significa "a operação ainda não escolheu uma empresa".
    """

    company_id: int | None
    company_ids: frozenset[int] | None
    effective_role: str
    is_global: bool
    policy: str

    def allows(self, company_id: int) -> bool:
        if self.is_global or self.company_ids is None:
            return True
        return int(company_id) in self.company_ids

    def as_audit_metadata(self) -> dict[str, Any]:
        return {
            "authorized_company_id": self.company_id,
            "effective_role": self.effective_role,
            "route_policy": self.policy,
            "is_global": self.is_global,
        }


def set_authorized_scope(
    request: Request,
    *,
    company_id: int | None,
    company_ids: frozenset[int] | None,
    effective_role: str,
    is_global: bool,
    policy: str,
) -> AuthorizedScope:
    """Registra o escopo autorizado como fonte única para os handlers.

    Um handler que refaz a resolução por conta própria pode chegar a uma
    resposta diferente da que foi autorizada aqui — normalmente caindo na
    primeira empresa ativa. Por isso o resultado fica em `request.state` e não
    é recalculado adiante.
    """
    scope = AuthorizedScope(
        company_id=company_id,
        company_ids=company_ids,
        effective_role=effective_role,
        is_global=is_global,
        policy=policy,
    )
    request.state.authorized_scope = scope
    # Campos planos, para o código que já lê `request.state` diretamente.
    request.state.authorized_company_id = company_id
    request.state.authorized_company_ids = company_ids
    return scope


def get_authorized_scope(request: Request) -> AuthorizedScope | None:
    return getattr(request.state, "authorized_scope", None)
