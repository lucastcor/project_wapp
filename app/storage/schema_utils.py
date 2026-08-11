"""Helpers de DDL compatíveis com SQLite e Postgres.

Usado pelas migrations da plataforma multiempresa. Mantido separado de
`app/storage/db.py` para evitar crescimento daquele módulo e ciclos de import.
"""

from __future__ import annotations

import re
from typing import Any

# Tokens da sintaxe canônica do projeto e sua tradução por backend.
#
# A substituição é por **token**, com fronteira de palavra, e num único passe.
# A versão anterior usava `str.replace` encadeado, e isso reescrevia
# `CURRENT_TIMESTAMP` como `CURRENT_TIMESTAMPTZ` — que o PostgreSQL rejeita
# ("cannot use column reference in DEFAULT expression"). Como há 90 colunas com
# `DEFAULT CURRENT_TIMESTAMP`, nenhuma tabela podia ser criada no PostgreSQL.
#
# `\b` resolve o caso porque `_` é caractere de palavra: em `CURRENT_TIMESTAMP`
# não existe fronteira antes de `TIMESTAMP`. O passe único evita que uma
# tradução alimente a seguinte.
_POSTGRES_TOKENS: dict[str, str] = {
    "ID_PK": "BIGSERIAL PRIMARY KEY",
    "TIMESTAMP": "TIMESTAMPTZ",
    "REAL": "DOUBLE PRECISION",
}

_SQLITE_TOKENS: dict[str, str] = {
    "ID_PK": "INTEGER PRIMARY KEY AUTOINCREMENT",
}

_TOKEN_PATTERN = re.compile(r"\b(?:ID_PK|TIMESTAMP|REAL)\b")


def translate_column_definition(definition: str, *, postgres: bool) -> str:
    """Traduz a sintaxe canônica para o dialeto do backend.

    Usada por `create_table` e por `ensure_column`, para que uma coluna
    acrescentada depois nasça com o mesmo tipo de uma criada de origem.
    """
    tokens = _POSTGRES_TOKENS if postgres else _SQLITE_TOKENS
    return _TOKEN_PATTERN.sub(lambda match: tokens.get(match.group(0), match.group(0)), definition)


def is_postgres() -> bool:
    from app.storage.db import get_database_backend

    return get_database_backend() == "postgres"


def ensure_column(cursor: Any, table: str, column: str, definition: str) -> None:
    """Adiciona a coluna se ela ainda não existir. Seguro para reexecução."""
    postgres = is_postgres()
    normalized = translate_column_definition(definition, postgres=postgres)

    if postgres:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {normalized}")
        return

    cursor.execute(f"PRAGMA table_info({table})")
    existing_columns = {row["name"] for row in cursor.fetchall()}
    if column not in existing_columns:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {normalized}")


def create_table(cursor: Any, table: str, columns_sql: str, *, extra_sql: str = "") -> None:
    """Cria a tabela traduzindo tipos entre SQLite e Postgres.

    `columns_sql` usa a sintaxe canônica do projeto:
    - `ID_PK` para chave primária autoincremental
    - `TIMESTAMP` para data/hora
    - `REAL` para ponto flutuante
    """
    normalized = translate_column_definition(columns_sql, postgres=is_postgres())

    suffix = f",\n            {extra_sql}" if extra_sql else ""
    cursor.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {table} (
            {normalized}{suffix}
        )
        """
    )


def create_index(cursor: Any, index_name: str, table: str, columns: str) -> None:
    cursor.execute(f"CREATE INDEX IF NOT EXISTS {index_name} ON {table} ({columns})")


def create_unique_index(cursor: Any, index_name: str, table: str, columns: str) -> None:
    cursor.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS {index_name} ON {table} ({columns})")
