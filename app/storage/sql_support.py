"""Acesso SQL compartilhado pelos repositórios da plataforma.

Segue o mesmo contrato de `app/storage/db.py`: placeholders `?`, tradução
automática para PostgreSQL e linhas devolvidas como `dict`.

Este módulo **não** reimplementa o ciclo de vida de conexão. Ele delega para
`db_session()` e `db_transaction()`, que são a fonte única de como uma conexão
é adquirida, comitada, revertida e devolvida ao pool. Duas cópias desse ciclo
divergem, e o modo de falha é caro: uma conexão devolvida com transação aberta
envenena o próximo tomador e, repetida, esgota o pool.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

from app.storage.db import db_session, db_transaction


class ScopedCursor:
    """Cursor cuja vida termina junto com o bloco `with`.

    Sem esta guarda, um cursor obtido dentro de `transaction()` continua
    utilizável depois do bloco — sobre uma conexão física **já devolvida ao
    pool**, que outra requisição pode ter recebido. Na prática isso é escrita
    de um tenant dentro da transação de outro, e falha em silêncio: o cursor
    não reclama, apenas escreve no lugar errado.
    """

    def __init__(self, cursor: Any) -> None:
        self._cursor = cursor
        self._valid = True

    def invalidate(self) -> None:
        self._valid = False

    def __getattr__(self, name: str) -> Any:
        if not object.__getattribute__(self, "_valid"):
            raise RuntimeError(
                "This cursor belongs to a transaction that already ended. "
                "Its connection is back in the pool and may be serving another request."
            )
        return getattr(object.__getattribute__(self, "_cursor"), name)


@contextmanager
def transaction() -> Iterator[Any]:
    """Cursor transacional: commit no sucesso, rollback no erro, close sempre.

    `connection.cursor()` fica dentro do bloco gerido por `db_transaction()`,
    então uma falha ao criar o cursor ainda passa pelo `finally` que devolve a
    conexão.
    """
    with db_transaction() as connection:
        scoped = ScopedCursor(connection.cursor())
        try:
            yield scoped
        finally:
            scoped.invalidate()


def fetch_all(query: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
    with db_session() as connection:
        cursor = connection.cursor()
        cursor.execute(query, tuple(params))
        return [dict(row) for row in cursor.fetchall()]


def fetch_one(query: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
    with db_session() as connection:
        cursor = connection.cursor()
        cursor.execute(query, tuple(params))
        row = cursor.fetchone()
        return dict(row) if row else None


def execute(query: str, params: Sequence[Any] = ()) -> int:
    """Executa uma escrita e devolve o id gerado (0 quando não houver).

    O id vem de `cursor.lastrowid`, que no PostgreSQL depende de a tabela estar
    registrada em `SQLITE_ID_TABLES` — é lá que o wrapper anexa `RETURNING id`.
    Uma tabela nova esquecida naquele registro devolve 0 aqui, e só no
    PostgreSQL.
    """
    with db_transaction() as connection:
        cursor = connection.cursor()
        cursor.execute(query, tuple(params))
        return int(cursor.lastrowid or 0)


def execute_rowcount(query: str, params: Sequence[Any] = ()) -> int:
    with db_transaction() as connection:
        cursor = connection.cursor()
        cursor.execute(query, tuple(params))
        return int(cursor.rowcount)


# Identificador simples: só o nome da coluna é interpolado, e ele é validado.
_COLUMN_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Predicado neutro para "não filtrar". Uma constante, e não `1=1` montado na
# hora, para que o SQL resultante seja estável e comparável em teste.
ALWAYS_TRUE_CONDITION = "1 = 1"


def optional_equals(column: str, value: Any) -> tuple[str, tuple[Any, ...]]:
    """Condição de igualdade opcional, sem placeholder sem tipo.

    `value is None` significa "não filtre", e devolve o predicado neutro **sem
    parâmetro nenhum**.

    O padrão que isto substitui é `(? IS NULL OR coluna = ?)`. Ele funciona no
    SQLite e quebra no PostgreSQL com `IndeterminateDatatype` (SQLSTATE 42P18):
    o primeiro `?` aparece sozinho num `IS NULL`, sem nada de onde inferir o
    tipo, e o servidor recusa a query antes de executá-la. O erro só aparece no
    backend de produção, o que é a pior forma possível de descobri-lo.

    O valor vai sempre como parâmetro; só o identificador da coluna é
    interpolado, e é validado contra um padrão estrito para que um chamador
    futuro não consiga transformar isto num ponto de injeção.
    """
    if not _COLUMN_PATTERN.match(column):
        raise ValueError(f"Unsafe SQL identifier for optional filter: {column!r}")
    if value is None:
        return ALWAYS_TRUE_CONDITION, ()
    return f"{column} = ?", (value,)
