"""Gatilho de agenda: dispara por janela, e uma vez por janela.

`trigger.schedule` esteve no catálogo desde a v1 sem ninguém que o acionasse:
dava para desenhar, publicar e ativar um fluxo agendado que nunca rodava. O
motor estava correto — faltava o chamador, e nenhum teste de motor pegaria
isso, porque o motor nunca era acionado.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.automations.catalog import TRIGGER_SCHEDULE, TRIGGER_TYPES
from app.automations.events import TRIGGER_BY_EVENT_TYPE
from app.core.config import settings
from app.services import automation_workflow_service as workflows
from app.services.automation_scheduler_service import (
    dispatch_scheduled_workflows,
    window_key_for,
)
from app.storage import automation_repository as repo


@pytest.fixture(autouse=True)
def _enable_engine(monkeypatch):
    monkeypatch.setattr(settings, "automation_engine_enabled", True)


def _publish_scheduled_workflow(company_id: int, *, cadence: str, hour: int = 9) -> int:
    """Fluxo agendado mínimo, publicado e ativado."""
    graph = {
        "schema_version": "automation-graph/1",
        "nodes": [
            {
                "id": "trigger",
                "node_type": TRIGGER_SCHEDULE,
                "config": {"cadence": cadence, "hour": hour},
            },
            {"id": "fim", "node_type": "terminal.done", "config": {}},
        ],
        "edges": [
            {"id": "e1", "source": "trigger", "target": "fim", "source_handle": "next"}
        ],
    }
    workflow = workflows.create_workflow(
        company_id=company_id,
        workflow_key=f"agendado-{cadence}-{hour}",
        name="Fluxo agendado",
        created_by="teste",
    )
    workflow_id = int(workflow["id"])
    draft = repo.get_draft(company_id=company_id, workflow_id=workflow_id)
    workflows.save_draft(
        company_id=company_id,
        workflow_id=workflow_id,
        graph=graph,
        expected_revision=int(draft["revision"]),
        updated_by="teste",
    )
    workflows.publish_workflow(
        company_id=company_id,
        workflow_id=workflow_id,
        published_by="teste",
    )
    workflows.set_workflow_state(
        company_id=company_id,
        workflow_id=workflow_id,
        state="active",
        actor="teste",
    )
    return workflow_id


def _scheduled_events(company_id: int) -> int:
    """Eventos de agenda emitidos **para esta empresa**.

    O scheduler é global: ele varre os deployments de todas as empresas, e o
    contador que devolve soma todas. Num banco de teste compartilhado, contar
    o retorno global tornaria cada teste sensível ao que os outros deixaram
    para trás. O que importa aqui é o efeito na própria empresa.
    """
    from app.automations.events import EVENT_SCHEDULE_TRIGGERED

    return sum(
        1
        for entry in repo.list_outbox_entries(company_id=company_id)
        if repo.get_event(company_id=company_id, event_id=int(entry["event_id"]))["event_type"]
        == EVENT_SCHEDULE_TRIGGERED
    )


def test_every_trigger_in_the_palette_maps_to_a_canonical_event() -> None:
    """A paleta não pode anunciar um gatilho sem evento correspondente.

    Um gatilho sem evento é um fluxo que publica, ativa e nunca roda — e o
    usuário não tem como descobrir isso a não ser esperando.
    """
    mapped = set(TRIGGER_BY_EVENT_TYPE.values())
    orphans = sorted(TRIGGER_TYPES - mapped)
    assert orphans == [], f"gatilhos sem evento canônico: {orphans}"


def test_a_daily_workflow_fires_in_its_configured_hour(company_factory) -> None:
    company_id = company_factory("agenda-diaria")
    _publish_scheduled_workflow(company_id, cadence="daily", hour=9)

    dispatch_scheduled_workflows(now=datetime(2026, 3, 10, 9, 30, tzinfo=UTC))

    assert _scheduled_events(company_id) == 1


def test_a_daily_workflow_does_not_fire_outside_its_hour(company_factory) -> None:
    """Sem a checagem de hora, um fluxo diário dispararia na primeira janela
    do dia, independentemente do que o operador escolheu."""
    company_id = company_factory("agenda-fora-hora")
    _publish_scheduled_workflow(company_id, cadence="daily", hour=9)

    dispatch_scheduled_workflows(now=datetime(2026, 3, 10, 3, 0, tzinfo=UTC))

    assert _scheduled_events(company_id) == 0


def test_the_same_window_never_fires_twice(company_factory) -> None:
    """Idempotência por janela.

    O worker roda a cada poucos segundos e pode ter réplicas. Sem a chave de
    janela, cada ciclo inscreveria o fluxo de novo — um fluxo diário rodaria
    centenas de vezes por dia.
    """
    company_id = company_factory("agenda-idempotente")
    _publish_scheduled_workflow(company_id, cadence="daily", hour=9)

    dispatch_scheduled_workflows(now=datetime(2026, 3, 10, 9, 5, tzinfo=UTC))
    after_first = _scheduled_events(company_id)
    # Segundo ciclo dentro da mesma janela, alguns minutos depois.
    dispatch_scheduled_workflows(now=datetime(2026, 3, 10, 9, 47, tzinfo=UTC))

    assert after_first == 1
    assert _scheduled_events(company_id) == 1


def test_a_new_window_fires_again(company_factory) -> None:
    company_id = company_factory("agenda-nova-janela")
    _publish_scheduled_workflow(company_id, cadence="daily", hour=9)

    dispatch_scheduled_workflows(now=datetime(2026, 3, 10, 9, 5, tzinfo=UTC))
    after_today = _scheduled_events(company_id)
    dispatch_scheduled_workflows(now=datetime(2026, 3, 11, 9, 5, tzinfo=UTC))

    assert after_today == 1
    assert _scheduled_events(company_id) == 2


def test_a_paused_workflow_is_not_scheduled(company_factory) -> None:
    """Pausar bloqueia novas entradas, inclusive as vindas da agenda."""
    company_id = company_factory("agenda-pausada")
    workflow_id = _publish_scheduled_workflow(company_id, cadence="daily", hour=9)
    workflows.set_workflow_state(
        company_id=company_id,
        workflow_id=workflow_id,
        state="paused",
        actor="teste",
    )

    dispatch_scheduled_workflows(now=datetime(2026, 3, 10, 9, 5, tzinfo=UTC))

    assert _scheduled_events(company_id) == 0


def test_window_keys_are_stable_within_and_distinct_across_windows() -> None:
    early = datetime(2026, 3, 10, 9, 1, tzinfo=UTC)
    late = datetime(2026, 3, 10, 9, 59, tzinfo=UTC)
    next_day = datetime(2026, 3, 11, 9, 1, tzinfo=UTC)

    assert window_key_for("hourly", early) == window_key_for("hourly", late)
    assert window_key_for("daily", early) == window_key_for("daily", late)
    assert window_key_for("daily", early) != window_key_for("daily", next_day)
    # A janela semanal atravessa a virada do dia.
    assert window_key_for("weekly", early) == window_key_for("weekly", next_day)


def test_the_scheduler_never_raises_into_the_worker(monkeypatch) -> None:
    """Uma empresa com configuração estranha não pode parar as demais."""
    from app.services import automation_scheduler_service as scheduler

    def _explode(**kwargs):
        raise RuntimeError("banco indisponível")

    monkeypatch.setattr(
        scheduler.repo, "list_active_deployments_for_trigger_globally", _explode
    )

    result = dispatch_scheduled_workflows(now=datetime(2026, 3, 10, 9, 0, tzinfo=UTC))

    assert result == {"examined": 0, "emitted": 0}
