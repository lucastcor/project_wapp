"""Garantia estática de que SQL interpolado nunca carrega dado do chamador.

O Bandit sinaliza toda f-string que vira SQL (B608) porque não consegue provar
a origem do que é interpolado. Este teste prova, percorrendo a AST de
`app/storage` e `app/services`:

1. toda interpolação em SQL é uma constante de colunas ou um fragmento
   estrutural — cada um listado e justificado individualmente abaixo;
2. cada constante de colunas contém apenas nomes de coluna literais;
3. cada fragmento estrutural é construído exatamente pela expressão registrada
   aqui — mudar a construção quebra o teste;
4. a allowlist não acumula entradas mortas.

Valores de consulta continuam sempre em placeholders `?`.

Por isso o CI pula B608: esta verificação é mais forte e mais específica que a
heurística do Bandit, que não consegue distinguir nome de coluna de valor.
"""

from __future__ import annotations

import ast
import pathlib
import re

SCANNED_DIRS = (
    pathlib.Path("app/storage"),
    pathlib.Path("app/services"),
    pathlib.Path("app/tools"),
)

# --- exceção 1: constantes com listas fixas de colunas -------------------------------
#
# Cada uma é um literal de string definido no topo do módulo. Nenhuma é
# montada a partir de entrada do usuário.
ALLOWED_COLUMN_CONSTANTS: dict[str, str] = {
    "MESSAGE_EXAMPLE_COLUMNS": "colunas de message_examples",
    "_COLUMNS": "colunas da tabela principal do próprio módulo",
    "_DOCUMENT_COLUMNS": "colunas de knowledge_documents",
    "_VERSION_COLUMNS": "colunas de dataset_versions",
    "_ITEM_COLUMNS": "colunas de dataset_version_items",
    "_RUN_COLUMNS": "colunas de evaluation_runs",
    "_CASE_COLUMNS": "colunas de evaluation_cases",
    "_BATCH_COLUMNS": "colunas de conversation_import_batches",
    "_CANDIDATE_COLUMNS": "colunas de training_candidates",
    "_PRINCIPAL_COLUMNS": "colunas de platform_principals",
    "_ACCESS_RECORD_COLUMNS": "colunas de platform_access_tokens",
    "_CREDENTIAL_COLUMNS": "colunas de tenant_api_credentials",
    "_WORKFLOW_COLUMNS": "colunas de automation_workflows",
    "_DRAFT_COLUMNS": "colunas de automation_workflow_drafts",
    "_DEPLOYMENT_COLUMNS": "colunas de automation_deployments",
    "_EXECUTION_COLUMNS": "colunas de automation_executions",
    "_JOB_COLUMNS": "colunas de automation_jobs",
    "_GATE_COLUMNS": "colunas de automation_approval_gates",
    "_EFFECT_COLUMNS": "colunas de automation_effects",
}

# --- exceção 5: identificadores do claim de fila --------------------------------------
#
# `automation_repository._claim_one` interpola o nome da tabela e o da coluna de
# prontidão porque não existe placeholder SQL para identificador, e a consulta
# precisa de `FOR UPDATE SKIP LOCKED` sobre a tabela certa.
#
# A garantia é de origem, não de escape: os dois parâmetros são keyword-only e
# `test_claim_one_is_only_called_with_literal_identifiers` prova que **todos**
# os chamadores passam literais deste conjunto fechado. Nenhum valor vem de
# requisição, de configuração ou de banco.
CLAIM_IDENTIFIER_PARAMETERS: dict[str, str] = {
    "table": "nome de tabela literal, restrito a automation_jobs/automation_outbox",
    "ready_column": "coluna de prontidão literal, restrita a run_at/available_at",
}

ALLOWED_CLAIM_TABLES = frozenset({"automation_jobs", "automation_outbox"})
ALLOWED_CLAIM_READY_COLUMNS = frozenset({"run_at", "available_at"})

