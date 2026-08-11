"""I-01: a conexão volta para o pool em todos os caminhos, exatamente uma vez.

O padrão que existia em 99 das 107 aquisições era este:

    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute(...)
    connection.commit()
    connection.close()          # <- só aqui, e só se nada acima lançar

Qualquer coisa que lance antes da última linha pula o `close`. E lançar é
banal: `cursor()`, `execute()` com violação de constraint, `fetchall()`,
`commit()`, uma conversão de linha, um `int()` sobre coluna nula.

No SQLite o coletor de lixo às vezes salva — o que é pior do que nunca salvar,
porque esconde o defeito. No pool do PostgreSQL não existe salvação: `putconn`
só acontece dentro de `close()`. Medido contra PostgreSQL real com
`max_size=10`, dez escritas inválidas esgotavam o pool e a décima primeira dava
`PoolTimeout`: qualquer operator autenticado de um único tenant derrubava a
plataforma para todos os outros.

Estes testes usam uma conexão instrumentada em vez de um banco real porque a
pergunta não é "a query funcionou" — é "quantas vezes a conexão foi devolvida".
Um teste que não conta aberturas e fechamentos não prova nada sobre vazamento.
"""

from __future__ import annotations

import pytest

from app.storage import db


class InstrumentedConnection:
    """Conexão falsa que conta o próprio ciclo de vida e falha sob demanda."""

    def __init__(self, *, fail_on: str | None = None) -> None:
        self.fail_on = fail_on
        self.closed_count = 0
        self.commit_count = 0
        self.rollback_count = 0
        self.cursor_count = 0

    def _maybe_fail(self, stage: str) -> None:
        if self.fail_on == stage:
            raise RuntimeError(f"falha injetada em {stage}")

    def cursor(self) -> InstrumentedCursor:
        self.cursor_count += 1
        self._maybe_fail("cursor")
        return InstrumentedCursor(self)

    def commit(self) -> None:
        self.commit_count += 1
        self._maybe_fail("commit")

    def rollback(self) -> None:
        self.rollback_count += 1
        self._maybe_fail("rollback")

    def close(self) -> None:
        self.closed_count += 1


class InstrumentedCursor:
    def __init__(self, connection: InstrumentedConnection) -> None:
        self._connection = connection

    def execute(self, *_args: object, **_kwargs: object) -> InstrumentedCursor:
        self._connection._maybe_fail("execute")
        return self

    def fetchall(self) -> list[dict[str, object]]:
        self._connection._maybe_fail("fetch")
        return [{"id": 1}]

    def fetchone(self) -> dict[str, object]:
        self._connection._maybe_fail("fetch")
        return {"id": 1}


@pytest.fixture()
def instrumented(monkeypatch):
    """Substitui `get_connection` e devolve a conexão criada, para inspeção."""
    created: list[InstrumentedConnection] = []

    def _factory(*, fail_on: str | None = None) -> InstrumentedConnection:
        def _get_connection() -> InstrumentedConnection:
            connection = InstrumentedConnection(fail_on=fail_on)
            created.append(connection)
            return connection

        monkeypatch.setattr(db, "get_connection", _get_connection)
        return connection_holder

    class _Holder:
        @property
        def connections(self) -> list[InstrumentedConnection]:
            return created

        @property
        def only(self) -> InstrumentedConnection:
            assert len(created) == 1, f"esperava 1 conexão, houve {len(created)}"
            return created[0]

    connection_holder = _Holder()
    return _factory


# --- caminho feliz -----------------------------------------------------------------------


def test_a_successful_transaction_commits_once_and_closes_once(instrumented) -> None:
    holder = instrumented()

    with db.transaction() as connection:
        connection.cursor().execute("INSERT INTO t VALUES (1)")

    assert holder.only.commit_count == 1
    assert holder.only.closed_count == 1
    assert holder.only.rollback_count == 0


def test_a_successful_read_closes_once_and_never_commits(instrumented) -> None:
    """Leitura com commit custa round-trip e segura a transação por mais tempo."""
    holder = instrumented()

    with db.read_connection() as connection:
        connection.cursor().execute("SELECT 1")

    assert holder.only.closed_count == 1
    assert holder.only.commit_count == 0


