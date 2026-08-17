"""A documentação que cita defaults precisa citar os defaults reais.

Documentação erra em silêncio. Um valor que diverge do código não quebra
nada em teste nem em produção — só faz um operador dimensionar um lease, um
teto de retry ou um limite de plano pelo número errado, e descobrir o engano
quando o comportamento não bate com o que estava escrito.

Estes testes leem a tabela do próprio documento e a comparam com a fonte.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from app.automations.catalog import NODE_CATALOG
from app.automations.entitlements import CAPABILITY_TO_PLAN_LIMIT
from app.core.config import settings
from app.services.billing_service import PLANS

DOC = pathlib.Path("docs/automation_studio.md")
RAG_DOC = pathlib.Path("docs/rag_e_finetuning.md")

# `| \`NOME\` | \`valor\` | descrição |`
_CONFIG_ROW = re.compile(r"^\|\s*`(AUTOMATION_[A-Z_]+)`\s*\|\s*`([^`]+)`\s*\|", re.MULTILINE)


def _documented_settings() -> dict[str, str]:
    return dict(_CONFIG_ROW.findall(DOC.read_text(encoding="utf-8")))


def _actual(env_name: str) -> str:
    attribute = env_name.lower()
    value = getattr(settings, attribute)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


DOCUMENTED = _documented_settings()


def test_the_config_table_was_actually_found() -> None:
    """Guarda contra o teste passar por não ter lido nada.

    Se a tabela mudar de formato, os testes abaixo passariam com zero linhas —
    o pior desfecho possível para um teste de consistência.
    """
    assert len(DOCUMENTED) >= 10, f"apenas {len(DOCUMENTED)} variáveis lidas do documento"


@pytest.mark.parametrize("env_name", sorted(DOCUMENTED))
def test_documented_default_matches_the_setting(env_name: str) -> None:
    attribute = env_name.lower()
    assert hasattr(settings, attribute), (
        f"{env_name} está documentada mas não existe em Settings"
    )
    assert DOCUMENTED[env_name] == _actual(env_name), (
        f"{env_name}: documento diz {DOCUMENTED[env_name]!r}, "
        f"código usa {_actual(env_name)!r}"
    )


def test_every_automation_setting_is_documented() -> None:
    """O inverso: uma variável nova não pode ficar fora do documento."""
    in_code = {
        name.upper()
        for name in dir(settings)
        if name.startswith("automation_") and not name.startswith("_")
    }
    undocumented = sorted(in_code - set(DOCUMENTED))
    assert undocumented == [], f"variáveis de automação não documentadas: {undocumented}"


def test_documented_capability_table_matches_the_catalog() -> None:
    """A tabela de capacidades reflete o catálogo e o mapa de entitlements."""
    text = DOC.read_text(encoding="utf-8")
    for node_type, spec in sorted(NODE_CATALOG.items()):
        if not spec.required_capability:
            continue
        plan_limit = CAPABILITY_TO_PLAN_LIMIT[spec.required_capability]
        assert f"`{node_type}`" in text, f"{node_type} exige capacidade e não está documentado"
        assert f"`{plan_limit}`" in text, (
            f"o limite {plan_limit!r} de {node_type} não aparece no documento"
        )


def test_documented_plan_limits_match_the_billing_plans() -> None:
    """Os números por plano vêm do billing, não de uma tabela paralela."""
    text = DOC.read_text(encoding="utf-8")
    for plan_key, plan in PLANS.items():
        for metric in ("automation_workflows", "automation_executions"):
            value = int(plan["limits"][metric])
            # O documento usa separador de milhar em português.
            rendered = f"{value:,}".replace(",", ".")
            assert rendered in text or str(value) in text, (
                f"limite {metric}={value} do plano {plan_key} não aparece no documento"
            )


# --- documentação de RAG e fine-tuning ------------------------------------------

_RAG_ROW = re.compile(
    r"^\|\s*`([A-Z][A-Z_0-9]+)`\s*\|\s*`?([^`|]*)`?\s*\|", re.MULTILINE
)


def _documented_rag_settings() -> dict[str, str]:
    rows = _RAG_ROW.findall(RAG_DOC.read_text(encoding="utf-8"))
    # O travessão marca "sem default" (a chave de PII, obrigatória em produção).
    return {name: value.strip() for name, value in rows if value.strip() not in {"", "—"}}


RAG_DOCUMENTED = _documented_rag_settings()


def test_the_rag_config_table_was_actually_found() -> None:
    assert len(RAG_DOCUMENTED) >= 10, (
        f"apenas {len(RAG_DOCUMENTED)} variáveis lidas de {RAG_DOC}"
    )


@pytest.mark.parametrize("env_name", sorted(RAG_DOCUMENTED))
def test_documented_rag_default_matches_the_setting(env_name: str) -> None:
    attribute = env_name.lower()
    assert hasattr(settings, attribute), (
        f"{env_name} está documentada em {RAG_DOC} mas não existe em Settings"
    )
    assert RAG_DOCUMENTED[env_name] == _actual(env_name), (
        f"{env_name}: documento diz {RAG_DOCUMENTED[env_name]!r}, "
        f"código usa {_actual(env_name)!r}"
    )