# --- exceção 2: fragmentos estruturais construídos em tempo de execução --------------
#
# A chave é o nome da variável; o valor descreve por que ela não pode conter
# dado do chamador. `EXPECTED_FRAGMENT_SOURCES` fixa a construção exata: se
# alguém alterar a forma como o fragmento é montado, o teste falha.
ALLOWED_SQL_FRAGMENTS: dict[str, str] = {
    "where_clause": "concatena apenas condições literais; valores vão em `?`",
    "entry_where": "concatena apenas condições literais; valores vão em `?`",
    "offer_where": "concatena apenas condições literais; valores vão em `?`",
    "set_clause": (
        "nomes de coluna vêm de `updates`, que é filtrado contra o conjunto "
        "literal `allowed` em update_billing_subscription; valores vão em `?`"
    ),
    "day_extract": "expressão de data escolhida por backend, entre dois literais",
    "metric_filter": "literal vazio ou ' AND metric = ?'",
    "base_filter": "literal vazio ou ' WHERE company_id = ?'; o valor vai em `?`",
    "month_extract": "expressão de mês escolhida por backend, entre dois literais",
    "order_clause": "cláusula ORDER BY vinda do mapa literal TABLE_ORDER_BY",
    "columns_sql": "colunas vindas do catálogo literal TABLE_COLUMNS",
    "id_column": "coluna de id vinda do catálogo literal TABLE_COLUMNS",
    "placeholders": (
        "gera apenas placeholders (`?` no runtime, `%s` na migração), um por "
        "item; os valores vão na tupla de parâmetros"
    ),
    "profile_columns": "literal de nomes de coluna, definido na própria função",
    "profile_select": "SELECT literal, definido na própria função",
}

EXPECTED_FRAGMENT_SOURCES: dict[str, set[str]] = {
    "where_clause": {
        "f' WHERE {' AND '.join(conditions)}' if conditions else ''",
        "f' WHERE {' AND '.join(conditions)}'",
        "f' AND {' AND '.join(conditions)}' if conditions else ''",
        # get_finetuning_job_records: `placeholders` é uma sequência de `?`
        # derivada do tamanho do conjunto autorizado; os company_id seguem
        # como parâmetros. Coberto por
        # test_placeholders_are_only_question_marks.
        "f'WHERE company_id IN ({placeholders})'",
        # get_companies (T-09): mesma forma, sobre a PK. `placeholders` é uma
        # sequência de `?` derivada do tamanho do conjunto autorizado; os ids
        # seguem como parâmetros. Coberto por
        # test_placeholders_are_only_question_marks.
        "f'WHERE id IN ({placeholders})'",
    },
    "entry_where": {"f' WHERE {' AND '.join(entry_conditions)}' if entry_conditions else ''"},
    "offer_where": {"f' WHERE {' AND '.join(offer_conditions)}' if offer_conditions else ''"},
    "set_clause": {"', '.join((f'{k} = ?' for k in updates))"},
    "day_extract": {
        '"TO_CHAR(recorded_at, \'YYYY-MM-DD\')"',
        '"strftime(\'%Y-%m-%d\', recorded_at)"',
    },
    "metric_filter": {"''", "' AND metric = ?'"},
    "base_filter": {"'' if company_id is None else ' WHERE company_id = ?'"},
    "month_extract": {
        '"TO_CHAR(starts_at, \'YYYY-MM\')"',
        '"strftime(\'%Y-%m\', starts_at)"',
    },
    "placeholders": {
        "', '.join(('?' for _ in document_types))",
        "', '.join(('?' for _ in company_ids))",
        # migrate_sqlite_to_postgres usa o placeholder do psycopg (%s).
        "', '.join(['%s'] * len(columns))",
    },
    "order_clause": {"f' ORDER BY {order_by}' if order_by else ''"},
}

# --- exceção 4: construtores de DDL das migrations -----------------------------------
#
# `schema_utils` monta DDL a partir de identificadores recebidos por parâmetro.
# Não há placeholder possível para nome de tabela/coluna em SQL, então a
# garantia é a origem: `test_ddl_builders_only_receive_literals` prova que todo
# chamador passa literal ou constante de módulo.
ALLOWED_CATALOG_BOUND: dict[str, str] = {
    "table_name": (
        "vem do catálogo literal BACKUP_TABLES e é revalidado contra "
        "BACKUP_TABLE_SET antes do uso"
    ),
    "columns_sql": "colunas do catálogo literal TABLE_COLUMNS",
    "id_column": "coluna de id do catálogo literal TABLE_COLUMNS",
}

ALLOWED_DDL_PARAMETERS: dict[str, str] = {
    "table": "nome de tabela vindo das migrations, sempre literal",
    "normalized": "colunas traduzidas a partir de `columns_sql` literal",
    "suffix": "cláusula extra literal das migrations",
    "index_name": "nome de índice literal das migrations",
    "columns": "lista de colunas literal das migrations",
    "column": "nome de coluna literal das migrations",
    "definition": "definição de coluna literal das migrations",
}