# --- cada estágio que pode lançar --------------------------------------------------------

# `rollback` fica fora desta matriz: para ele disparar, o corpo precisa falhar
# antes, e aí a falha injetada não é mais isolada. Tem teste próprio abaixo.
FAILURE_STAGES = ["cursor", "execute", "fetch", "commit"]


@pytest.mark.parametrize("stage", FAILURE_STAGES)
def test_a_transaction_returns_the_connection_exactly_once_when_a_stage_fails(
    instrumented,
    stage: str,
) -> None:
    """Cada estágio, isolado. É a matriz que o padrão antigo reprovava inteira."""
    holder = instrumented(fail_on=stage)

    with pytest.raises(RuntimeError):
        with db.transaction() as connection:
            cursor = connection.cursor()
            cursor.execute("INSERT INTO t VALUES (1)")
            cursor.fetchall()

    assert holder.only.closed_count == 1, (
        f"falha em `{stage}` devolveu a conexão {holder.only.closed_count} vezes"
    )


@pytest.mark.parametrize("stage", ["cursor", "execute", "fetch"])
def test_a_read_returns_the_connection_exactly_once_when_a_stage_fails(
    instrumented,
    stage: str,
) -> None:
    holder = instrumented(fail_on=stage)

    with pytest.raises(RuntimeError):
        with db.read_connection() as connection:
            cursor = connection.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchall()

    assert holder.only.closed_count == 1


def test_a_failure_after_fetch_still_returns_the_connection(instrumented) -> None:
    """Conversão de linha que estoura depois do fetch — `int()` sobre coluna nula.

    Este é o caso que o padrão antigo mais escondia: a query funcionou, o
    `close()` estava logo abaixo, e a conversão no meio pulava para o topo da
    pilha levando a conexão junto.
    """
    holder = instrumented()

    with pytest.raises(KeyError):
        with db.transaction() as connection:
            rows = connection.cursor().execute("SELECT ...").fetchall()
            int(rows[0]["ausente"])

    assert holder.only.closed_count == 1
    assert holder.only.commit_count == 0, "commitou apesar do erro"


def test_the_transaction_rolls_back_before_returning_a_failed_connection(instrumented) -> None:
    """Sem rollback, a conexão volta suja e a próxima operação nela herda o aborto."""
    holder = instrumented(fail_on="execute")

    with pytest.raises(RuntimeError):
        with db.transaction() as connection:
            connection.cursor().execute("INSERT INTO t VALUES (1)")

    assert holder.only.rollback_count == 1
    assert holder.only.closed_count == 1


def test_a_failing_rollback_does_not_replace_the_original_error(instrumented) -> None:
    """O rollback que falha não pode virar a exceção que o chamador vê.

    Se ele mascarasse o erro original, a causa real do problema sumiria e o
    diagnóstico apontaria para a limpeza em vez da falha.
    """
    holder = instrumented(fail_on="rollback")

    with pytest.raises(RuntimeError, match="falha injetada em rollback"):
        with db.transaction():
            raise RuntimeError("falha injetada em rollback")

    assert holder.only.closed_count == 1


def test_a_base_exception_also_returns_the_connection(instrumented) -> None:
    """`KeyboardInterrupt` no meio de uma escrita também precisa devolver."""
    holder = instrumented()

    with pytest.raises(KeyboardInterrupt):
        with db.transaction():
            raise KeyboardInterrupt

    assert holder.only.closed_count == 1
    assert holder.only.commit_count == 0


# --- idempotência de close ---------------------------------------------------------------


def test_closing_the_postgres_wrapper_twice_returns_the_connection_once() -> None:
    """Devolver duas vezes é pior que vazar.

    O pool passa a acreditar que tem uma conexão livre a mais do que tem, e
    duas partes do código recebem o mesmo objeto. Vazar desperdiça um recurso;
    devolver duas vezes corrompe a contabilidade do pool.
    """
    returned: list[object] = []

    class FakePool:
        def putconn(self, connection: object) -> None:
            returned.append(connection)

    class FakeRaw:
        def rollback(self) -> None:
            return None

    raw = FakeRaw()
    wrapper = db.PostgresConnectionWrapper(raw, FakePool())

    wrapper.close()
    wrapper.close()
    wrapper.close()

    assert returned == [raw], f"putconn chamado {len(returned)} vezes"


