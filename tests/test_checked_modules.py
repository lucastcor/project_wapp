"""A lista de módulos verificados no CI não pode esquecer ninguém.

Antes, mypy e Bandit rodavam sobre listas repetidas à mão dentro do workflow.
Um módulo novo simplesmente não era verificado, e nada avisava. Agora a lista
mora em `checked_modules.txt`, o CI a lê, e este teste prova que as superfícies
sensíveis estão dentro dela.

A versão anterior deste arquivo provava menos do que parecia. Ela exigia
`len(modules) >= 60` e alguns prefixos de nome. A revisão independente mostrou
que dava para remover **os oito módulos mais críticos do repositório** —
incluindo `app/api/admin_routes.py`, onde o vazamento cross-tenant viveu, e
`app/storage/db.py` — e os cinco testes continuavam verdes, porque a lista
seguia com mais de 60 entradas e nenhum daqueles nomes casava com os prefixos.

Um limite numérico não protege um módulo específico: ele protege um *tamanho*.
Por isso agora há um conjunto nomeado, e um teste de mutação que remove cada
entrada dele, uma por vez, e exige falha.
"""

from __future__ import annotations

import pathlib

import pytest

CHECKED_MODULES_FILE = pathlib.Path("checked_modules.txt")

# Módulos que precisam estar no gate por nome, não por regra genérica.
#
# O critério para entrar aqui: um erro de tipo ou um achado de segurança neste
# arquivo tem consequência multi-tenant direta. São o ponto de entrada da
# aplicação, a superfície de rotas administrativas, a fronteira de autorização,
# a camada de persistência e os serviços que decidem sobre recurso de um tenant.
CRITICAL_MODULES: frozenset[str] = frozenset(
    {
        # entrada e superfície HTTP
        "app/main.py",
        "app/api/admin_routes.py",
        "app/api/routes.py",
        "app/api/platform_admin_routes.py",
        "app/api/automation_routes.py",
        # fronteira de autorização e identidade
        "app/api/admin_security.py",
        "app/api/company_scope.py",
        "app/core/tenancy.py",
        "app/core/credentials.py",
        "app/core/exceptions.py",
        "app/services/admin_principal_service.py",
        "app/services/tenant_resolution_service.py",
        "app/services/tenant_credential_service.py",
        # persistência
        "app/storage/db.py",
        "app/storage/repository.py",
        "app/storage/schema_utils.py",
        "app/storage/sql_support.py",
        # serviços que resolvem recurso de um tenant por id
        "app/services/analytics_service.py",
        "app/services/appointment_queue_service.py",
        "app/services/calendar_service.py",
        # plano de controle das automações
        "app/services/automation_execution_service.py",
        "app/services/automation_approval_service.py",
        "app/tools/automation_worker.py",
    }
)

# Diretórios inteiramente cobertos. Um arquivo novo em qualquer um deles
# precisa entrar na lista.
FULLY_COVERED_DIRS: tuple[pathlib.Path, ...] = (
    pathlib.Path("app/automations"),
    pathlib.Path("app/api"),
    pathlib.Path("app/storage"),
)

# Prefixos de arquivo que precisam estar cobertos onde quer que apareçam.
COVERED_PREFIXES: tuple[str, ...] = (
    "automation_",
    "migrations_",
    "identity_",
    "evaluation_",
    "tenant_",
    "admin_principal_",
    "company_scope",
)


def load_checked_modules(text: str | None = None) -> set[str]:
    lines = (text if text is not None else CHECKED_MODULES_FILE.read_text(encoding="utf-8")).splitlines()
    return {
        stripped
        for line in lines
        if (stripped := line.strip()) and not stripped.startswith("#")
    }


def test_the_module_list_is_readable_and_not_empty() -> None:
    modules = load_checked_modules()

    assert len(modules) >= 60
    for module in modules:
        assert module.endswith(".py"), module


def test_every_listed_module_exists() -> None:
    """Uma entrada morta faria o CI falhar por arquivo inexistente."""
    missing = [module for module in sorted(load_checked_modules()) if not pathlib.Path(module).is_file()]

    assert missing == [], f"Módulos listados que não existem: {missing}"


def test_every_critical_module_is_checked() -> None:
    missing = sorted(CRITICAL_MODULES - load_checked_modules())

    assert missing == [], (
        "Módulos críticos fora de checked_modules.txt. Um módulo fora do gate é "
        f"um módulo onde mypy e Bandit não dizem nada: {missing}"
    )


