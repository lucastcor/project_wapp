"""Acesso SQL compartilhado pelos repositórios da plataforma.

Segue o mesmo contrato de `app/storage/db.py`: placeholders `?`, tradução
automática para Postgres e linhas devolvidas como `dict`.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

from app.storage.db import read_connection
from app.storage.db import transaction as db_transaction


class ScopedCursor:
    """Cursor cuja vida termina junto com o bloco `with`.

    Sem esta guarda, o cursor rendido por `transaction()` continuava
    funcionando depois do bloco — sobre uma conexão física **já devolvida ao
    pool**, que outra requisição pode ter recebido. Na prática isso é escrita
    de um tenant dentro da transação de outro, e falha em silêncio: o cursor
    não reclama.
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

    `connection.cursor()` ficava **fora** do `try`: se ele lançasse, o `finally`
    nunca era registrado e a conexão vazava. E não havia rollback, então uma
    transação abortada voltava ao pool e a próxima operação naquela conexão
    herdava `current transaction is aborted`.

    A implementação delega para `db.transaction()`, que é a fonte única do
    contrato — duas cópias do ciclo de vida divergem.
    """
    with db_transaction() as connection:
        scoped = ScopedCursor(connection.cursor())
        try:
            yield scoped
        finally:
            scoped.invalidate()


def fetch_all(query: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
    with read_connection() as connection:
        cursor = connection.cursor()
        cursor.execute(query, tuple(params))
        return [dict(row) for row in cursor.fetchall()]


def fetch_one(query: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
    with read_connection() as connection:
        cursor = connection.cursor()
        cursor.execute(query, tuple(params))
        row = cursor.fetchone()
        return dict(row) if row else None


def execute(query: str, params: Sequence[Any] = ()) -> int:
    """Executa uma escrita e devolve o id gerado (0 quando não houver)."""
    with db_transaction() as connection:
        cursor = connection.cursor()
        cursor.execute(query, tuple(params))
        return int(cursor.lastrowid or 0)


def execute_rowcount(query: str, params: Sequence[Any] = ()) -> int:
    with db_transaction() as connection:
        cursor = connection.cursor()
        cursor.execute(query, tuple(params))
        return int(cursor.rowcount)