# Módulos cujos identificadores SQL vêm de um catálogo literal do próprio
# módulo. `test_literal_catalog_modules_are_cli_only` prova que nenhum deles é
# alcançável a partir da superfície HTTP.
LITERAL_CATALOG_MODULES: dict[str, str] = {
    "schema_utils.py": "identificadores vêm das migrations, sempre literais",
    "migrate_sqlite_to_postgres.py": "identificadores vêm do catálogo TABLE_COLUMNS",
}

LITERAL_CATALOGS: dict[str, str] = {
    "app/tools/migrate_sqlite_to_postgres.py": "TABLE_COLUMNS",
    "app/tools/export_runtime_backup.py": "BACKUP_TABLES",
    "app/tools/export_runtime_backup.py::order": "TABLE_ORDER_BY",
}

DDL_BUILDERS = (
    "create_table",
    "create_index",
    "create_unique_index",
    "ensure_column",
)

# --- exceção 3: expressões inline -----------------------------------------------------
ALLOWED_INLINE_EXPRESSIONS: dict[str, str] = {
    "' AND '.join(conditions)": "condições literais montadas no próprio módulo",
    "' AND '.join(cancel_conditions)": "condições literais montadas no próprio módulo",
    "', '.join(selected_columns)": (
        "colunas do catálogo literal TABLE_COLUMNS, eventualmente prefixadas "
        "por 'NULL AS' para colunas ausentes no SQLite de origem"
    ),
}

_SQL_VERBS = ("SELECT ", "INSERT INTO", "UPDATE ", "DELETE FROM", "CREATE TABLE")
_SQL_CLAUSES = ("FROM ", "INTO ", " SET ", "TABLE ", "VALUES", "WHERE ")


def _python_files() -> list[pathlib.Path]:
    files: list[pathlib.Path] = []
    for directory in SCANNED_DIRS:
        files.extend(sorted(directory.rglob("*.py")))
    return files


def _literal_text(node: ast.JoinedStr) -> str:
    raw = "".join(part.value for part in node.values if isinstance(part, ast.Constant))
    # SQL multilinha quebra depois de SELECT; normalizar espaço evita que o
    # detector deixe de reconhecer a query.
    return re.sub(r"\s+", " ", raw)


def _is_sql(node: ast.JoinedStr) -> bool:
    """Distingue SQL real de mensagem que apenas cita uma palavra SQL."""
    literal = _literal_text(node).upper()
    has_verb = any(verb in literal for verb in _SQL_VERBS)
    has_clause = any(clause in literal for clause in _SQL_CLAUSES)
    return has_verb and has_clause


def _interpolations(node: ast.JoinedStr) -> list[ast.expr]:
    return [part.value for part in node.values if isinstance(part, ast.FormattedValue)]


def _is_condition_fragment(node: ast.Call) -> bool:
    """f-string anexada a uma lista de condições vira SQL depois do join.

    Sem isto, `conditions.append(f"x IN ({placeholders})")` escaparia do guard
    mesmo compondo a cláusula WHERE final.
    """
    if not isinstance(node.func, ast.Attribute) or node.func.attr != "append":
        return False
    target = node.func.value
    if not isinstance(target, ast.Name):
        return False
    return target.id == "conditions" or target.id.endswith("_conditions")


def _sql_fstrings() -> list[tuple[pathlib.Path, ast.JoinedStr]]:
    found: list[tuple[pathlib.Path, ast.JoinedStr]] = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.JoinedStr) and _is_sql(node):
                found.append((path, node))
                continue
            if isinstance(node, ast.Call) and _is_condition_fragment(node):
                for argument in node.args:
                    if isinstance(argument, ast.JoinedStr):
                        found.append((path, argument))
    return found


