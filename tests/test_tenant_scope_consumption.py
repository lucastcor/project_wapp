"""Defesa **complementar**: a forma do handler não contradiz o escopo autorizado.

Leia primeiro o que este arquivo *não* é. Ele não prova que a rota é segura.
Uma AST não acompanha um valor até o SQL, e afirmar que acompanha foi
exatamente o erro da rodada anterior: a versão original aceitava

    def cancel(booking_id: int, company_id: int = Depends(require_authorized_company_id)):
        return cancel_calendar_booking(booking_id=booking_id)   # escopo ignorado

que é o T-03 verbatim. O guarda construído para impedir a reincidência não a
impedia, porque ele parava em "a dependência está declarada?".

A garantia de verdade é comportamental e mora em
`tests/test_cross_tenant_resource_by_id.py`: dez cenários por rota, contra o
banco, verificando o **estado persistido**. Este arquivo é a segunda linha —
ele captura o que uma AST realmente consegue provar, que é um conjunto pequeno
e honesto de proposições **negativas**:

  * o parâmetro de escopo não é lido em lugar nenhum  → certamente ignorado;
  * o parâmetro de escopo é reatribuído no corpo      → o valor autorizado foi
    descartado antes de ser usado;
  * a empresa auditada vem do registro carregado      → autorização invertida.

Cada uma dessas é decidível estaticamente e, quando dispara, é sempre um
defeito. O caminho inverso — "consome, logo está correto" — não é prova, e o
teste não o afirma em lugar nenhum.

Os controles negativos abaixo existem porque um detector que não encontra nada
passa por vacuidade. Cada escape que a revisão independente demonstrou virou um
caso de teste que o detector precisa reprovar.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from app.api.company_scope import POLICY_TENANT, describe_route_policy, resolve_route_policy
from app.main import app

MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
ALL_ROUTE_METHODS = MUTATING_METHODS | {"GET"}

# Dependências que entregam o escopo já autorizado.
SCOPE_DEPENDENCIES = frozenset({"require_authorized_company_id", "require_company_filter"})

# Chamadas que resolvem o escopo dentro do corpo, para handlers que não usam
# parâmetro injetado.
SCOPE_CALLS = frozenset(
    {
        "require_authorized_company_id",
        "require_company_filter",
        "authorize_company",
        "_require_company",
        "_resolve_company_scope",
        "get_authorized_company_id",
        "get_authorized_scope",
    }
)

ROUTE_MODULES = (
    pathlib.Path("app/api/admin_routes.py"),
    pathlib.Path("app/api/platform_admin_routes.py"),
    pathlib.Path("app/api/automation_routes.py"),
)

FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef

T03_ROUTES = frozenset(
    {
        "/admin/calendar/bookings/{booking_id}/reschedule",
        "/admin/calendar/bookings/{booking_id}/cancel",
        "/admin/calendar/queue-offers/{offer_id}/respond",
        "/admin/analytics/bookings/{booking_id}/outcome",
        "/admin/analytics/bookings/{booking_id}/cancellation-reason",
    }
)


# --- extração de rotas -------------------------------------------------------------------


def _router_names(tree: ast.Module) -> frozenset[str]:
    """Nomes ligados a `APIRouter(...)` no módulo.

    A versão anterior exigia que o nome terminasse em "router". Um router
    chamado `admin` sumiria da varredura inteira sem nenhum aviso.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        func = node.value.func
        called = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if called != "APIRouter":
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
    return frozenset(names)


def _decorator_routes(node: FunctionNode, routers: frozenset[str]) -> list[tuple[str, str]]:
    """Todos os pares (método, path) dos decoradores de rota do handler.

    Um handler pode ter mais de um decorador. Devolver só o primeiro deixaria
    o resto fora da varredura.
    """
    routes: list[tuple[str, str]] = []
    for decorator in node.decorator_list:
        if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
            continue
        router = decorator.func.value
        if not isinstance(router, ast.Name) or router.id not in routers:
            continue
        method = decorator.func.attr.upper()
        if method not in ALL_ROUTE_METHODS:
            continue
        # `@router.post("/x")` e `@router.post(path="/x")` são a mesma rota.
        raw_path: str | None = None
        if decorator.args and isinstance(decorator.args[0], ast.Constant):
            raw_path = str(decorator.args[0].value)
        else:
            for keyword in decorator.keywords:
                if keyword.arg == "path" and isinstance(keyword.value, ast.Constant):
                    raw_path = str(keyword.value.value)
        if raw_path is not None:
            routes.append((method, raw_path))
    return routes


