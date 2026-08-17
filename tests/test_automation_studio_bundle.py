"""O bundle do Automation Studio, visto do backend.

Cobre o cenário obrigatório 22 pelo lado do servidor: o que é servido ao
navegador não carrega segredo de provider nem executa fluxo de verdade.

Quando o bundle não foi construído, os testes que dependem dele são pulados
com a razão explícita — não silenciosamente aprovados.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.platform_routes import AUTOMATION_STUDIO_DIR, automation_studio_is_built
from app.main import app

client = TestClient(app)

FRONTEND_DIR = Path("frontend/automation-studio")

# Formatos de credencial, não palavras: "secret" aparece em comentário
# justamente dizendo que segredo nenhum entra no frontend.
CREDENTIAL_SHAPES = (
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"\bwapp(adm)?_[A-Za-z0-9_\-]{16,}"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\."),
    re.compile(r"Bearer\s+[A-Za-z0-9_\-]{12,}"),
)

requires_bundle = pytest.mark.skipif(
    not automation_studio_is_built(),
    reason=(
        "Bundle do Automation Studio ausente. Rode "
        "`cd frontend/automation-studio && npm ci && npm run build`."
    ),
)


def test_the_frontend_source_exists_and_is_typed() -> None:
    """O código-fonte é versionado, mesmo que o bundle não seja."""
    assert (FRONTEND_DIR / "package.json").is_file()
    assert (FRONTEND_DIR / "tsconfig.json").is_file()
    assert (FRONTEND_DIR / "src" / "App.tsx").is_file()

    tsconfig = (FRONTEND_DIR / "tsconfig.json").read_text(encoding="utf-8")
    assert '"strict": true' in tsconfig
    assert '"strictNullChecks": true' in tsconfig


def test_the_frontend_source_carries_no_credential() -> None:
    sources = [
        path
        for path in (FRONTEND_DIR / "src").rglob("*")
        if path.suffix in {".ts", ".tsx"} and path.is_file()
    ]
    assert len(sources) >= 8

    for path in sources:
        text = path.read_text(encoding="utf-8")
        for shape in CREDENTIAL_SHAPES:
            assert not shape.search(text), f"{path} parece conter uma credencial"


def test_the_frontend_never_evaluates_code() -> None:
    """O navegador não executa workflow: nada de `eval` nem de `new Function`."""
    for path in (FRONTEND_DIR / "src").rglob("*"):
        if path.suffix not in {".ts", ".tsx"} or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"\beval\s*\(", text), f"{path} usa eval"
        assert not re.search(r"new\s+Function\s*\(", text), f"{path} usa new Function"


def test_the_frontend_only_talks_to_the_same_origin() -> None:
    """Nenhum host externo no cliente: sem CORS e sem exfiltração de sessão."""
    client_source = (FRONTEND_DIR / "src" / "api" / "client.ts").read_text(encoding="utf-8")

    assert "credentials: \"same-origin\"" in client_source
    assert not re.search(r"https?://(?!fonts\.)", client_source), (
        "O cliente não deve conter host absoluto."
    )


def test_the_shell_explains_itself_when_the_bundle_is_missing(monkeypatch) -> None:
    """Sem build, o servidor responde 503 dizendo o que fazer — não 404."""
    from app.api import platform_routes

    monkeypatch.setattr(platform_routes, "automation_studio_is_built", lambda: False)
    response = client.get("/platform/automations")

    assert response.status_code == 503
    assert "npm run build" in response.text


@requires_bundle
def test_the_built_shell_is_served() -> None:
    response = client.get("/platform/automations")

    assert response.status_code == 200
    assert "automation-root" in response.text


@requires_bundle
def test_the_built_bundle_carries_no_credential() -> None:
    """A varredura final é sobre o artefato, não sobre o fonte."""
    artifacts = [
        path
        for path in AUTOMATION_STUDIO_DIR.rglob("*")
        if path.suffix in {".js", ".css", ".html"} and path.is_file()
    ]
    assert artifacts, "O bundle construído não tem artefatos."

    for path in artifacts:
        text = path.read_text(encoding="utf-8", errors="ignore")
        for shape in CREDENTIAL_SHAPES:
            assert not shape.search(text), f"{path} parece conter uma credencial"