@pytest.mark.parametrize("critical", sorted(CRITICAL_MODULES))
def test_removing_any_critical_module_fails_the_gate(critical: str) -> None:
    """Controle negativo por mutação — um por módulo crítico.

    Sem isto, `test_every_critical_module_is_checked` poderia passar por
    construção (por exemplo, se `load_checked_modules` devolvesse o próprio
    conjunto esperado). Aqui a lista é realmente editada e a verificação
    precisa reprovar.
    """
    text = CHECKED_MODULES_FILE.read_text(encoding="utf-8")
    mutated = "\n".join(line for line in text.splitlines() if line.strip() != critical)

    assert critical not in load_checked_modules(mutated), "a mutação não removeu nada"
    assert sorted(CRITICAL_MODULES - load_checked_modules(mutated)) == [critical]


def test_every_critical_module_exists_on_disk() -> None:
    """A lista de críticos não pode apodrecer depois de um rename."""
    missing = [module for module in sorted(CRITICAL_MODULES) if not pathlib.Path(module).is_file()]

    assert missing == [], f"Módulos críticos que não existem mais: {missing}"


def test_fully_covered_packages_have_no_gaps() -> None:
    modules = load_checked_modules()
    uncovered: list[str] = []

    for directory in FULLY_COVERED_DIRS:
        for path in sorted(directory.rglob("*.py")):
            if path.name == "__init__.py" and path.read_text(encoding="utf-8").strip() == "":
                continue
            if path.as_posix() not in modules:
                uncovered.append(path.as_posix())

    assert uncovered == [], (
        "Pacotes cobertos por inteiro com módulo de fora de checked_modules.txt: "
        f"{uncovered}"
    )


def test_sensitive_modules_are_covered() -> None:
    """Identidade, tenancy, avaliação e migrations entram sempre."""
    modules = load_checked_modules()
    uncovered: list[str] = []

    for path in sorted(pathlib.Path("app").rglob("*.py")):
        name = path.name
        if not any(name.startswith(prefix) for prefix in COVERED_PREFIXES):
            continue
        if path.as_posix() not in modules:
            uncovered.append(path.as_posix())

    assert uncovered == [], f"Módulos sensíveis fora de checked_modules.txt: {uncovered}"


def test_the_ci_workflow_reads_the_list_instead_of_repeating_it() -> None:
    """O workflow não pode voltar a ter a lista embutida."""
    workflow = pathlib.Path(".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "checked_modules.txt" in workflow
    # Uma lista embutida reapareceria como muitos caminhos literais no YAML.
    literal_paths = workflow.count("app/services/")
    assert literal_paths == 0, (
        "O workflow voltou a listar módulos à mão; use checked_modules.txt."
    )


@pytest.mark.parametrize("document", [".github/workflows/ci.yml", ".claude/agents/release-verifier.md"])
def test_module_expansion_is_shell_portable(document: str) -> None:
    """`$MODULES` sem aspas depende de word splitting, que o zsh não faz.

    O comando publicado rodava no bash do CI e falhava no zsh do desenvolvedor
    com `File name too long` — a lista inteira virava **um** nome de arquivo.
    Um gate que só roda num shell não é um gate; é uma instrução que a metade
    das pessoas não consegue seguir.

    A forma portável é `xargs`, que separa os argumentos ele mesmo.
    """
    text = pathlib.Path(document).read_text(encoding="utf-8")

    assert "$MODULES" not in text and "${MODULES}" not in text, (
        f"{document} expande a lista via variável escalar; use `xargs`."
    )
    assert "xargs" in text, f"{document} precisa expandir checked_modules.txt com xargs."


def test_the_lint_gate_is_configured_not_improvised() -> None:
    """O CI não pode voltar a passar `--select` na linha de comando.

    Uma seleção no comando é invisível para quem roda `ruff check` localmente,
    e foi assim que o gate ficou em quatro regras enquanto o repositório
    acumulava 260 achados — 58 deles `noqa` que já não suprimiam nada.
    """
    workflow = pathlib.Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    config = pathlib.Path("ruff.toml")

    assert config.is_file(), "ruff.toml precisa existir e ser versionado"
    # Só as linhas de comando importam: o comentário acima do passo cita a
    # forma antiga de propósito, para explicar por que ela saiu.
    commands = [
        line for line in workflow.splitlines() if "ruff" in line and not line.lstrip().startswith("#")
    ]
    assert commands, "o CI não roda ruff"
    improvised = [line for line in commands if "--select" in line or "--ignore" in line]
    assert improvised == [], (
        f"o CI voltou a escolher regras na linha de comando; a seleção mora em ruff.toml: {improvised}"
    )
    assert any("ruff check app tests" in line for line in commands)

    text = config.read_text(encoding="utf-8")
    # Cada código fora do gate precisa de justificativa escrita ao lado.
    for code in ("S608", "PLC0415", "BLE001", "E501", "B008", "PERF401"):
        assert f"#   {code}" in text or f"#   {code} " in text, (
            f"{code} está em `ignore` sem motivo escrito em ruff.toml"
        )