def test_the_wrapper_returns_the_connection_even_when_rollback_fails() -> None:
    """Rollback quebrado não pode impedir a devolução — isso vazaria o pool."""
    returned: list[object] = []

    class FakePool:
        def putconn(self, connection: object) -> None:
            returned.append(connection)

    class BrokenRaw:
        def rollback(self) -> None:
            raise RuntimeError("conexão morta")

    raw = BrokenRaw()
    db.PostgresConnectionWrapper(raw, FakePool()).close()

    assert returned == [raw]


# --- o cursor não sobrevive ao bloco -----------------------------------------------------


def test_a_cursor_cannot_be_used_after_its_transaction_ended() -> None:
    """Use-after-return: o cursor operava sobre conexão já devolvida ao pool.

    Quem recebesse aquela conexão em seguida é outra requisição — possivelmente
    de outro tenant. Uma escrita ali entra na transação alheia, e nada reclama.
    """
    from app.storage.sql_support import transaction as scoped_transaction

    with scoped_transaction() as cursor:
        cursor.execute("SELECT 1")
        escaped = cursor

    with pytest.raises(RuntimeError, match="already ended"):
        escaped.execute("SELECT 1")


def test_the_cursor_still_works_inside_its_block() -> None:
    """Controle positivo: invalidar cedo demais quebraria todo o repositório."""
    from app.storage.sql_support import transaction as scoped_transaction

    with scoped_transaction() as cursor:
        cursor.execute("SELECT 1 AS ok")
        assert dict(cursor.fetchone())["ok"] == 1


# --- guarda estrutural: nova aquisição sem gerenciador ------------------------------------


def _direct_acquisitions(module_path: str) -> list[str]:
    """Funções que adquirem conexão sem `try/finally` **envolvendo a aquisição**.

    A primeira versão deste detector aceitava 6 de 7 aquisições inseguras
    construídas contra ele. Os dois piores casos:

      * `try/finally` **não relacionado** em qualquer ponto da função fazia a
        função inteira passar, mesmo sem `close()` nenhum;
      * `db.get_connection()` (chamada por atributo) não era reconhecida como
        aquisição — e essa é a forma mais provável numa regressão real, porque
        `app.storage.db` é importado como módulo em todo lugar.

    Agora a verificação é posicional: a aquisição precisa estar **dentro** do
    corpo de um `try` que tenha `finalbody`, ou dentro de um `with`. Reconhece
    `get_connection`, `db.get_connection`, `sqlite3.connect` e `psycopg.connect`.
    """
    import ast
    import pathlib

    path = pathlib.Path(module_path)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=module_path)

    def _is_acquisition(node: ast.AST) -> bool:
        if not isinstance(node, ast.Call):
            return False
        func = node.func
        if isinstance(func, ast.Name):
            return func.id == "get_connection"
        if isinstance(func, ast.Attribute):
            return func.attr in {"get_connection", "connect"}
        return False

    def _is_manager_shape(fn: ast.AST, acquisition: ast.AST) -> bool:
        """`x = adquirir()` seguido imediatamente de `try: ... finally: x.close()`.

        É a única forma correta de escrever o próprio gerenciador: a aquisição
        **precisa** estar antes do `try`, senão não há o que fechar no
        `finally`. Vale só quando a atribuição é o statement imediatamente
        anterior ao `try`.
        """
        body = getattr(fn, "body", [])
        for index, statement in enumerate(body[:-1]):
            if not isinstance(statement, ast.Assign):
                continue
            if not any(inner is acquisition for inner in ast.walk(statement)):
                continue
            following = body[index + 1]
            if isinstance(following, ast.Try) and following.finalbody:
                closes = any(
                    isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Attribute)
                    and inner.func.attr == "close"
                    for stmt in following.finalbody
                    for inner in ast.walk(stmt)
                )
                if closes:
                    return True
        return False

    def _covered(fn: ast.AST, acquisition: ast.AST) -> bool:
        """A aquisição está dentro de um `try` com `finally`, ou de um `with`?"""
        if _is_manager_shape(fn, acquisition):
            return True
        for node in ast.walk(fn):
            if isinstance(node, ast.Try) and node.finalbody:
                guarded = [inner for stmt in node.body for inner in ast.walk(stmt)]
            elif isinstance(node, ast.With | ast.AsyncWith):
                guarded = [inner for stmt in node.body for inner in ast.walk(stmt)]
                guarded += [
                    inner for item in node.items for inner in ast.walk(item.context_expr)
                ]
            else:
                continue
            if any(inner is acquisition for inner in guarded):
                return True
        return False

    # A fábrica é o único lugar onde adquirir sem gerenciar é o trabalho.
    FACTORIES = {"get_connection", "_get_postgres_pool"}

    offenders: list[str] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if fn.name in FACTORIES:
            continue
        # Um `def` aninhado é analisado por si; não herda a proteção de fora.
        acquisitions = [
            node
            for node in ast.walk(fn)
            if _is_acquisition(node)
            and not any(
                isinstance(inner, ast.FunctionDef | ast.AsyncFunctionDef)
                and inner is not fn
                and any(n is node for n in ast.walk(inner))
                for inner in ast.walk(fn)
            )
        ]
        if any(not _covered(fn, acquisition) for acquisition in acquisitions):
            offenders.append(f"{module_path}::{fn.name}")
    return offenders