def _full_path(module: pathlib.Path, raw_path: str) -> str:
    if raw_path.startswith("/admin"):
        return raw_path
    if module.name == "automation_routes.py":
        return f"/admin/automations{raw_path}"
    return f"/admin{raw_path}"


def _route_handlers() -> list[tuple[pathlib.Path, FunctionNode, str, str]]:
    found: list[tuple[pathlib.Path, FunctionNode, str, str]] = []
    for path in ROUTE_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        routers = _router_names(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for method, raw_path in _decorator_routes(node, routers):
                found.append((path, node, method, _full_path(module=path, raw_path=raw_path)))
    return found


def _mutating_tenant_handlers() -> list[tuple[pathlib.Path, FunctionNode, str, str]]:
    return [
        entry
        for entry in _route_handlers()
        if entry[2] in MUTATING_METHODS and resolve_route_policy(entry[3], entry[2]) == POLICY_TENANT
    ]


# --- proposições decidíveis --------------------------------------------------------------


def _scope_parameter_names(node: FunctionNode) -> set[str]:
    """Parâmetros ligados a uma dependência de escopo.

    Cobre `x: T = Depends(dep)` e `x: Annotated[T, Depends(dep)]`.
    """
    names: set[str] = set()
    arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]

    def _binds_scope(expression: ast.expr | None) -> bool:
        if expression is None:
            return False
        for inner in ast.walk(expression):
            if not isinstance(inner, ast.Call) or not isinstance(inner.func, ast.Name):
                continue
            if inner.func.id != "Depends":
                continue
            for argument in inner.args:
                if isinstance(argument, ast.Name) and argument.id in SCOPE_DEPENDENCIES:
                    return True
        return False

    positional = [*node.args.posonlyargs, *node.args.args]
    padded: list[ast.expr | None] = [None] * (len(positional) - len(node.args.defaults))
    for argument, default in zip(positional, [*padded, *node.args.defaults], strict=True):
        if _binds_scope(default) or _binds_scope(argument.annotation):
            names.add(argument.arg)
    for argument, default in zip(node.args.kwonlyargs, node.args.kw_defaults, strict=True):
        if _binds_scope(default) or _binds_scope(argument.annotation):
            names.add(argument.arg)

    for argument in arguments:
        if _binds_scope(argument.annotation):
            names.add(argument.arg)
    return names


# Sinks de observabilidade. Passar o escopo para um deles é correto e desejável,
# mas não é evidência de que o valor chegou ao serviço: um handler que apenas
# *loga* a empresa autorizada e consulta o recurso por id global continua sendo
# o T-03. Auditar e logar são consequência da decisão, não a decisão.
OBSERVABILITY_SINKS = frozenset({"log_event", "_record_audit_event", "print"})


def _is_observability_call(call: ast.Call) -> bool:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id in OBSERVABILITY_SINKS
    if isinstance(func, ast.Attribute):
        # `logger.info(...)`, `logging.getLogger(__name__).warning(...)`
        value = func.value
        if isinstance(value, ast.Name) and value.id in {"logger", "logging", "log"}:
            return True
    return False


def _names_read_inside_calls(node: FunctionNode) -> set[str]:
    """Nomes repassados como argumento a alguma chamada **de domínio** no corpo.

    Ser *lido* em qualquer lugar seria fraco demais, e contar qualquer chamada
    também: `logger.info("cancelled for %s", company_id)` satisfaria a condição
    sem que o valor chegasse ao serviço. Descontados os sinks de observabilidade,
    "aparece como argumento de uma chamada" é a aproximação mais próxima de "foi
    repassado" que uma AST alcança sem análise interprocedural — e continua sendo
    aproximação, o que é justamente por que a prova real é comportamental.
    """
    used: set[str] = set()
    for inner in ast.walk(node):
        if not isinstance(inner, ast.Call) or _is_observability_call(inner):
            continue
        for expression in [*inner.args, *(keyword.value for keyword in inner.keywords)]:
            for name in ast.walk(expression):
                if isinstance(name, ast.Name) and isinstance(name.ctx, ast.Load):
                    used.add(name.id)
    return used


