"""Tradução de tipos entre SQLite e PostgreSQL.

Estes testes existem por causa de um defeito que a suíte inteira nunca pegou:
`columns_sql.replace("TIMESTAMP", "TIMESTAMPTZ")` também reescrevia
`CURRENT_TIMESTAMP` como `CURRENT_TIMESTAMPTZ`, que o PostgreSQL rejeita com
`cannot use column reference in DEFAULT expression`.

Consequência: **nenhuma tabela com `DEFAULT CURRENT_TIMESTAMP` podia ser criada
no PostgreSQL** — 90 ocorrências no schema. O produto declarava suportar
PostgreSQL em produção e o `init_db()` falhava na primeira migration.

Nada disso aparecia na suíte porque ela roda em SQLite, onde o ramo de
tradução nem é executado. É o exemplo concreto de por que SQLite não prova
comportamento PostgreSQL.
"""

from __future__ import annotations

import pytest

from app.storage.schema_utils import translate_column_definition


class TestPostgresTranslation:
    def test_current_timestamp_is_preserved(self) -> None:
        """O defeito original: `CURRENT_TIMESTAMP` não pode virar outra coisa."""
        translated = translate_column_definition(
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
            postgres=True,
        )

        assert "CURRENT_TIMESTAMPTZ" not in translated
        assert "DEFAULT CURRENT_TIMESTAMP" in translated
        assert "created_at TIMESTAMPTZ" in translated

    def test_the_column_type_is_still_translated(self) -> None:
        translated = translate_column_definition("updated_at TIMESTAMP", postgres=True)

        assert translated == "updated_at TIMESTAMPTZ"

    def test_id_pk_becomes_bigserial(self) -> None:
        assert translate_column_definition("id ID_PK", postgres=True) == (
            "id BIGSERIAL PRIMARY KEY"
        )

    def test_real_becomes_double_precision(self) -> None:
        assert translate_column_definition("score REAL", postgres=True) == (
            "score DOUBLE PRECISION"
        )

    def test_a_column_name_containing_a_type_word_is_untouched(self) -> None:
        """`real_time_score` não pode virar `DOUBLE PRECISION_time_score`."""
        translated = translate_column_definition(
            "real_time_score REAL, timestamp_source TEXT",
            postgres=True,
        )

        assert "real_time_score DOUBLE PRECISION" in translated
        assert "timestamp_source TEXT" in translated
        assert "DOUBLE PRECISION_time_score" not in translated

    def test_translation_is_single_pass(self) -> None:
        """Traduzir duas vezes não pode acumular substituição."""
        once = translate_column_definition("a TIMESTAMP, b REAL", postgres=True)
        twice = translate_column_definition(once, postgres=True)

        assert once == twice

    def test_a_full_table_definition_survives(self) -> None:
        definition = """
            id ID_PK,
            company_id INTEGER NOT NULL,
            quality_score REAL NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        """
        translated = translate_column_definition(definition, postgres=True)

        assert translated.count("CURRENT_TIMESTAMP,") + translated.count(
            "CURRENT_TIMESTAMP\n"
        ) == 2
        assert "CURRENT_TIMESTAMPTZ" not in translated
        assert "BIGSERIAL PRIMARY KEY" in translated
        assert "DOUBLE PRECISION" in translated


class TestSqliteTranslation:
    def test_id_pk_becomes_autoincrement(self) -> None:
        assert translate_column_definition("id ID_PK", postgres=False) == (
            "id INTEGER PRIMARY KEY AUTOINCREMENT"
        )

    def test_timestamp_and_real_are_native(self) -> None:
        definition = "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, score REAL"

        assert translate_column_definition(definition, postgres=False) == definition


class TestEnsureColumnTranslation:
    """`ensure_column` precisa traduzir igual a `create_table`.

    Sem isso, uma coluna acrescentada por migration nasce `TIMESTAMP` no
    PostgreSQL enquanto a mesma coluna criada de origem nasce `TIMESTAMPTZ`:
    dois bancos com o mesmo número de migration e tipos diferentes, conforme a
    ordem em que as migrations foram aplicadas.
    """

    def test_a_timestamp_column_added_later_uses_the_same_type(self) -> None:
        assert translate_column_definition("TIMESTAMP", postgres=True) == "TIMESTAMPTZ"

    def test_a_defaulted_timestamp_column_added_later_is_valid(self) -> None:
        translated = translate_column_definition(
            "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
            postgres=True,
        )

        assert translated == "TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP"


@pytest.mark.parametrize(
    "definition",
    [
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        "id ID_PK, ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        "ratio REAL NOT NULL DEFAULT 0.2",
        "expires_at TIMESTAMP",
    ],
)
def test_no_translation_produces_an_unknown_postgres_token(definition: str) -> None:
    """Guarda contra qualquer regressão que invente um token inválido."""
    translated = translate_column_definition(definition, postgres=True)

    for invalid in ("CURRENT_TIMESTAMPTZ", "TIMESTAMPTZTZ", "DOUBLE PRECISION_"):
        assert invalid not in translated, f"{definition} produziu {invalid}"