def test_sql_fstrings_only_interpolate_justified_expressions() -> None:
    violations: list[str] = []

    for path, node in _sql_fstrings():
        for expression in _interpolations(node):
            if isinstance(expression, ast.Name):
                if expression.id in ALLOWED_COLUMN_CONSTANTS:
                    continue
                if expression.id in ALLOWED_SQL_FRAGMENTS:
                    continue
                if expression.id in ALLOWED_CATALOG_BOUND:
                    continue
                if (
                    path.name in LITERAL_CATALOG_MODULES
                    and expression.id in ALLOWED_DDL_PARAMETERS
                ):
                    continue
                if (
                    path.name == "automation_repository.py"
                    and expression.id in CLAIM_IDENTIFIER_PARAMETERS
                ):
                    continue
                violations.append(
                    f"{path}:{expression.lineno} interpola o nome '{expression.id}', "
                    "que não está justificado"
                )
                continue

            rendered = ast.unparse(expression)
            if rendered in ALLOWED_INLINE_EXPRESSIONS:
                continue
            violations.append(
                f"{path}:{getattr(expression, 'lineno', '?')} interpola a expressão "
                f"não justificada: {rendered}"
            )

    assert violations == [], (
        "SQL interpolado com expressão não justificada. Use placeholders `?` para "
        "valores, ou registre a exceção neste teste com justificativa:\n"
        + "\n".join(violations)
    )


def test_claim_one_is_only_called_with_literal_identifiers() -> None:
    """Prova de origem dos identificadores do claim de fila.

    `_claim_one` interpola `table` e `ready_column`. Este teste percorre a AST
    de todo o pacote e exige que cada chamada passe uma **constante literal**
    pertencente ao conjunto fechado. Uma chamada nova com valor dinâmico —
    vindo de query string, de config ou de banco — falha aqui, e é por isso
    que a exceção ao B608 se sustenta.
    """
    calls_found = 0
    violations: list[str] = []

    for path in sorted(pathlib.Path("app").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else func.attr
                if isinstance(func, ast.Attribute)
                else ""
            )
            if name != "_claim_one":
                continue
            calls_found += 1

            if node.args:
                violations.append(
                    f"{path}:{node.lineno} chama _claim_one posicionalmente; "
                    "os identificadores precisam ser keyword-only"
                )

            supplied: dict[str, ast.expr] = {
                keyword.arg: keyword.value for keyword in node.keywords if keyword.arg
            }
            for parameter, allowed in (
                ("table", ALLOWED_CLAIM_TABLES),
                ("ready_column", ALLOWED_CLAIM_READY_COLUMNS),
            ):
                value = supplied.get(parameter)
                if value is None:
                    violations.append(
                        f"{path}:{node.lineno} chama _claim_one sem '{parameter}'"
                    )
                    continue
                if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
                    violations.append(
                        f"{path}:{node.lineno} passa '{parameter}' não literal: "
                        f"{ast.unparse(value)}"
                    )
                    continue
                if value.value not in allowed:
                    violations.append(
                        f"{path}:{node.lineno} passa '{parameter}'='{value.value}', "
                        f"fora do conjunto permitido {sorted(allowed)}"
                    )

    assert violations == [], "\n".join(violations)
    # Se o claim deixar de existir, a exceção correspondente precisa sair da
    # allowlist junto — o teste não pode passar por vacuidade.
    assert calls_found >= 2, (
        f"_claim_one deveria ter pelo menos 2 chamadores (jobs e outbox); "
        f"encontrados {calls_found}"
    )


def test_column_constants_contain_only_column_names() -> None:
    column_pattern = re.compile(
        r"^[A-Za-z_][A-Za-z0-9_]*(\s+AS\s+[A-Za-z_][A-Za-z0-9_]*)?$",
        re.IGNORECASE,
    )
    checked: set[str] = set()

    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if not isinstance(target, ast.Name):
                    continue
                if target.id not in ALLOWED_COLUMN_CONSTANTS:
                    continue

                assert isinstance(node.value, ast.Constant) and isinstance(
                    node.value.value, str
                ), f"{path}:{node.lineno} — {target.id} precisa ser um literal de string"

                for column in node.value.value.replace("\n", " ").split(","):
                    normalized = column.strip().split(".")[-1]
                    if not normalized:
                        continue
                    assert column_pattern.match(normalized), (
                        f"{path}:{node.lineno} — {target.id} contém algo que não é "
                        f"nome de coluna: {normalized!r}"
                    )
                checked.add(target.id)

    missing = sorted(set(ALLOWED_COLUMN_CONSTANTS) - checked)
    assert missing == [], f"constantes declaradas mas não encontradas no código: {missing}"