def _alias_closure(node: FunctionNode, names: set[str]) -> set[str]:
    """Nomes que são apenas outro nome para o escopo autorizado.

    `resolved_company_id = company_id` é indireção legítima. Sem seguir o alias,
    o detector acusaria um handler correto — um falso positivo é seguro, mas
    ruído repetido é como um guarda deixa de ser lido.
    """
    aliases = set(names)
    changed = True
    while changed:
        changed = False
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Assign) or not isinstance(inner.value, ast.Name):
                continue
            if inner.value.id not in aliases:
                continue
            for target in inner.targets:
                if isinstance(target, ast.Name) and target.id not in aliases:
                    aliases.add(target.id)
                    changed = True
    return aliases


def _rebound_names(node: FunctionNode) -> set[str]:
    """Nomes reatribuídos no corpo do handler.

    `company_id = int(request.query_params["company_id"])` descarta o valor
    autorizado e volta a ler a query crua — o handler declara a dependência e
    ignora o que ela decidiu.
    """
    rebound: set[str] = set()
    for inner in ast.walk(node):
        targets: list[ast.expr] = []
        if isinstance(inner, ast.Assign):
            targets = list(inner.targets)
        elif (isinstance(inner, ast.AugAssign | ast.AnnAssign) and inner.value is not None) or isinstance(inner, ast.NamedExpr):
            targets = [inner.target]
        for target in targets:
            if isinstance(target, ast.Name):
                rebound.add(target.id)
    return rebound


def _resolves_scope_in_body(node: FunctionNode) -> bool:
    for inner in ast.walk(node):
        if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name) and inner.func.id in SCOPE_CALLS:
            return True
    return False


def scope_violation(node: FunctionNode) -> str | None:
    """Motivo pelo qual o handler certamente **não** usa o escopo, ou None.

    None não significa "seguro" — significa "esta análise não encontrou
    contradição". A prova de segurança é comportamental.
    """
    scope_names = _scope_parameter_names(node)
    if not scope_names:
        if _resolves_scope_in_body(node):
            return None
        return "não injeta nem resolve o escopo autorizado"

    used = _names_read_inside_calls(node)
    rebound = _rebound_names(node)
    for name in sorted(scope_names):
        if name in rebound:
            return f"`{name}` é reatribuído no corpo, descartando o valor autorizado"
        if not (_alias_closure(node, {name}) & used):
            return f"`{name}` é injetado mas nunca repassado a nenhuma chamada"
    return None


# --- a varredura -------------------------------------------------------------------------


def test_every_mutating_tenant_route_consumes_the_authorized_scope() -> None:
    offenders = [
        f"{module.name}::{node.name} ({method} {path}) — {violation}"
        for module, node, method, path in _mutating_tenant_handlers()
        if (violation := scope_violation(node)) is not None
    ]

    assert offenders == [], (
        "Handler tenant-scoped mutável que não consome o escopo autorizado:\n  "
        + "\n  ".join(offenders)
    )


def test_the_scan_reaches_the_five_t03_routes() -> None:
    """Sem isto, um erro no matcher faria a varredura passar por vacuidade."""
    seen = {path for _module, _node, _method, path in _route_handlers()}
    assert seen >= T03_ROUTES, f"o matcher não alcança: {sorted(T03_ROUTES - seen)}"


def test_the_scan_reaches_a_meaningful_number_of_handlers() -> None:
    """Um matcher quebrado devolveria lista vazia e tudo passaria."""
    handlers = _route_handlers()
    mutating = _mutating_tenant_handlers()
    assert len(handlers) >= 80, f"apenas {len(handlers)} rotas encontradas"
    assert len(mutating) >= 30, f"apenas {len(mutating)} mutáveis tenant-scoped"


# --- controles negativos: cada escape que a revisão demonstrou ---------------------------


def _parse(source: str) -> FunctionNode:
    return next(
        entry
        for entry in ast.walk(ast.parse(source))
        if isinstance(entry, ast.FunctionDef | ast.AsyncFunctionDef)
    )