def _production_modules_with_sql() -> list[str]:
    """Todo módulo de produção que adquire conexão — descoberto, não listado.

    Uma lista à mão deixou 12 módulos de fora. Descobrir é a única forma de a
    varredura acompanhar um arquivo novo.
    """
    import pathlib

    found: list[str] = []
    for path in sorted(pathlib.Path("app").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if "get_connection(" in text or "sqlite3.connect(" in text or "psycopg.connect(" in text:
            found.append(path.as_posix())
    return found


PRODUCTION_MODULES_WITH_SQL = _production_modules_with_sql()


def test_the_module_discovery_finds_the_known_sql_modules() -> None:
    """Sem isto, um erro na descoberta faria a varredura passar por vacuidade."""
    discovered = set(PRODUCTION_MODULES_WITH_SQL)

    # `repository.py` saiu desta lista de propósito: ele não adquire mais nada
    # diretamente, só usa os gerenciadores. É o resultado esperado do I-01.
    assert "app/storage/db.py" in discovered, "o módulo da fábrica não foi descoberto"
    assert "app/storage/repository.py" not in discovered, (
        "repository.py voltou a adquirir conexão direto"
    )
    assert discovered, "a descoberta não encontrou nenhum módulo"


@pytest.mark.parametrize("module_path", PRODUCTION_MODULES_WITH_SQL)
def test_no_production_function_acquires_a_connection_without_a_lifecycle(module_path: str) -> None:
    """Uma aquisição nova sem gerenciador reabre o I-01.

    A varredura é deliberadamente estreita: ela só afirma que `get_connection()`
    fora de `try/finally` é errado — proposição decidível e sempre verdadeira
    aqui. Ela **não** afirma que o resto está correto; isso é o que os testes de
    contagem e o teste de pool contra PostgreSQL real fazem.
    """
    offenders = _direct_acquisitions(module_path)

    assert offenders == [], (
        "Aquisição direta de conexão sem ciclo de vida. Use `transaction()`, "
        f"`read_connection()` ou `connection_scope()`: {offenders}"
    )


def test_the_structural_guard_would_catch_a_regression(tmp_path) -> None:
    """Controle negativo: sem isto a varredura passaria por vacuidade."""
    offending = tmp_path / "regressao.py"
    offending.write_text(
        "def salvar(x):\n"
        "    connection = get_connection()\n"
        "    connection.cursor().execute('INSERT ...')\n"
        "    connection.commit()\n"
        "    connection.close()\n",
        encoding="utf-8",
    )

    assert _direct_acquisitions(str(offending)) == [f"{offending}::salvar"]


def test_the_structural_guard_accepts_the_managers(tmp_path) -> None:
    """Controle positivo: o próprio formato correto não pode ser reprovado."""
    correct = tmp_path / "correto.py"
    correct.write_text(
        "def salvar(x):\n"
        "    with transaction() as connection:\n"
        "        connection.cursor().execute('INSERT ...')\n",
        encoding="utf-8",
    )

    assert _direct_acquisitions(str(correct)) == []