def test_sql_fragments_are_built_exactly_as_registered() -> None:
    """Fixa a construção de cada fragmento estrutural.

    É isso que impede alguém de trocar `' AND '.join(conditions)` por algo que
    concatene entrada do usuário sem o teste perceber.
    """
    observed: dict[str, set[str]] = {}

    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            # Atribuição de literal puro não precisa de pin: não há
            # interpolação possível.
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in EXPECTED_FRAGMENT_SOURCES:
                    observed.setdefault(target.id, set()).add(ast.unparse(node.value))

    for name, expected in EXPECTED_FRAGMENT_SOURCES.items():
        actual = observed.get(name, set())
        unexpected = actual - expected
        assert unexpected == set(), (
            f"fragmento '{name}' passou a ser construído de forma não registrada: "
            f"{sorted(unexpected)}. Revise a segurança e atualize "
            "EXPECTED_FRAGMENT_SOURCES."
        )


def test_set_clause_column_names_come_from_a_literal_allowlist() -> None:
    """`set_clause` é o único fragmento que interpola nome de coluna dinâmico.

    A segurança dele depende de `updates` ser filtrado contra um conjunto
    literal. Este teste falha se esse filtro sumir.
    """
    source = pathlib.Path("app/storage/repository.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    function = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "update_billing_subscription"
    )
    body = ast.unparse(function)

    assert "allowed = {" in body, "o conjunto literal de colunas permitidas sumiu"
    assert "if k in allowed" in body, "o filtro contra a allowlist sumiu"
    # E o conjunto precisa ser realmente literal.
    allowed_assign = next(
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "allowed" for t in node.targets)
    )
    assert isinstance(allowed_assign.value, ast.Set)
    assert all(
        isinstance(element, ast.Constant) and isinstance(element.value, str)
        for element in allowed_assign.value.elts
    ), "a allowlist de colunas precisa conter apenas literais"


def test_ddl_builders_only_receive_literals() -> None:
    """Prova a origem dos identificadores usados no DDL.

    Nome de tabela e de coluna não podem ir em placeholder, então a segurança
    depende de nunca virem do chamador HTTP. Este teste percorre todas as
    chamadas aos construtores de DDL e falha se algum argumento não for um
    literal de string ou uma constante de módulo em MAIÚSCULAS.
    """
    violations: list[str] = []

    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        module_constants = {
            target.id
            for node in tree.body
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name) and target.id.isupper()
        }

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function_name = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else getattr(node.func, "attr", "")
            )
            if function_name not in DDL_BUILDERS:
                continue

            # O primeiro argumento é sempre o cursor; os demais são identificadores.
            for argument in list(node.args)[1:] + [kw.value for kw in node.keywords]:
                if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                    continue
                if isinstance(argument, ast.Name) and argument.id in module_constants:
                    continue
                if isinstance(argument, ast.JoinedStr):
                    # f-string só é aceita se interpolar apenas constantes de módulo.
                    interpolated = _interpolations(argument)
                    if all(
                        isinstance(part, ast.Name) and part.id in module_constants
                        for part in interpolated
                    ):
                        continue
                violations.append(
                    f"{path}:{node.lineno} chama {function_name} com argumento "
                    f"não literal: {ast.unparse(argument)}"
                )

    assert violations == [], (
        "Identificador de DDL veio de expressão não literal. Nome de tabela e "
        "coluna precisam ser literais das migrations:\n" + "\n".join(violations)
    )


def test_literal_catalogs_contain_only_string_literals() -> None:
    """Os catálogos que alimentam o DDL das ferramentas são 100% literais."""
    for catalog_key, catalog_name in LITERAL_CATALOGS.items():
        module_path = catalog_key.split("::")[0]
        tree = ast.parse(pathlib.Path(module_path).read_text(encoding="utf-8"))
        assignment = next(
            (
                node
                for node in tree.body
                if isinstance(node, (ast.Assign, ast.AnnAssign))
                and any(
                    isinstance(target, ast.Name) and target.id == catalog_name
                    for target in (
                        node.targets if isinstance(node, ast.Assign) else [node.target]
                    )
                )
            ),
            None,
        )
        assert assignment is not None, f"{catalog_name} não encontrado em {module_path}"
        assert assignment.value is not None

        for literal in ast.walk(assignment.value):
            if isinstance(literal, ast.Constant):
                assert isinstance(literal.value, (str, type(None))), (
                    f"{module_path}: {catalog_name} contém valor não textual"
                )
            elif isinstance(literal, ast.Name):
                raise AssertionError(
                    f"{module_path}: {catalog_name} referencia a variável "
                    f"'{literal.id}' em vez de um literal"
                )