ESCAPES: tuple[tuple[str, str], ...] = (
    (
        "injeta e ignora — o T-03 verbatim",
        ("def h(booking_id: int, company_id: int = Depends(require_authorized_company_id)) -> dict:\n"
        "    return cancel_calendar_booking(booking_id=booking_id)\n"),
    ),
    (
        "injeta e reatribui a partir da query crua",
        ("def h(booking_id: int, company_id: int = Depends(require_authorized_company_id)) -> dict:\n"
        "    company_id = int(request.query_params['company_id'])\n"
        "    return cancel_calendar_booking(booking_id=booking_id, company_id=company_id)\n"),
    ),
    (
        "injeta e só usa em log, sem repassar ao serviço",
        ("def h(booking_id: int, company_id: int = Depends(require_authorized_company_id)) -> dict:\n"
        "    result = cancel_calendar_booking(booking_id=booking_id)\n"
        "    logger.info('cancelled for %s', company_id)\n"
        "    return result\n"),
    ),
    (
        "não injeta nada",
        "def h(booking_id: int, request: Request) -> dict:\n    return cancel(booking_id)\n",
    ),
    (
        "async, injeta e ignora",
        ("async def h(booking_id: int, company_id: int = Depends(require_authorized_company_id)) -> dict:\n"
        "    return await cancel(booking_id=booking_id)\n"),
    ),
    (
        "Annotated, injeta e ignora",
        ("def h(booking_id: int, company_id: Annotated[int, Depends(require_authorized_company_id)]) -> dict:\n"
        "    return cancel(booking_id=booking_id)\n"),
    ),
    (
        "walrus reatribuindo o escopo",
        ("def h(booking_id: int, company_id: int = Depends(require_authorized_company_id)) -> dict:\n"
        "    if (company_id := body.get('company_id')) is None:\n"
        "        raise ValueError\n"
        "    return cancel(booking_id=booking_id, company_id=company_id)\n"),
    ),
)


@pytest.mark.parametrize("label,source", ESCAPES, ids=[label for label, _ in ESCAPES])
def test_the_detector_rejects_each_known_escape(label: str, source: str) -> None:
    assert scope_violation(_parse(source)) is not None, f"escape aceito: {label}"


ACCEPTED: tuple[tuple[str, str], ...] = (
    (
        "injeta e repassa",
        ("def h(booking_id: int, company_id: int = Depends(require_authorized_company_id)) -> dict:\n"
        "    return cancel(booking_id=booking_id, company_id=company_id)\n"),
    ),
    (
        "async, injeta e repassa",
        ("async def h(booking_id: int, company_id: int = Depends(require_authorized_company_id)) -> dict:\n"
        "    return await cancel(booking_id=booking_id, company_id=company_id)\n"),
    ),
    (
        "Annotated, injeta e repassa",
        ("def h(booking_id: int, company_id: Annotated[int, Depends(require_authorized_company_id)]) -> dict:\n"
        "    return cancel(booking_id=booking_id, company_id=company_id)\n"),
    ),
    (
        "resolve no corpo",
        ("def h(booking_id: int, request: Request) -> dict:\n"
        "    company_id = authorize_company(request, None)\n"
        "    return cancel(booking_id=booking_id, company_id=company_id)\n"),
    ),
    (
        "alias antes de repassar",
        ("def h(booking_id: int, company_id: int = Depends(require_authorized_company_id)) -> dict:\n"
        "    resolved = company_id\n"
        "    return cancel(booking_id=booking_id, company_id=resolved)\n"),
    ),
    (
        "filtro opcional repassado",
        ("def h(company_id: int | None = Depends(require_company_filter)) -> dict:\n"
        "    return listar(company_id=company_id)\n"),
    ),
)


@pytest.mark.parametrize("label,source", ACCEPTED, ids=[label for label, _ in ACCEPTED])
def test_the_detector_accepts_correct_handlers(label: str, source: str) -> None:
    assert scope_violation(_parse(source)) is None, f"handler correto reprovado: {label}"


@pytest.mark.parametrize(
    "label,source",
    [
        (
            "path por keyword",
            ("@admin_router.post(path='/calendar/bookings/{booking_id}/cancel')\n"
            "def h(booking_id: int) -> dict:\n    return cancel(booking_id)\n"),
        ),
        (
            "router com nome que não termina em 'router'",
            ("@admin.post('/calendar/bookings/{booking_id}/cancel')\n"
            "def h(booking_id: int) -> dict:\n    return cancel(booking_id)\n"),
        ),
        (
            "handler async",
            ("@admin_router.post('/calendar/bookings/{booking_id}/cancel')\n"
            "async def h(booking_id: int) -> dict:\n    return await cancel(booking_id)\n"),
        ),
    ],
)
def test_the_route_matcher_reaches_declarations_it_used_to_miss(label: str, source: str) -> None:
    """Escapes de *descoberta*: a rota sumia da varredura antes de ser avaliada."""
    module = ast.parse(f"admin_router = APIRouter()\nadmin = APIRouter()\n{source}")
    routers = _router_names(module)
    node = _parse(source)

    assert _decorator_routes(node, routers), f"rota não descoberta: {label}"


