"""Helpers de DDL compatíveis com SQLite e Postgres.

Usado pelas migrations da plataforma multiempresa. Mantido separado de
`app/storage/db.py` para evitar crescimento daquele módulo e ciclos de import.

Todo identificador interpolado passa por validação antes de alcançar o SQL.
As funções aqui recebem tabela, coluna e definição como parâmetros, e um
parâmetro é indistinguível de dado do chamador para quem lê o código: sem a
validação, `tests/test_dynamic_sql_guard.py` rejeitaria este módulo — com
razão. Validar na fronteira torna a segurança demonstrável no ponto de
construção, em vez de depender de todos os chamadores serem confiáveis.
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

# Um identificador simples: tabela, coluna ou nome de índice.
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Lista de colunas de um índice: identificadores separados por vírgula,
# opcionalmente com direção. Note a ausência de parênteses e de ';': um índice
# funcional ou uma segunda instrução não passam por aqui.
_INDEX_COLUMNS_PATTERN = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(\s+(?:ASC|DESC))?"
    r"(\s*,\s*[A-Za-z_][A-Za-z0-9_]*(\s+(?:ASC|DESC))?)*$",
    re.IGNORECASE,
)

# Corpo de DDL: tipos, restrições, defaults (inclusive JSON como '{}' e '[]'),
# checks e chaves estrangeiras. Exclui ';' e '--', de modo que uma definição
# não pode anexar uma segunda instrução nem comentar o resto da declaração.
_DEFINITION_PATTERN = re.compile(r"^[A-Za-z0-9_ \n\r\t()'\"{}\[\]:.,+*/<>=!|-]+$")


class UnsafeSchemaIdentifierError(ValueError):
    """Um identificador de esquema não passou na validação de fronteira."""


def assert_identifier(value: str, *, kind: str) -> str:
    """Valida um identificador simples antes de ele ser interpolado."""
    if not _IDENTIFIER_PATTERN.fullmatch(value):
        raise UnsafeSchemaIdentifierError(f"Unsafe {kind} identifier: {value!r}")
    return value


def assert_index_columns(columns: str) -> str:
    """Valida a lista de colunas de um índice."""
    if not _INDEX_COLUMNS_PATTERN.fullmatch(columns.strip()):
        raise UnsafeSchemaIdentifierError(f"Unsafe index column list: {columns!r}")
    return columns


def assert_definition(definition: str, *, kind: str) -> str:
    """Valida um corpo de DDL antes de ele ser interpolado."""
    if ";" in definition or "--" in definition:
        raise UnsafeSchemaIdentifierError(f"Unsafe {kind} definition: {definition!r}")
    if not _DEFINITION_PATTERN.fullmatch(definition):
        raise UnsafeSchemaIdentifierError(f"Unsafe {kind} definition: {definition!r}")
    return definition


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
    safe_table = assert_identifier(table, kind="table")
    safe_column = assert_identifier(column, kind="column")
    postgres = is_postgres()
    normalized = assert_definition(
        translate_column_definition(definition, postgres=postgres), kind="column"
    )

    if postgres:
        cursor.execute(
            f"ALTER TABLE {safe_table} ADD COLUMN IF NOT EXISTS {safe_column} {normalized}"
        )
        return

    cursor.execute(f"PRAGMA table_info({safe_table})")
    existing_columns = {row["name"] for row in cursor.fetchall()}
    if column not in existing_columns:
        cursor.execute(f"ALTER TABLE {safe_table} ADD COLUMN {safe_column} {normalized}")


def create_table(cursor: Any, table: str, columns_sql: str, *, extra_sql: str = "") -> None:
    """Cria a tabela traduzindo tipos entre SQLite e Postgres.

    `columns_sql` usa a sintaxe canônica do projeto:
    - `ID_PK` para chave primária autoincremental
    - `TIMESTAMP` para data/hora
    - `REAL` para ponto flutuante
    """
    safe_table = assert_identifier(table, kind="table")
    normalized = assert_definition(
        translate_column_definition(columns_sql, postgres=is_postgres()), kind="table"
    )
    safe_extra = assert_definition(extra_sql, kind="table constraint") if extra_sql else ""

    suffix = f",\n            {safe_extra}" if safe_extra else ""
    cursor.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {safe_table} (
            {normalized}{suffix}
        )
        """
    )


def create_index(cursor: Any, index_name: str, table: str, columns: str) -> None:
    safe_index = assert_identifier(index_name, kind="index")
    safe_table = assert_identifier(table, kind="table")
    safe_columns = assert_index_columns(columns)
    cursor.execute(
        f"CREATE INDEX IF NOT EXISTS {safe_index} ON {safe_table} ({safe_columns})"
    )


def create_unique_index(cursor: Any, index_name: str, table: str, columns: str) -> None:
    safe_index = assert_identifier(index_name, kind="index")
    safe_table = assert_identifier(table, kind="table")
    safe_columns = assert_index_columns(columns)
    cursor.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS {safe_index} ON {safe_table} ({safe_columns})"
    )
