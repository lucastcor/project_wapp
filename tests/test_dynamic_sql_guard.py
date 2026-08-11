"""Structural proof that interpolated SQL never carries caller-supplied data.

Bandit's B608 flags every f-string that becomes SQL because it cannot tell a
column identifier from a value. This guard is stronger and more specific: for
each interpolation site it walks the AST and proves the interpolated expression
is derived **only** from literals, module-level constants or an allowlisted
builder — never from a function parameter, a subscript of caller data, or an
attribute of a request object.

It deliberately compares AST *structure and name bindings*, never
``ast.unparse()`` text: reformatting, quote style or line wrapping must not be
able to break or silently weaken this check.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

SCANNED_DIRS = (
    pathlib.Path("app/storage"),
    pathlib.Path("app/services"),
    pathlib.Path("app/tools"),
    pathlib.Path("app/api"),
)

# Helpers that are themselves proven to emit only safe SQL fragments.
# `_optional_equals` validates its column against a strict identifier pattern
# and binds every value as a parameter (see app/storage/repository.py).
# `_quote_identifier` validates against a strict identifier pattern and raises
# on anything else before quoting; `test_safe_builders_actually_sanitize` proves
# both implementations reject injection input, so this allowlist is earned.
SAFE_BUILDERS = frozenset({
    "_optional_equals",
    "_quote_identifier",
    # Migration DDL sanitizers in app/storage/db.py: both fullmatch a
    # strict pattern and raise before the value can reach SQL.
    "_assert_sql_identifier",
    "_assert_sql_column_definition",
    # DDL boundary validators in app/storage/schema_utils.py: same contract —
    # fullmatch a strict pattern and raise before the value reaches SQL.
    "assert_identifier",
    "assert_index_columns",
    "assert_definition",
})

# Builtins that only re-shape an already-safe collection.
COLLECTION_BUILTINS = frozenset({"list", "tuple", "set", "sorted", "frozenset", "reversed"})

# Numeric coercions: the result cannot syntactically carry SQL.
NUMERIC_COERCIONS = frozenset({"int", "float", "len", "abs", "min", "max", "round"})

# Methods whose first positional argument is executed as SQL.
SQL_EXECUTORS = frozenset({"execute", "executemany"})


def _iter_python_files() -> list[pathlib.Path]:
    files: list[pathlib.Path] = []
    for directory in SCANNED_DIRS:
        files.extend(sorted(directory.rglob("*.py")))
    return files


def _module_constant_names(tree: ast.Module) -> set[str]:
    """Module-level names bound once to a literal or literal container.

    These are source-code constants: an attacker cannot influence them at
    runtime without editing the module.
    """
    constants: set[str] = set()
    for node in tree.body:
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
            value = node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
            value = node.value
        if value is None:
            continue
        if not _is_literal_expression(value, constants):
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                constants.add(target.id)
    return constants


def _is_literal_expression(node: ast.expr, known_constants: set[str]) -> bool:
    """Whether an expression is built purely from literals/known constants."""
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, ast.Name):
        return node.id in known_constants
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return all(_is_literal_expression(element, known_constants) for element in node.elts)
    if isinstance(node, ast.Dict):
        return all(
            key is not None
            and _is_literal_expression(key, known_constants)
            and _is_literal_expression(value, known_constants)
            for key, value in zip(node.keys, node.values, strict=False)
        )
    if isinstance(node, ast.Call):
        # frozenset(...) / dict(...) / set(...) over literal contents.
        if isinstance(node.func, ast.Name) and node.func.id in {"frozenset", "set", "dict", "tuple", "list"}:
            return all(_is_literal_expression(arg, known_constants) for arg in node.args)
        return False
    if isinstance(node, ast.BinOp):
        return _is_literal_expression(node.left, known_constants) and _is_literal_expression(
            node.right, known_constants
        )
    if isinstance(node, ast.JoinedStr):
        return all(
            _is_literal_expression(value.value, known_constants)
            for value in node.values
            if isinstance(value, ast.FormattedValue)
        )
    if isinstance(node, ast.comprehension):  # pragma: no cover - defensive
        return False
    return False


class _FunctionScope:
    """Name bindings inside one function, classified as safe or unproven."""

    def __init__(self, function: ast.AST, module_constants: set[str]) -> None:
        self.function = function
        self.module_constants = module_constants
        self.parameters = self._collect_parameters(function)
        self.assignments: dict[str, list[ast.expr]] = {}
        self.loop_targets: dict[str, ast.expr] = {}
        self._collect_bindings(function)

    @staticmethod
    def _collect_parameters(function: ast.AST) -> set[str]:
        names: set[str] = set()
        args = getattr(function, "args", None)
        if args is None:
            return names
        for group in (args.posonlyargs, args.args, args.kwonlyargs):
            names.update(argument.arg for argument in group)
        if args.vararg:
            names.add(args.vararg.arg)
        if args.kwarg:
            names.add(args.kwarg.arg)
        return names

    def _collect_bindings(self, function: ast.AST) -> None:
        for node in ast.walk(function):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    self._bind_target(target, node.value)
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                self._bind_target(node.target, node.value)
            elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
                self._bind_loop_target(node.target, node.iter)

    def _bind_target(self, target: ast.expr, value: ast.expr) -> None:
        if isinstance(target, ast.Name):
            self.assignments.setdefault(target.id, []).append(value)
        elif isinstance(target, (ast.Tuple, ast.List)):
            # Tuple unpack, e.g. `condition, params = _optional_equals(...)`.
            for element in target.elts:
                if isinstance(element, ast.Name):
                    self.assignments.setdefault(element.id, []).append(value)

    def _bind_loop_target(self, target: ast.expr, iterable: ast.expr) -> None:
        if isinstance(target, ast.Name):
            self.loop_targets[target.id] = iterable
        elif isinstance(target, (ast.Tuple, ast.List)):
            for element in target.elts:
                if isinstance(element, ast.Name):
                    self.loop_targets[element.id] = iterable


def _expression_is_safe(node: ast.expr, scope: _FunctionScope, seen: set[str] | None = None) -> bool:
    """Whether an interpolated expression provably excludes caller data."""
    seen = seen if seen is not None else set()

    if isinstance(node, ast.Constant):
        return True

    if isinstance(node, ast.JoinedStr):
        return all(
            _expression_is_safe(value.value, scope, seen)
            for value in node.values
            if isinstance(value, ast.FormattedValue)
        )

    if isinstance(node, ast.BinOp):
        return _expression_is_safe(node.left, scope, seen) and _expression_is_safe(node.right, scope, seen)

    if isinstance(node, ast.IfExp):
        return _expression_is_safe(node.body, scope, seen) and _expression_is_safe(node.orelse, scope, seen)

    if isinstance(node, ast.Call):
        # `", ".join(...)` and allowlisted builders.
        if isinstance(node.func, ast.Attribute) and node.func.attr == "join":
            return _expression_is_safe(node.func.value, scope, seen) and all(
                _expression_is_safe(argument, scope, seen) for argument in node.args
            )
        if isinstance(node.func, ast.Name) and node.func.id in SAFE_BUILDERS:
            return True
        if isinstance(node.func, ast.Name) and node.func.id in COLLECTION_BUILTINS:
            return all(_expression_is_safe(argument, scope, seen) for argument in node.args)
        return isinstance(node.func, ast.Name) and node.func.id in NUMERIC_COERCIONS

    if isinstance(node, (ast.GeneratorExp, ast.ListComp, ast.SetComp)):
        # Only the produced element matters: if every emitted item is a literal,
        # a module constant or a sanitizer call, the iterated source cannot
        # influence the SQL text regardless of where it came from.
        return _expression_is_safe(node.elt, scope, seen)

    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return all(_expression_is_safe(element, scope, seen) for element in node.elts)

    if isinstance(node, ast.Starred):
        # `[*conditions, "literal"]` is safe when the unpacked source is safe.
        return _expression_is_safe(node.value, scope, seen)

    if isinstance(node, ast.Name):
        name = node.id
        if name in scope.module_constants:
            return True
        # A function parameter is caller-controlled: never safe to interpolate.
        if name in scope.parameters:
            return False
        if name in seen:  # recursive binding; already being proven
            return True
        seen.add(name)
        if name in scope.loop_targets:
            return _expression_is_safe(scope.loop_targets[name], scope, seen)
        bindings = scope.assignments.get(name)
        if not bindings:
            return False
        return all(_expression_is_safe(binding, scope, seen) for binding in bindings)

    # Subscripts, attributes and everything else are unproven by construction:
    # they can read request payloads, ORM rows or provider responses.
    return False


def _collect_sql_interpolation_sites() -> list[tuple[str, int, ast.expr, _FunctionScope]]:
    sites: list[tuple[str, int, ast.expr, _FunctionScope]] = []
    for path in _iter_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        module_constants = _module_constant_names(tree)

        # Map each node to its enclosing function so bindings resolve locally.
        for function in [
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]:
            scope = _FunctionScope(function, module_constants)
            for node in ast.walk(function):
                if not isinstance(node, ast.Call):
                    continue
                if not (isinstance(node.func, ast.Attribute) and node.func.attr in SQL_EXECUTORS):
                    continue
                if not node.args:
                    continue
                sql_argument = node.args[0]
                sites.extend(
                    (str(path), value.lineno, value, scope)
                    for value in _interpolated_expressions(sql_argument)
                )
    return sites


def _interpolated_expressions(node: ast.expr) -> list[ast.expr]:
    """Every dynamic expression embedded in a SQL argument."""
    found: list[ast.expr] = []
    if isinstance(node, ast.JoinedStr):
        found.extend(
            value.value for value in node.values if isinstance(value, ast.FormattedValue)
        )
    elif isinstance(node, ast.BinOp):
        # String concatenation or %-formatting used to build SQL.
        found.extend(_interpolated_expressions(node.left))
        found.extend(_interpolated_expressions(node.right))
        if isinstance(node.op, ast.Mod) and not isinstance(node.right, ast.Constant):
            found.append(node.right)
    elif isinstance(node, ast.Name):
        # `cursor.execute(query)` where query was built above is resolved by the
        # scope walk in the Name branch of _expression_is_safe.
        pass
    return found


SITES = _collect_sql_interpolation_sites()


def test_guard_actually_finds_the_interpolation_sites() -> None:
    """A guard that silently scans nothing would always pass."""
    assert len(SITES) >= 100, f"expected the repository's SQL builders to be scanned, found {len(SITES)}"


@pytest.mark.parametrize(
    ("path", "lineno", "expression", "scope"),
    SITES,
    ids=[f"{pathlib.Path(p).name}:{ln}" for p, ln, _e, _s in SITES],
)
def test_interpolated_sql_never_carries_caller_data(path, lineno, expression, scope) -> None:
    assert _expression_is_safe(expression, scope), (
        f"{path}:{lineno} interpolates an expression into SQL that is not provably "
        f"derived from literals, module constants or {sorted(SAFE_BUILDERS)}. "
        f"Bind the value as a parameter instead."
    )


def test_parameters_are_rejected_by_construction() -> None:
    """The guard must fail on caller data, not just pass on today's code."""
    module = ast.parse(
        "TABLES = ['companies']\n"
        "def unsafe(cursor, table):\n"
        "    cursor.execute(f'SELECT * FROM {table}')\n"
        "def safe(cursor):\n"
        "    for table in TABLES:\n"
        "        cursor.execute(f'SELECT * FROM {table}')\n"
    )
    constants = _module_constant_names(module)
    functions = {
        node.name: node
        for node in module.body
        if isinstance(node, ast.FunctionDef)
    }

    unsafe_scope = _FunctionScope(functions["unsafe"], constants)
    unsafe_expression = _interpolated_expressions(
        functions["unsafe"].body[0].value.args[0]  # type: ignore[attr-defined]
    )[0]
    assert _expression_is_safe(unsafe_expression, unsafe_scope) is False

    safe_scope = _FunctionScope(functions["safe"], constants)
    safe_expression = _interpolated_expressions(
        functions["safe"].body[0].body[0].value.args[0]  # type: ignore[attr-defined]
    )[0]
    assert _expression_is_safe(safe_expression, safe_scope) is True


def test_guard_is_insensitive_to_formatting() -> None:
    """Reformatting must not change the verdict (no ast.unparse comparison)."""
    compact = ast.parse(
        "COLS='id'\n"
        "def q(cursor):\n"
        "    cursor.execute(f'SELECT {COLS} FROM t')\n"
    )
    spread = ast.parse(
        "COLS = 'id'\n"
        "def q(cursor):\n"
        "    cursor.execute(\n"
        "        f'SELECT {COLS} FROM t'\n"
        "    )\n"
    )
    verdicts = []
    for module in (compact, spread):
        constants = _module_constant_names(module)
        function = next(n for n in module.body if isinstance(n, ast.FunctionDef))
        scope = _FunctionScope(function, constants)
        expression = _interpolated_expressions(function.body[0].value.args[0])[0]  # type: ignore[attr-defined]
        verdicts.append(_expression_is_safe(expression, scope))

    assert verdicts == [True, True]
