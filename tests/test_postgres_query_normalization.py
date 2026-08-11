"""Normalização de query para PostgreSQL.

Estes testes cobrem dois defeitos que a suíte em SQLite nunca poderia pegar,
porque o ramo de normalização só roda quando o backend é PostgreSQL.

**Defeito 1 — parâmetro opcional sem tipo.** O repositório usa 67 vezes o
padrão `WHERE (? IS NULL OR coluna = ?)` para tornar um filtro opcional. No
SQLite funciona; no PostgreSQL o planner não consegue inferir o tipo de um
parâmetro que aparece **apenas** em `IS NULL`, e responde
`could not determine data type of parameter $N`. Isso derrubava 69 testes
contra PostgreSQL real — ou seja, quase toda listagem filtrável do produto.

**Defeito 2 — tradução de tipos.** Coberto em
`tests/test_schema_type_translation.py`.

A correção é central, na tradução, e não nos 67 pontos de chamada: um cast
espalhado por dezenas de arquivos seria esquecido no próximo `SELECT`.
"""

from __future__ import annotations

import pytest

from app.storage.db import _normalize_postgres_query


def normalize(query: str) -> str:
    normalized, _params, _returning = _normalize_postgres_query(query, ())
    return normalized


class TestOptionalParameterCast:
    def test_a_bare_parameter_in_is_null_gets_an_explicit_cast(self) -> None:
        normalized = normalize("SELECT * FROM t WHERE (? IS NULL OR company_id = ?)")

        # O primeiro parâmetro precisa de tipo; o segundo infere da coluna.
        assert "%s::text IS NULL" in normalized
        assert "company_id = %s" in normalized

    def test_the_cast_is_applied_to_every_occurrence(self) -> None:
        normalized = normalize(
            """
            SELECT * FROM t
            WHERE company_id = ?
              AND (? IS NULL OR workflow_id = ?)
              AND (? IS NULL OR status = ?)
            """
        )

        assert normalized.count("%s::text IS NULL") == 2

    def test_a_parameter_not_used_in_is_null_is_untouched(self) -> None:
        normalized = normalize("SELECT * FROM t WHERE company_id = ? AND status = ?")

        assert "::text" not in normalized
        assert normalized.count("%s") == 2

    def test_a_column_compared_to_null_is_untouched(self) -> None:
        """`coluna IS NULL` não é parâmetro e não pode receber cast."""
        normalized = normalize("SELECT * FROM t WHERE revoked_at IS NULL AND id = ?")

        assert "revoked_at IS NULL" in normalized
        assert "::text" not in normalized

    def test_is_not_null_on_a_parameter_also_gets_a_cast(self) -> None:
        normalized = normalize("SELECT * FROM t WHERE (? IS NOT NULL AND id = ?)")

        assert "%s::text IS NOT NULL" in normalized

    def test_spacing_and_case_do_not_matter(self) -> None:
        # O espaçamento original é preservado; só o parâmetro ganha tipo.
        assert normalize("SELECT 1 WHERE ?   is null") == "SELECT 1 WHERE %s::text   is null"

    def test_placeholder_count_is_preserved(self) -> None:
        """O cast não pode criar nem remover placeholder: a tupla precisa casar."""
        query = "SELECT * FROM t WHERE (? IS NULL OR a = ?) AND (? IS NULL OR b = ?)"

        assert normalize(query).count("%s") == query.count("?")


class TestExistingNormalizationIsPreserved:
    def test_insert_or_ignore_becomes_on_conflict(self) -> None:
        normalized = normalize("INSERT OR IGNORE INTO contacts (contact_id) VALUES (?)")

        assert "ON CONFLICT (contact_id) DO NOTHING" in normalized
        assert "INSERT OR IGNORE" not in normalized

    def test_sqlite_datetime_offset_becomes_an_interval(self) -> None:
        normalized = normalize("SELECT datetime('now', ?)")

        assert "CAST(%s AS interval)" in normalized

    def test_insert_into_an_id_table_gets_returning(self) -> None:
        normalized, _params, returning = _normalize_postgres_query(
            "INSERT INTO automation_jobs (company_id) VALUES (?)",
            (1,),
        )

        assert normalized.rstrip().endswith("RETURNING id")
        assert returning == "automation_jobs"


@pytest.mark.parametrize(
    "query",
    [
        "SELECT * FROM t WHERE (? IS NULL OR company_id = ?)",
        "SELECT * FROM t WHERE (? IS NULL OR status = ?) AND (? IS NULL OR kind = ?)",
        "UPDATE t SET a = ? WHERE (? IS NULL OR id = ?)",
    ],
)
def test_no_bare_parameter_remains_in_an_is_null_check(query: str) -> None:
    """Guarda geral: nenhum `%s IS NULL` sem cast pode sobreviver."""
    import re

    normalized = normalize(query)

    assert not re.search(r"%s\s+IS\s+(NOT\s+)?NULL", normalized, re.IGNORECASE), normalized
