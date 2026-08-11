"""I-01 contra PostgreSQL real: falha repetida não pode esgotar o pool.

Este arquivo é a única prova que vale para o I-01. Os testes unitários de
`test_connection_lifecycle.py` usam conexão instrumentada e mostram que o
gerenciador devolve em todos os caminhos — mas conexão instrumentada não tem
pool, e era o pool que derrubava a plataforma.

A medição que abriu o achado, com `max_size=10`:

    tentativa 10: ForeignKeyViolation   pool_size=10 pool_available=0
    tentativa 11: PoolTimeout em 30.00s
    leitura seguinte: PoolTimeout — couldn't get a connection after 30.00 sec

Dez escritas inválidas, ao alcance de qualquer operator autenticado de um único
tenant, e a plataforma inteira parava para todos os outros até o processo
reiniciar. Aqui o pool é deliberadamente minúsculo (`max_size=2`) para que o
vazamento, se voltar, apareça na terceira falha em vez da décima primeira.

Roda apenas com `WAPP_TEST_DATABASE_URL` apontando para um PostgreSQL real.
"""

from __future__ import annotations

import os

import pytest

from app.storage import db

pytestmark = [
    pytest.mark.integration,
    pytest.mark.postgres,
    pytest.mark.skipif(
        not os.environ.get("WAPP_TEST_DATABASE_URL", "").startswith("postgres"),
        reason="exige PostgreSQL real em WAPP_TEST_DATABASE_URL",
    ),
]

POOL_SIZE = 2
FAILURES = POOL_SIZE * 3 + 1  # mais falhas do que o pool comporta, com folga


@pytest.fixture()
def tiny_pool(monkeypatch):
    """Repõe o pool global por um de `max_size=2` e restaura no fim.

    O pool é um singleton de módulo: sem restaurar, os testes seguintes
    herdariam duas conexões e falhariam por um motivo que nada tem a ver com
    o que eles testam.
    """
    from app.core.config import settings

    db.close_postgres_pool()
    monkeypatch.setattr(settings, "database_pool_size", POOL_SIZE, raising=False)
    pool = db._get_postgres_pool()
    assert pool.max_size == POOL_SIZE, f"pool com max_size={pool.max_size}"
    yield pool
    db.close_postgres_pool()


def _pool_stats(pool: object) -> dict[str, int]:
    stats = pool.get_stats()  # type: ignore[attr-defined]
    return {
        "size": int(stats.get("pool_size", 0)),
        "available": int(stats.get("pool_available", 0)),
        "waiting": int(stats.get("requests_waiting", 0)),
    }


def _write_that_violates_a_foreign_key() -> None:
    """Escrita inválida alcançável pela API: contato que não existe.

    `pending_questions.contact_id` referencia `contacts`. O PostgreSQL recusa
    independentemente de qualquer PRAGMA — é a mesma exceção que o operador
    provoca mandando um id de contato que não foi ingerido.
    """
    with db.transaction() as connection:
        connection.cursor().execute(
            """
            INSERT INTO pending_questions
                (contact_id, company_id, customer_message, question_type, question_for_owner, status)
            VALUES (?, ?, ?, ?, ?, 'pending')
            """,
            ("contato-que-nao-existe", 1, "m", "clarification", "q"),
        )


def test_repeated_invalid_writes_never_exhaust_the_pool(tiny_pool) -> None:
    """O teste que reproduz o DoS. Sem a correção, falha na terceira tentativa."""
    for attempt in range(1, FAILURES + 1):
        with pytest.raises(Exception, match=r"(?i)foreign key|violates") as caught:
            _write_that_violates_a_foreign_key()

        message = str(caught.value).lower()
        assert "pooltimeout" not in message and "couldn't get a connection" not in message, (
            f"tentativa {attempt}: o pool esgotou — {caught.value}"
        )

        stats = _pool_stats(tiny_pool)
        assert stats["available"] > 0, (
            f"tentativa {attempt}: nenhuma conexão disponível ({stats}). "
            "A conexão da tentativa anterior não voltou."
        )
        assert stats["waiting"] == 0, f"tentativa {attempt}: alguém esperando ({stats})"


def test_a_valid_read_still_answers_after_the_pool_was_hammered(tiny_pool) -> None:
    """Depois das falhas, o serviço continua atendendo — é o que o DoS negava."""
    for _ in range(FAILURES):
        with pytest.raises(Exception, match=r"(?i)foreign key|violates"):
            _write_that_violates_a_foreign_key()

    with db.read_connection() as connection:
        cursor = connection.cursor()
        cursor.execute("SELECT 1 AS ok")
        row = cursor.fetchone()

    assert int(row["ok"]) == 1


def test_a_write_still_commits_after_the_pool_was_hammered(tiny_pool, company_factory) -> None:
    """E continua aceitando escrita — não só leitura.

    Uma transação abortada devolvida ao pool sem rollback faria esta escrita
    herdar `current transaction is aborted`.
    """
    for _ in range(FAILURES):
        with pytest.raises(Exception, match=r"(?i)foreign key|violates"):
            _write_that_violates_a_foreign_key()

    company_id = company_factory("i01-depois")

    with db.read_connection() as connection:
        cursor = connection.cursor()
        cursor.execute("SELECT slug FROM companies WHERE id = ?", (company_id,))
        row = cursor.fetchone()

    assert row is not None, "a escrita depois das falhas não persistiu"


def test_no_connection_stays_checked_out_after_the_failures(tiny_pool) -> None:
    """A contabilidade do pool tem que fechar: tudo que saiu, voltou."""
    for _ in range(FAILURES):
        with pytest.raises(Exception, match=r"(?i)foreign key|violates"):
            _write_that_violates_a_foreign_key()

    stats = _pool_stats(tiny_pool)

    assert stats["available"] == stats["size"], (
        f"{stats['size'] - stats['available']} conexão(ões) ainda em uso: {stats}"
    )


def test_the_failing_write_really_is_rejected_by_the_database(tiny_pool) -> None:
    """Controle: se o INSERT passasse, os testes acima passariam por vacuidade."""
    with pytest.raises(Exception, match=r"(?i)foreign key|violates") as caught:
        _write_that_violates_a_foreign_key()

    assert "foreign key" in str(caught.value).lower() or "violates" in str(caught.value).lower(), (
        f"a escrita não foi recusada pela FK: {caught.value}"
    )