# --- auditoria com a empresa autorizada --------------------------------------------------

AUDITED_T03_HANDLERS = (
    "reschedule_calendar_booking_endpoint",
    "cancel_calendar_booking_endpoint",
    "respond_to_appointment_queue_offer_endpoint",
    "record_booking_outcome_endpoint",
    "record_cancellation_reason_endpoint",
)


def _audit_company_arguments(handler_name: str) -> list[str | None]:
    """`company_id` de cada `_record_audit_event` do handler. None = kwarg ausente."""
    tree = ast.parse(
        pathlib.Path("app/api/admin_routes.py").read_text(encoding="utf-8"),
        filename="admin_routes.py",
    )
    node = next(
        entry
        for entry in ast.walk(tree)
        if isinstance(entry, ast.FunctionDef | ast.AsyncFunctionDef) and entry.name == handler_name
    )
    rendered: list[str | None] = []
    for call in ast.walk(node):
        if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name):
            continue
        if call.func.id != "_record_audit_event":
            continue
        keywords = {keyword.arg: keyword.value for keyword in call.keywords}
        value = keywords.get("company_id")
        rendered.append(None if value is None else ast.unparse(value))
    return rendered


@pytest.mark.parametrize("handler_name", AUDITED_T03_HANDLERS)
def test_the_five_t03_handlers_audit_with_the_authorized_company(handler_name: str) -> None:
    """Presença **e** valor.

    A versão anterior comparava o valor apenas quando o kwarg existia — então
    um handler que não auditava empresa nenhuma passava por vacuidade. Foi
    assim que as duas rotas de analytics gravaram `company_id=None` na trilha
    de auditoria enquanto o teste ficava verde.
    """
    arguments = _audit_company_arguments(handler_name)

    assert arguments, f"{handler_name} não chama _record_audit_event"
    for rendered in arguments:
        assert rendered is not None, (
            f"{handler_name} audita sem `company_id`: o evento fica com empresa nula "
            "e some do filtro de quem precisa investigá-lo."
        )
        assert rendered == "company_id", (
            f"{handler_name} audita com `{rendered}`. A empresa tem que ser a "
            "autorizada — a do registro carregado é, numa exploração, a da vítima."
        )


def test_the_audit_check_covers_every_t03_route() -> None:
    """A parametrização precisa cobrir as cinco rotas, não três delas."""
    handlers_by_route = {
        path: node.name
        for _module, node, _method, path in _route_handlers()
        if path in T03_ROUTES
    }
    assert set(handlers_by_route) == T03_ROUTES, (
        f"rotas do T-03 não encontradas: {sorted(T03_ROUTES - set(handlers_by_route))}"
    )
    assert set(handlers_by_route.values()) == set(AUDITED_T03_HANDLERS), (
        "a lista parametrizada divergiu dos handlers reais das cinco rotas: "
        f"{sorted(set(handlers_by_route.values()) ^ set(AUDITED_T03_HANDLERS))}"
    )


# --- políticas ---------------------------------------------------------------------------


def test_no_admin_route_declares_a_company_id_path_parameter_without_authorizing() -> None:
    """`{company_id}` no path não passa por `require_company_scope`, que lê a query."""
    offenders: list[str] = []
    for module, node, method, path in _mutating_tenant_handlers():
        if "{company_id}" not in path:
            continue
        calls = {
            inner.func.id
            for inner in ast.walk(node)
            if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name)
        }
        if not calls & {"authorize_company", "require_path_company_id"}:
            offenders.append(f"{module.name}::{node.name} ({method} {path})")

    assert offenders == [], f"path com company_id sem autorização explícita: {offenders}"


def test_route_policies_and_registered_routes_agree() -> None:
    registered = {
        getattr(route, "path", "")
        for route in app.routes
        if getattr(route, "path", "").startswith("/admin")
    }
    assert registered, "nenhuma rota /admin registrada"

    without_policy = [path for path in sorted(registered) if describe_route_policy(path) is None]
    assert without_policy == [], f"rotas sem política declarada: {without_policy}"