def test_literal_catalog_modules_are_cli_only() -> None:
    """Nenhum módulo de catálogo literal é alcançável pela superfície HTTP.

    É isso que garante que `table` e `columns` jamais recebem dado de request.
    """
    http_surfaces = list(pathlib.Path("app/api").rglob("*.py")) + list(
        pathlib.Path("app/services").rglob("*.py")
    )
    forbidden = {
        pathlib.Path(module.split("::")[0]).stem
        for module in LITERAL_CATALOGS
        if pathlib.Path(module.split("::")[0]).parent.name == "tools"
    }

    offenders: list[str] = []
    for path in http_surfaces:
        source = path.read_text(encoding="utf-8")
        for module_name in forbidden:
            if f"import {module_name}" in source or f"from app.tools.{module_name}" in source:
                offenders.append(f"{path} importa {module_name}")

    assert offenders == [], (
        "ferramenta de CLI passou a ser alcançável pela API; a justificativa "
        "de 'catálogo literal' deixa de valer:\n" + "\n".join(offenders)
    )


def _literal_only_fragment_names() -> set[str]:
    """Fragmentos cujas atribuições são todas literais de string."""
    assignments: dict[str, list[ast.expr]] = {}
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in ALLOWED_SQL_FRAGMENTS:
                    assignments.setdefault(target.id, []).append(node.value)

    return {
        name
        for name, values in assignments.items()
        if values
        and all(
            isinstance(value, ast.Constant) and isinstance(value.value, str)
            for value in values
        )
    }


def test_every_fragment_has_a_pinned_construction() -> None:
    """Nenhum fragmento pode estar justificado sem ter a construção fixada."""
    unpinned = sorted(set(ALLOWED_SQL_FRAGMENTS) - set(EXPECTED_FRAGMENT_SOURCES))
    # Parâmetros de DDL não têm atribuição para fixar: a garantia deles é a
    # origem literal, provada em test_ddl_builders_only_receive_literals e em
    # test_literal_catalogs_contain_only_string_literals.
    literal_only = _literal_only_fragment_names()
    unpinned = [
        name
        for name in unpinned
        if name not in ALLOWED_DDL_PARAMETERS
        and name not in ALLOWED_CATALOG_BOUND
        and name not in literal_only
    ]
    assert unpinned == [], f"fragmentos sem construção fixada: {unpinned}"


def test_allowlists_have_no_dead_entries() -> None:
    used_names: set[str] = set()
    used_expressions: set[str] = set()

    for _path, node in _sql_fstrings():
        for expression in _interpolations(node):
            if isinstance(expression, ast.Name):
                used_names.add(expression.id)
            else:
                used_expressions.add(ast.unparse(expression))

    dead_columns = sorted(set(ALLOWED_COLUMN_CONSTANTS) - used_names)
    dead_fragments = sorted(
        (set(ALLOWED_SQL_FRAGMENTS) | set(ALLOWED_CATALOG_BOUND)) - used_names
    )
    dead_inline = sorted(set(ALLOWED_INLINE_EXPRESSIONS) - used_expressions)

    assert dead_columns == [], f"constantes justificadas sem uso: {dead_columns}"
    assert dead_fragments == [], f"fragmentos justificados sem uso: {dead_fragments}"
    assert dead_inline == [], f"expressões justificadas sem uso: {dead_inline}"


def test_every_exception_carries_a_justification() -> None:
    for allowlist in (
        ALLOWED_COLUMN_CONSTANTS,
        ALLOWED_SQL_FRAGMENTS,
        ALLOWED_CATALOG_BOUND,
        ALLOWED_DDL_PARAMETERS,
        ALLOWED_INLINE_EXPRESSIONS,
    ):
        for key, justification in allowlist.items():
            assert justification.strip(), f"'{key}' está na allowlist sem justificativa"


def test_the_guard_actually_detects_a_violation(tmp_path: pathlib.Path, monkeypatch) -> None:
    """Prova que o guard não passa por vacuidade."""
    unsafe = tmp_path / "unsafe_repository.py"
    unsafe.write_text(
        "def query(user_input):\n"
        "    return f'SELECT * FROM contacts WHERE name = {user_input}'\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "tests.test_sql_interpolation_guard.SCANNED_DIRS", (tmp_path,), raising=False
    )

    with __import__("pytest").raises(AssertionError, match="não justificada"):
        test_sql_fstrings_only_interpolate_justified_expressions()
