"""Disparo dos fluxos com gatilho de agenda.

`trigger.schedule` está no catálogo desde a v1, e até aqui ninguém o
acionava: era possível desenhar, publicar e ativar um fluxo agendado que
nunca rodava. O motor estava certo — faltava quem o chamasse, e esse é o tipo
de defeito que teste de unidade do motor não pega.

## Janela, não instante

O scheduler não guarda "última execução". Ele calcula a **janela corrente**
(a hora, o dia ou a semana em curso) e emite um evento cuja chave inclui essa
janela. Como `event_key` é único por empresa, emitir a mesma janela duas vezes
encontra o evento existente e não cria uma segunda execução.

Isso importa porque o worker roda a cada poucos segundos e pode ter várias
réplicas. Guardar "última execução" exigiria lock e ainda assim perderia
disparos num restart no instante errado; a janela é idempotente por
construção e não tem estado a corromper.

A consequência aceita é que um worker parado por mais de uma janela **não
recupera** as janelas perdidas: ele dispara a corrente e segue. Para um fluxo
agendado isso é o comportamento certo — executar às 9h um lote de "bom dia"
represado de três dias é pior do que não executá-lo.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from app.automations.catalog import TRIGGER_SCHEDULE
from app.automations.domain import parse_graph
from app.core.logging import get_logger, log_event
from app.services.automation_trigger_service import emit_schedule_trigger
from app.storage import automation_repository as repo

logger = get_logger(__name__)

CADENCE_HOURLY = "hourly"
CADENCE_DAILY = "daily"
CADENCE_WEEKLY = "weekly"


def window_key_for(cadence: str, moment: datetime) -> str:
    """Identidade da janela corrente para esta cadência.

    Sempre em UTC. Derivar a janela do fuso da empresa faria o mesmo fluxo
    disparar duas vezes na virada do horário de verão, ou nenhuma.
    """
    if cadence == CADENCE_HOURLY:
        return moment.strftime("h:%Y-%m-%dT%H")
    if cadence == CADENCE_WEEKLY:
        iso_year, iso_week, _ = moment.isocalendar()
        return f"w:{iso_year}-{iso_week:02d}"
    return moment.strftime("d:%Y-%m-%d")


def _schedule_config(graph_json: str) -> dict[str, Any] | None:
    """Configuração do nó de gatilho de agenda, se o grafo tiver um."""
    import json

    try:
        graph = parse_graph(json.loads(graph_json or "{}"))
    except (TypeError, ValueError):
        return None
    for node in graph.nodes:
        if node.node_type == TRIGGER_SCHEDULE:
            return dict(node.config)
    return None


def _is_due(config: dict[str, Any], moment: datetime) -> bool:
    """Se a janela corrente é a janela em que este fluxo deve disparar.

    Cadência horária dispara em toda janela. Diária e semanal disparam apenas
    na hora configurada — sem isso, um fluxo diário dispararia na primeira
    janela horária do dia independentemente do que o operador escolheu.
    """
    cadence = str(config.get("cadence") or CADENCE_DAILY)
    if cadence == CADENCE_HOURLY:
        return True
    try:
        target_hour = int(config.get("hour", 9))
    except (TypeError, ValueError):
        target_hour = 9
    return moment.hour == max(0, min(23, target_hour))


def dispatch_scheduled_workflows(*, now: datetime | None = None) -> dict[str, Any]:
    """Emite o gatilho de agenda para os fluxos ativos cuja janela chegou.

    Devolve contagens, e nunca levanta: o scheduler roda dentro do ciclo do
    worker, e uma empresa com configuração estranha não pode impedir o
    processamento das demais.
    """
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    examined = 0
    emitted = 0

    try:
        deployments = repo.list_active_deployments_for_trigger_globally(
            trigger_type=TRIGGER_SCHEDULE,
        )
    except Exception as exc:
        log_event(
            logger,
            logging.WARNING,
            "automation.scheduler.list_failed",
            error_type=type(exc).__name__,
        )
        return {"examined": 0, "emitted": 0}

    for deployment in deployments:
        examined += 1
        config = _schedule_config(str(deployment.get("graph_json") or ""))
        if config is None or not _is_due(config, moment):
            continue

        cadence = str(config.get("cadence") or CADENCE_DAILY)
        result = emit_schedule_trigger(
            company_id=int(deployment["company_id"]),
            workflow_id=int(deployment["workflow_id"]),
            window_key=window_key_for(cadence, moment),
        )
        if result.get("emitted"):
            emitted += 1

    if emitted:
        log_event(
            logger,
            logging.INFO,
            "automation.scheduler.dispatched",
            examined=examined,
            emitted=emitted,
        )
    return {"examined": examined, "emitted": emitted}
