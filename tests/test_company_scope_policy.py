"""A política de escopo cobre toda rota administrativa, e é deny-by-default.

Este teste existe porque a versão anterior da autorização decidia por
allowlist: os caminhos que exigiam empresa eram enumerados, e tudo que não
casasse passava sem exigir nenhuma. Uma rota nova nascia liberada. O que
mudou não foi só a tabela — foi o ônus: aqui o não declarado é o mais
restritivo, e a lacuna aparece na suíte em vez de em produção.
"""

from __future__ import annotations

import pytest
from starlette.routing import Route

from app.api.company_scope import (
    POLICY_PLATFORM_GLOBAL,
    POLICY_TENANT,
    POLICY_TENANT_COLLECTION,
    POLICY_UNSCOPED,
    ROUTE_POLICIES,
    describe_route_policy,
    resolve_route_policy,
)
from app.main import app

VALID_POLICIES = {
    POLICY_UNSCOPED,
    POLICY_PLATFORM_GLOBAL,
    POLICY_TENANT,
    POLICY_TENANT_COLLECTION,
}
IGNORED_METHODS = {"HEAD", "OPTIONS"}


def _admin_routes() -> list[tuple[str, str]]:
    """Enumera (caminho, método) reais do app.

    O FastAPI não achata os routers incluídos: `include_router` deixa um
    wrapper cujo router original vive em `original_router`. Percorrer só
    `app.routes` acha um punhado de rotas e faz este teste passar por vazio,
    que é o pior desfecho possível para um teste de cobertura.
    """
    found: list[tuple[str, str]] = []

    def walk(node: object) -> None:
        for route in getattr(node, "routes", []):
            inner = getattr(route, "original_router", None)
            if inner is not None:
                walk(inner)
                continue
            if isinstance(route, Route) and route.path.startswith("/admin"):
                found.extend(
                    (route.path, method)
                    for method in sorted((route.methods or set()) - IGNORED_METHODS)
                )
            walk(route)

    walk(app)
    return sorted(set(found))


ADMIN_ROUTES = _admin_routes()


def test_the_walk_actually_finds_the_admin_routes() -> None:
    """Guarda contra o teste passar porque não enumerou nada.

    Se `original_router` mudar de nome numa atualização do FastAPI, os testes
    de cobertura abaixo passariam com a lista vazia. Este limite inferior faz
    a mudança aparecer como falha.
    """
    assert len(ADMIN_ROUTES) >= 60, f"apenas {len(ADMIN_ROUTES)} rotas admin encontradas"


@pytest.mark.parametrize(("path", "method"), ADMIN_ROUTES)
def test_every_admin_route_declares_a_policy(path: str, method: str) -> None:
    """Nenhuma rota administrativa depende do default.

    O default existe para que uma rota nova nasça segura, não para que a
    tabela fique incompleta: uma rota sem declaração é uma decisão que
    ninguém tomou.
    """
    declared = describe_route_policy(path, method)
    assert declared is not None, (
        f"{method} {path} não tem política declarada em ROUTE_POLICIES. "
        f"Em runtime cairia em '{POLICY_TENANT}'; declare a política correta."
    )
    assert declared in VALID_POLICIES


def test_undeclared_paths_fall_back_to_the_most_restrictive_policy() -> None:
    """O default é tenant-scoped, não liberado."""
    assert resolve_route_policy("/admin/rota-que-ainda-nao-existe", "POST") == POLICY_TENANT
    assert resolve_route_policy("/admin/qualquer/coisa/nova", "GET") == POLICY_TENANT


def test_more_specific_prefix_wins() -> None:
    """Catálogo e dado da empresa convivem sob o mesmo prefixo raiz."""
    assert resolve_route_policy("/admin/billing/plans", "GET") == POLICY_UNSCOPED
    assert resolve_route_policy("/admin/billing/subscription", "GET") == POLICY_TENANT


def test_exact_match_separates_the_collection_from_the_item() -> None:
    """Listar as próprias empresas não é o mesmo que ler uma delas.

    Sem o casamento exato, as duas regras empatam no comprimento do prefixo e
    `/admin/companies/{id}` herdaria a política da listagem — que não exige
    `company_id` e portanto não verificaria membership naquela empresa.
    """
    assert resolve_route_policy("/admin/companies", "GET") == POLICY_TENANT_COLLECTION
    assert resolve_route_policy("/admin/companies/7", "GET") == POLICY_TENANT
    assert resolve_route_policy("/admin/companies/7", "PATCH") == POLICY_TENANT


def test_simulator_is_tenant_scoped() -> None:
    """Regressão: era a rota que a allowlist deixava passar.

    `/admin/simulator` aceita `company_id` e processa uma mensagem em nome
    daquela empresa, mas não constava de `tenant_scoped_prefixes`, então a
    exigência de empresa era pulada em silêncio.
    """
    assert resolve_route_policy("/admin/simulator", "POST") == POLICY_TENANT


def test_policy_table_declares_only_known_policies() -> None:
    for pattern, methods, policy in ROUTE_POLICIES:
        assert policy in VALID_POLICIES, f"{pattern}: política desconhecida {policy!r}"
        assert pattern.startswith("/admin"), f"{pattern}: fora do namespace administrativo"
        if methods is not None:
            assert methods, f"{pattern}: conjunto de métodos vazio nunca casa"
            assert all(m == m.upper() for m in methods), f"{pattern}: métodos em minúscula"
