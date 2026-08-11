"""T-09 e T-10: onde o filtro acontece, e o que sai quando ele falha.

**T-09** — `read_companies` carregava a tabela inteira e filtrava em memória.
O resultado é correto hoje; o problema é que a fronteira está no lugar errado.
`company_id não é um parâmetro; é uma fronteira` significa que o tenant
autorizado entra na consulta **antes** de o dado sair do banco. Filtrar depois
transforma cada leitura numa varredura da carteira inteira e deixa o isolamento
na mão de uma linha de list-comprehension que qualquer refatoração remove sem
que nenhum teste perceba.

**T-10** — `TenantScopeError` não tinha handler. Uma empresa ausente ou
inválida numa borda interna virava **500**, que é a resposta errada em três
sentidos: culpa o servidor por um erro do cliente, aciona alerta de plataforma
para um caso esperado, e é o único status cuja página de erro pode carregar
detalhe interno. O status estável é 400.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.tenancy import TenantScopeError, require_company_id
from app.main import app
from app.storage.repository import RuntimeRepository

client = TestClient(app)
repository = RuntimeRepository()


@pytest.fixture(autouse=True)
def _enable_admin_auth(monkeypatch):
    monkeypatch.setattr(settings, "enable_admin_auth", True)
    monkeypatch.setattr(settings, "admin_viewer_token", "legacy-viewer")
    monkeypatch.setattr(settings, "admin_operator_token", "legacy-operator")
    monkeypatch.setattr(settings, "admin_superadmin_token", "legacy-superadmin")
    monkeypatch.setattr(settings, "admin_api_token", None)
    yield


def _headers(principal: dict) -> dict[str, str]:
    return {"X-Admin-Token": principal["token"]}


# --- T-09: o filtro mora no SQL ----------------------------------------------------------


def test_listing_companies_filters_in_the_query_not_in_memory(company_factory, admin_principal_factory) -> None:
    """O repositório não pode devolver empresa de fora do escopo para ser descartada depois.

    A verificação é sobre o que **sai do banco**, não sobre o que chega ao
    cliente: a rota já devolvia a lista certa filtrando em memória.
    """
    mine = company_factory("t09-mine")
    for index in range(3):
        company_factory(f"t09-other-{index}")

    rows = repository.get_companies(company_ids=[mine])

    assert [int(row["id"]) for row in rows] == [mine], (
        "get_companies devolveu empresas fora do conjunto autorizado; o filtro "
        f"ainda está em memória: {[int(row['id']) for row in rows]}"
    )


def test_the_repository_rejects_an_empty_scope_instead_of_returning_everything(company_factory) -> None:
    """Conjunto vazio significa "nenhuma empresa", nunca "todas".

    É o mesmo erro de `WHERE (NULL IS NULL OR ...)`: uma lista vazia que vira
    ausência de filtro devolve a plataforma inteira.
    """
    company_factory("t09-a")
    company_factory("t09-b")

    assert repository.get_companies(company_ids=[]) == []


def test_a_global_principal_still_lists_every_company(company_factory) -> None:
    """Controle positivo: `None` continua significando "sem filtro"."""
    company_factory("t09-global-a")
    company_factory("t09-global-b")

    assert len(repository.get_companies(company_ids=None)) >= 2


def test_the_route_shows_only_the_principals_companies(company_factory, admin_principal_factory) -> None:
    mine = company_factory("t09-route-mine")
    other = company_factory("t09-route-other")
    operator = admin_principal_factory(global_role="operator", company_ids=[mine])

    response = client.get("/admin/companies", headers=_headers(operator))

    assert response.status_code == 200, response.text
    listed = {int(row["id"]) for row in response.json()}
    assert listed == {mine}, f"a rota listou {listed}, esperado {{{mine}}}"
    assert other not in listed


# --- T-10: erro de escopo é 400, nunca 500 -----------------------------------------------


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        (None, "empresa ausente"),
        ("abc", "empresa não numérica"),
        (0, "empresa não positiva"),
        (-1, "empresa negativa"),
    ],
)
def test_require_company_id_raises_the_typed_error(value: object, reason: str) -> None:
    with pytest.raises(TenantScopeError):
        require_company_id(value, context=f"teste — {reason}")


def test_a_tenant_scope_error_becomes_a_stable_400(monkeypatch, admin_principal_factory) -> None:
    """Uma borda interna que recusa a empresa não pode virar 500.

    500 culpa o servidor por um erro do cliente, aciona alerta operacional para
    um caso esperado, e é o único status cuja resposta pode carregar detalhe
    interno.
    """
    from app.api import platform_admin_routes

    def _explode(*_args, **_kwargs):
        raise TenantScopeError("company_id is required for teste.")

    monkeypatch.setattr(platform_admin_routes, "describe_taxonomy", _explode)

    principal = admin_principal_factory(global_role="superadmin", is_global=True)
    with TestClient(app, raise_server_exceptions=False) as local_client:
        response = local_client.get("/admin/taxonomy", headers=_headers(principal))

    assert response.status_code == 400, f"status {response.status_code}: {response.text[:300]}"


def test_the_scope_error_response_does_not_leak_internals(monkeypatch, admin_principal_factory) -> None:
    """A mensagem interna pode citar tabela, contexto ou id. O corpo não."""
    from app.api import platform_admin_routes

    def _explode(*_args, **_kwargs):
        raise TenantScopeError("company_id is required for platform_company_memberships lookup.")

    monkeypatch.setattr(platform_admin_routes, "describe_taxonomy", _explode)

    principal = admin_principal_factory(global_role="superadmin", is_global=True)
    with TestClient(app, raise_server_exceptions=False) as local_client:
        response = local_client.get("/admin/taxonomy", headers=_headers(principal))

    body = response.text
    assert "platform_company_memberships" not in body, f"vazou detalhe interno: {body[:300]}"


# --- integridade referencial ligada nos dois backends -----------------------------------


def test_sqlite_enforces_foreign_keys_like_postgres() -> None:
    """O SQLite não pode aceitar o que o PostgreSQL recusa.

    `PRAGMA foreign_keys` vem desligada por padrão **e por conexão**. Enquanto
    esteve assim, a suíte não conseguia ver violação de integridade
    referencial: o teste passava no backend de desenvolvimento e o mesmo INSERT
    era recusado em produção. Não é preferência de rigor — é a suíte medindo
    outra coisa.

    Este teste substitui o temporário que exigia `enabled == 0`, o qual só fazia
    sentido enquanto o I-01 mantinha o vazamento de conexão aberto.
    """
    from app.storage.db import get_database_backend, read_connection

    if get_database_backend() != "sqlite":
        pytest.skip("verificação específica do SQLite; no PostgreSQL a FK é sempre aplicada")

    with read_connection() as connection:
        enabled = connection.execute("PRAGMA foreign_keys").fetchone()[0]

    assert enabled == 1, "integridade referencial desligada nesta conexão SQLite"


def test_an_orphan_insert_is_rejected(company_factory) -> None:
    """Configurada não basta: precisa estar sendo aplicada."""
    company_id = company_factory("fk-insert")

    with pytest.raises(Exception, match=r"(?i)foreign key|violates"):
        repository.create_pending_question(
            contact_id="contato-que-nao-existe",
            customer_message="mensagem",
            question_type="clarification",
            question_for_owner="pergunta",
            company_id=company_id,
        )


def test_an_orphan_update_is_rejected(company_factory) -> None:
    """UPDATE também: a FK vale nas duas direções da escrita."""
    from app.storage.db import transaction

    company_id = company_factory("fk-update")
    repository.save_contact(contact_id="+5511900000001", contact_name="Real", company_id=company_id)
    question_id = repository.create_pending_question(
        contact_id="+5511900000001",
        customer_message="m",
        question_type="clarification",
        question_for_owner="q",
        company_id=company_id,
    )

    with pytest.raises(Exception, match=r"(?i)foreign key|violates"):
        with transaction() as connection:
            connection.cursor().execute(
                "UPDATE pending_questions SET contact_id = ? WHERE id = ?",
                ("contato-inexistente", question_id),
            )


def test_the_connection_is_returned_when_a_foreign_key_fires(company_factory) -> None:
    """O elo entre L-06 e I-01: a violação não pode levar a conexão junto.

    Foi exatamente esta combinação — FK ligada + conexão vazando — que
    transformou um erro de entrada em negação de serviço da plataforma.
    """
    from app.storage import db as db_module

    company_id = company_factory("fk-leak")
    opened: list[object] = []
    closed: list[object] = []
    original = db_module.get_connection

    class CountingConnection:
        """Proxy: `sqlite3.Connection.close` é somente-leitura, não dá para trocar."""

        def __init__(self, inner: object) -> None:
            self._inner = inner

        def close(self) -> None:
            closed.append(self._inner)
            self._inner.close()

        def __getattr__(self, name: str) -> object:
            return getattr(self._inner, name)

    def _counting_get_connection():
        connection = original()
        opened.append(connection)
        return CountingConnection(connection)

    db_module.get_connection = _counting_get_connection  # type: ignore[assignment]
    try:
        with pytest.raises(Exception, match=r"(?i)foreign key|violates"):
            repository.create_pending_question(
                contact_id="contato-que-nao-existe",
                customer_message="m",
                question_type="clarification",
                question_for_owner="q",
                company_id=company_id,
            )
    finally:
        db_module.get_connection = original  # type: ignore[assignment]

    assert len(opened) == len(closed) == 1, (
        f"aberturas={len(opened)} fechamentos={len(closed)}: a violação de FK vazou a conexão"
    )
