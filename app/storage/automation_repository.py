"""Persistência do Automation Studio.

Todo acesso tenant-scoped exige `company_id` e o filtro entra **dentro** da
query. As únicas varreduras sem empresa são o claim de job e o de outbox: o
worker é global por natureza, e o isolamento acontece logo em seguida, ao
carregar a execução com o `company_id` que veio do próprio job.

Claim concorrente:

- **Postgres**: `SELECT ... FOR UPDATE SKIP LOCKED` numa transação, que é a
  primitiva certa para uma fila com várias réplicas;
- **SQLite**: `UPDATE ... WHERE id = (SELECT ...)` com verificação de
  `rowcount`, que serializa pelo lock de escrita do próprio arquivo.

Nos dois casos o claim é atômico: dois workers nunca ficam com o mesmo job.
"""

from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.tenancy import require_company_id
from app.storage.db import db_transaction
from app.storage.schema_utils import assert_identifier, is_postgres
from app.storage.sql_support import (
    execute,
    execute_rowcount,
    fetch_all,
    fetch_one,
    optional_equals,
    transaction,
)

_WORKFLOW_COLUMNS = """
    id,
    company_id,
    workflow_key,
    name,
    description,
    archived,
    created_by,
    created_at,
    updated_at
"""

_DRAFT_COLUMNS = """
    id,
    company_id,
    workflow_id,
    revision,
    schema_version,
    graph_json,
    viewport_json,
    updated_by,
    created_at,
    updated_at
"""

_VERSION_COLUMNS = """
    id,
    company_id,
    workflow_id,
    version,
    schema_version,
    graph_json,
    canonical_form,
    content_hash,
    trigger_type,
    published_by,
    published_at,
    notes
"""

_DEPLOYMENT_COLUMNS = """
    id,
    company_id,
    workflow_id,
    version_id,
    trigger_type,
    state,
    activated_by,
    activated_at,
    paused_by,
    paused_at,
    created_by,
    created_at,
    updated_at
"""

_EXECUTION_COLUMNS = """
    id,
    company_id,
    workflow_id,
    version_id,
    event_id,
    enrollment_key,
    status,
    current_node_id,
    steps_executed,
    context_json,
    failure_reason,
    started_at,
    finished_at,
    updated_at
"""

_JOB_COLUMNS = """
    id,
    company_id,
    execution_id,
    job_kind,
    node_id,
    status,
    attempts,
    max_attempts,
    run_at,
    lease_owner,
    lease_expires_at,
    last_error,
    payload_json,
    cancel_requested,
    created_at,
    updated_at
"""

_EFFECT_COLUMNS = """
    id,
    company_id,
    execution_id,
    effect_key,
    effect_kind,
    node_id,
    occurrence,
    status,
    request_json,
    result_json,
    failure_reason,
    created_at,
    completed_at
"""

_GATE_COLUMNS = """
    id,
    company_id,
    execution_id,
    node_id,
    occurrence,
    status,
    title,
    summary,
    payload_json,
    claimed_by,
    claimed_at,
    lease_expires_at,
    decided_by,
    decided_at,
    decision,
    decision_note,
    expires_at,
    created_at,
    updated_at
"""


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat()


def new_lease_owner(prefix: str = "worker") -> str:
    """Dono de lease único por processo/execução do worker."""
    return f"{prefix}:{secrets.token_hex(8)}"


def _loads(raw: Any, fallback: Any) -> Any:
    if raw is None:
        return fallback
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return fallback


def normalize_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    """Devolve o mesmo tipo em SQLite e PostgreSQL.

    O SQLite entrega timestamp como texto; o psycopg entrega `datetime`. Sem
    normalizar, qualquer comparação em Python (`run_at > agora.isoformat()`)
    funciona num backend e levanta `TypeError` no outro — defeito que só
    aparece em produção, porque a suíte rodava só em SQLite.

    A escolha é ISO 8601 em texto: é o que o resto do código já usa para
    montar `run_at` e `lease_expires_at`, e é ordenável como string.
    """
    if row is None:
        return None
    return {
        key: (
            (value if value.tzinfo else value.replace(tzinfo=UTC)).astimezone(UTC).isoformat()
            if isinstance(value, datetime)
            else value
        )
        for key, value in row.items()
    }


def _normalize_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [normalized for row in rows if (normalized := normalize_row(row)) is not None]


# --- workflows ------------------------------------------------------------------------


def create_workflow(
    *,
    company_id: int,
    workflow_key: str,
    name: str,
    description: str | None = None,
    created_by: str | None = None,
) -> int:
    resolved_company_id = require_company_id(company_id, context="automation workflow create")
    return execute(
        """
        INSERT INTO automation_workflows (
            company_id, workflow_key, name, description, created_by
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (resolved_company_id, workflow_key, name, description, created_by),
    )


def get_workflow(*, company_id: int, workflow_id: int) -> dict[str, Any] | None:
    resolved_company_id = require_company_id(company_id, context="automation workflow read")
    return fetch_one(
        f"SELECT {_WORKFLOW_COLUMNS} FROM automation_workflows WHERE id = ? AND company_id = ?",
        (workflow_id, resolved_company_id),
    )


def get_workflow_by_key(*, company_id: int, workflow_key: str) -> dict[str, Any] | None:
    resolved_company_id = require_company_id(company_id, context="automation workflow read")
    return fetch_one(
        f"""
        SELECT {_WORKFLOW_COLUMNS} FROM automation_workflows
        WHERE company_id = ? AND workflow_key = ?
        """,
        (resolved_company_id, workflow_key),
    )


def list_workflows(
    *,
    company_id: int,
    include_archived: bool = False,
    search: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    resolved_company_id = require_company_id(company_id, context="automation workflow listing")
    term = f"%{(search or '').strip().lower()}%"
    return _normalize_rows(fetch_all(
        f"""
        SELECT {_WORKFLOW_COLUMNS} FROM automation_workflows
        WHERE company_id = ?
          AND (? = 1 OR archived = 0)
          AND (? = '%%' OR LOWER(name) LIKE ? OR LOWER(workflow_key) LIKE ?)
        ORDER BY updated_at DESC, id DESC
        LIMIT ?
        """,
        (resolved_company_id, int(include_archived), term, term, term, limit),
    ))


def update_workflow(
    *,
    company_id: int,
    workflow_id: int,
    name: str | None = None,
    description: str | None = None,
    archived: bool | None = None,
) -> bool:
    resolved_company_id = require_company_id(company_id, context="automation workflow update")
    return (
        execute_rowcount(
            """
            UPDATE automation_workflows
            SET name = COALESCE(?, name),
                description = COALESCE(?, description),
                archived = COALESCE(?, archived),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND company_id = ?
            """,
            (
                name,
                description,
                None if archived is None else int(archived),
                workflow_id,
                resolved_company_id,
            ),
        )
        > 0
    )


# --- rascunhos ------------------------------------------------------------------------


def get_draft(*, company_id: int, workflow_id: int) -> dict[str, Any] | None:
    resolved_company_id = require_company_id(company_id, context="automation draft read")
    row = fetch_one(
        f"""
        SELECT {_DRAFT_COLUMNS} FROM automation_workflow_drafts
        WHERE company_id = ? AND workflow_id = ?
        """,
        (resolved_company_id, workflow_id),
    )
    if not row:
        return None
    return {
        **row,
        "graph": _loads(row.get("graph_json"), {}),
        "viewport": _loads(row.get("viewport_json"), None),
    }


def create_draft(
    *,
    company_id: int,
    workflow_id: int,
    schema_version: str,
    graph: dict[str, Any],
    viewport: dict[str, Any] | None = None,
    updated_by: str | None = None,
) -> int:
    resolved_company_id = require_company_id(company_id, context="automation draft create")
    return execute(
        """
        INSERT INTO automation_workflow_drafts (
            company_id, workflow_id, revision, schema_version,
            graph_json, viewport_json, updated_by
        )
        VALUES (?, ?, 1, ?, ?, ?, ?)
        """,
        (
            resolved_company_id,
            workflow_id,
            schema_version,
            json.dumps(graph, ensure_ascii=False),
            json.dumps(viewport, ensure_ascii=False) if viewport else None,
            updated_by,
        ),
    )


def save_draft_if_current(
    *,
    company_id: int,
    workflow_id: int,
    expected_revision: int,
    schema_version: str,
    graph: dict[str, Any],
    viewport: dict[str, Any] | None = None,
    updated_by: str | None = None,
) -> int | None:
    """Grava o rascunho apenas se a revisão continuar sendo a esperada.

    Devolve a nova revisão, ou `None` quando outro editor já avançou. É o
    ponto onde o 409 nasce: sem o `WHERE revision = ?`, a última escrita
    apagaria silenciosamente o trabalho da outra pessoa.
    """
    resolved_company_id = require_company_id(company_id, context="automation draft write")
    updated = execute_rowcount(
        """
        UPDATE automation_workflow_drafts
        SET revision = revision + 1,
            schema_version = ?,
            graph_json = ?,
            viewport_json = ?,
            updated_by = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE company_id = ?
          AND workflow_id = ?
          AND revision = ?
        """,
        (
            schema_version,
            json.dumps(graph, ensure_ascii=False),
            json.dumps(viewport, ensure_ascii=False) if viewport else None,
            updated_by,
            resolved_company_id,
            workflow_id,
            expected_revision,
        ),
    )
    if not updated:
        return None
    return expected_revision + 1


# --- versões imutáveis ------------------------------------------------------------------


def get_next_version_number(*, company_id: int, workflow_id: int) -> int:
    resolved_company_id = require_company_id(company_id, context="automation version read")
    row = fetch_one(
        """
        SELECT MAX(version) AS max_version FROM automation_workflow_versions
        WHERE company_id = ? AND workflow_id = ?
        """,
        (resolved_company_id, workflow_id),
    )
    return int((row or {}).get("max_version") or 0) + 1


def create_version(
    *,
    company_id: int,
    workflow_id: int,
    version: int,
    schema_version: str,
    graph: dict[str, Any],
    canonical_form: str,
    content_hash: str,
    trigger_type: str,
    published_by: str | None = None,
    notes: str | None = None,
) -> int:
    resolved_company_id = require_company_id(company_id, context="automation version create")
    return execute(
        """
        INSERT INTO automation_workflow_versions (
            company_id, workflow_id, version, schema_version, graph_json,
            canonical_form, content_hash, trigger_type, published_by, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            resolved_company_id,
            workflow_id,
            version,
            schema_version,
            json.dumps(graph, ensure_ascii=False),
            canonical_form,
            content_hash,
            trigger_type,
            published_by,
            notes,
        ),
    )


def get_version(*, company_id: int, version_id: int) -> dict[str, Any] | None:
    resolved_company_id = require_company_id(company_id, context="automation version read")
    row = fetch_one(
        f"""
        SELECT {_VERSION_COLUMNS} FROM automation_workflow_versions
        WHERE company_id = ? AND id = ?
        """,
        (resolved_company_id, version_id),
    )
    return {**(normalize_row(row) or {}), "graph": _loads(row.get("graph_json"), {})} if row else None


def list_versions(*, company_id: int, workflow_id: int, limit: int = 50) -> list[dict[str, Any]]:
    resolved_company_id = require_company_id(company_id, context="automation version listing")
    rows = fetch_all(
        f"""
        SELECT {_VERSION_COLUMNS} FROM automation_workflow_versions
        WHERE company_id = ? AND workflow_id = ?
        ORDER BY version DESC
        LIMIT ?
        """,
        (resolved_company_id, workflow_id, limit),
    )
    return [{**(normalize_row(row) or {}), "graph": _loads(row.get("graph_json"), {})} for row in rows]


# --- deployments --------------------------------------------------------------------


def upsert_deployment(
    *,
    company_id: int,
    workflow_id: int,
    version_id: int,
    trigger_type: str,
    state: str,
    created_by: str | None = None,
) -> int:
    """Aponta o deployment para uma versão exata, sempre no estado informado.

    Publicar chama isto com `paused`: nenhuma empresa passa a executar
    automação como efeito colateral de publicar.
    """
    resolved_company_id = require_company_id(company_id, context="automation deployment write")
    existing = fetch_one(
        "SELECT id FROM automation_deployments WHERE company_id = ? AND workflow_id = ?",
        (resolved_company_id, workflow_id),
    )
    if existing:
        execute(
            """
            UPDATE automation_deployments
            SET version_id = ?, trigger_type = ?, state = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (version_id, trigger_type, state, int(existing["id"])),
        )
        return int(existing["id"])

    return execute(
        """
        INSERT INTO automation_deployments (
            company_id, workflow_id, version_id, trigger_type, state, created_by
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (resolved_company_id, workflow_id, version_id, trigger_type, state, created_by),
    )


def set_deployment_state(
    *,
    company_id: int,
    workflow_id: int,
    state: str,
    actor: str | None = None,
) -> bool:
    resolved_company_id = require_company_id(company_id, context="automation deployment state")
    if state == "active":
        return (
            execute_rowcount(
                """
                UPDATE automation_deployments
                SET state = 'active',
                    activated_by = ?, activated_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE company_id = ? AND workflow_id = ?
                """,
                (actor, resolved_company_id, workflow_id),
            )
            > 0
        )
    return (
        execute_rowcount(
            """
            UPDATE automation_deployments
            SET state = 'paused',
                paused_by = ?, paused_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE company_id = ? AND workflow_id = ?
            """,
            (actor, resolved_company_id, workflow_id),
        )
        > 0
    )


def get_deployment(*, company_id: int, workflow_id: int) -> dict[str, Any] | None:
    resolved_company_id = require_company_id(company_id, context="automation deployment read")
    return fetch_one(
        f"""
        SELECT {_DEPLOYMENT_COLUMNS} FROM automation_deployments
        WHERE company_id = ? AND workflow_id = ?
        """,
        (resolved_company_id, workflow_id),
    )


def list_active_deployments_for_trigger(
    *,
    company_id: int,
    trigger_type: str,
) -> list[dict[str, Any]]:
    """Deployments ativos da empresa para um gatilho.

    Pausado não aparece aqui: é exatamente assim que pausar bloqueia novas
    entradas sem tocar nas execuções já em espera.
    """
    resolved_company_id = require_company_id(company_id, context="automation dispatch")
    return fetch_all(
        f"""
        SELECT {_DEPLOYMENT_COLUMNS} FROM automation_deployments
        WHERE company_id = ?
          AND trigger_type = ?
          AND state = 'active'
        ORDER BY workflow_id ASC
        """,
        (resolved_company_id, trigger_type),
    )


def list_active_deployments_for_trigger_globally(
    *,
    trigger_type: str,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Deployments ativos de **todas** as empresas para um gatilho.

    Existe para o scheduler, que é global pela mesma razão que o worker: ele
    percorre a fila sem filtrar empresa, e o isolamento vem do `company_id`
    que acompanha cada linha e é usado imediatamente a seguir. A alternativa
    — iterar empresa por empresa — faria o custo do scheduler crescer com a
    carteira mesmo quando quase nenhuma tem fluxo agendado.

    Junta a versão para trazer o grafo: a cadência está na configuração do nó
    de gatilho, e sem ela o scheduler não sabe qual janela avaliar.
    """
    return fetch_all(
        """
        SELECT d.id, d.company_id, d.workflow_id, d.version_id, d.trigger_type,
               d.state, v.graph_json
        FROM automation_deployments AS d
        JOIN automation_workflow_versions AS v
          ON v.id = d.version_id AND v.company_id = d.company_id
        WHERE d.trigger_type = ?
          AND d.state = 'active'
        ORDER BY d.company_id ASC, d.workflow_id ASC
        LIMIT ?
        """,
        (trigger_type, limit),
    )


# --- eventos e outbox -------------------------------------------------------------------


def record_event_with_outbox(
    *,
    company_id: int,
    event_key: str,
    event_type: str,
    schema_version: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Grava evento e entrada de outbox na **mesma** transação.

    Idempotente por `(company_id, event_key)`: reentregar o mesmo fato
    devolve o evento existente e não cria uma segunda entrada de outbox.
    """
    resolved_company_id = require_company_id(company_id, context="automation event write")
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    existing = fetch_one(
        "SELECT id FROM automation_events WHERE company_id = ? AND event_key = ?",
        (resolved_company_id, event_key),
    )
    if existing:
        return {"event_id": int(existing["id"]), "created": False}

    with transaction() as cursor:
        cursor.execute(
            """
            INSERT INTO automation_events (
                company_id, event_key, event_type, schema_version,
                payload_json, payload_bytes
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                resolved_company_id,
                event_key,
                event_type,
                schema_version,
                serialized,
                len(serialized.encode("utf-8")),
            ),
        )
        event_id = int(cursor.lastrowid or 0)
        cursor.execute(
            """
            INSERT INTO automation_outbox (company_id, event_id, status)
            VALUES (?, ?, 'pending')
            """,
            (resolved_company_id, event_id),
        )

    return {"event_id": event_id, "created": True}


def insert_event_with_outbox_on_cursor(
    cursor: Any,
    *,
    company_id: int,
    event_key: str,
    event_type: str,
    schema_version: str,
    payload: dict[str, Any],
) -> int | None:
    """Grava evento + outbox no cursor de **outra** transação.

    É o que torna o outbox realmente transacional: quem persiste o fato
    (a mensagem) grava o evento no mesmo commit, então não existe janela em
    que a mensagem esteja salva e o evento perdido.

    O trabalho acontece dentro de um SAVEPOINT. Se a escrita do evento
    falhar, só ela é desfeita — o fato principal continua e o atendimento não
    é derrubado por um problema de automação.

    Devolve o `event_id`, ou `None` se o evento já existia ou não pôde ser
    gravado.
    """
    resolved_company_id = require_company_id(company_id, context="automation event write")
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    cursor.execute(
        "SELECT id FROM automation_events WHERE company_id = ? AND event_key = ?",
        (resolved_company_id, event_key),
    )
    existing = cursor.fetchone()
    if existing:
        return None

    cursor.execute("SAVEPOINT automation_event")
    try:
        cursor.execute(
            """
            INSERT INTO automation_events (
                company_id, event_key, event_type, schema_version,
                payload_json, payload_bytes
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                resolved_company_id,
                event_key,
                event_type,
                schema_version,
                serialized,
                len(serialized.encode("utf-8")),
            ),
        )
        event_id = int(cursor.lastrowid or 0)
        cursor.execute(
            """
            INSERT INTO automation_outbox (company_id, event_id, status)
            VALUES (?, ?, 'pending')
            """,
            (resolved_company_id, event_id),
        )
        cursor.execute("RELEASE SAVEPOINT automation_event")
        return event_id
    except Exception:
        cursor.execute("ROLLBACK TO SAVEPOINT automation_event")
        cursor.execute("RELEASE SAVEPOINT automation_event")
        raise


def get_event(*, company_id: int, event_id: int) -> dict[str, Any] | None:
    resolved_company_id = require_company_id(company_id, context="automation event read")
    row = fetch_one(
        """
        SELECT id, company_id, event_key, event_type, schema_version,
               payload_json, payload_bytes, occurred_at, created_at
        FROM automation_events
        WHERE company_id = ? AND id = ?
        """,
        (resolved_company_id, event_id),
    )
    return {**(normalize_row(row) or {}), "payload": _loads(row.get("payload_json"), {})} if row else None


def claim_outbox_entries(*, lease_owner: str, limit: int = 20, lease_seconds: int = 60) -> list[dict[str, Any]]:
    """Reserva entradas pendentes de outbox para este worker."""
    lease_until = _iso(datetime.now(UTC) + timedelta(seconds=lease_seconds))
    claimed: list[dict[str, Any]] = []
    seen: set[int] = set()

    for _ in range(limit):
        row = _claim_one(
            table="automation_outbox",
            lease_owner=lease_owner,
            lease_until=lease_until,
            ready_column="available_at",
            claimable_status="pending",
            exclude_ids=frozenset(seen),
        )
        if row is None:
            break
        seen.add(int(row["id"]))
        claimed.append(normalize_row(row) or row)
    return claimed


def finish_outbox_entry(
    *,
    company_id: int,
    outbox_id: int,
    status: str,
    error: str | None = None,
    retry_at: str | None = None,
) -> None:
    resolved_company_id = require_company_id(company_id, context="automation outbox finish")
    execute(
        """
        UPDATE automation_outbox
        SET status = ?,
            attempts = attempts + 1,
            last_error = ?,
            available_at = COALESCE(?, available_at),
            lease_owner = NULL,
            lease_expires_at = NULL,
            dispatched_at = CASE WHEN ? = 'dispatched' THEN CURRENT_TIMESTAMP ELSE dispatched_at END,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND company_id = ?
        """,
        (status, error, retry_at, status, outbox_id, resolved_company_id),
    )


def list_outbox_entries(
    *,
    company_id: int,
    status: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    resolved_company_id = require_company_id(company_id, context="automation outbox listing")
    status_condition, status_params = optional_equals("status", status)
    return fetch_all(
        f"""
        SELECT id, company_id, event_id, status, attempts, last_error,
               available_at, dispatched_at, created_at, updated_at
        FROM automation_outbox
        WHERE company_id = ?
          AND {status_condition}
        ORDER BY id DESC
        LIMIT ?
        """,
        (resolved_company_id, *status_params, limit),
    )


# --- execuções ---------------------------------------------------------------------------


def create_execution(
    *,
    company_id: int,
    workflow_id: int,
    version_id: int,
    enrollment_key: str,
    event_id: int | None,
    context: dict[str, Any],
    current_node_id: str | None,
) -> int | None:
    """Cria a execução, ou devolve `None` se a inscrição já existir.

    O índice único em `(company_id, workflow_id, enrollment_key)` é o que
    impede que a reentrega do mesmo evento crie uma segunda execução.
    """
    resolved_company_id = require_company_id(company_id, context="automation execution create")
    existing = fetch_one(
        """
        SELECT id FROM automation_executions
        WHERE company_id = ? AND workflow_id = ? AND enrollment_key = ?
        """,
        (resolved_company_id, workflow_id, enrollment_key),
    )
    if existing:
        return None

    try:
        return execute(
            """
            INSERT INTO automation_executions (
                company_id, workflow_id, version_id, event_id,
                enrollment_key, status, current_node_id, context_json
            )
            VALUES (?, ?, ?, ?, ?, 'queued', ?, ?)
            """,
            (
                resolved_company_id,
                workflow_id,
                version_id,
                event_id,
                enrollment_key,
                current_node_id,
                json.dumps(context, ensure_ascii=False),
            ),
        )
    except Exception as exc:
        # Corrida entre dois workers: o índice único decide. Quem perdeu
        # simplesmente não cria nada.
        if "UNIQUE" in str(exc).upper() or "DUPLICATE" in str(exc).upper():
            return None
        raise


def get_execution(*, company_id: int, execution_id: int) -> dict[str, Any] | None:
    resolved_company_id = require_company_id(company_id, context="automation execution read")
    row = fetch_one(
        f"""
        SELECT {_EXECUTION_COLUMNS} FROM automation_executions
        WHERE company_id = ? AND id = ?
        """,
        (resolved_company_id, execution_id),
    )
    return {**(normalize_row(row) or {}), "context": _loads(row.get("context_json"), {})} if row else None


def list_executions(
    *,
    company_id: int,
    workflow_id: int | None = None,
    status: str | None = None,
    limit: int = 50,
    ) -> list[dict[str, Any]]:
    resolved_company_id = require_company_id(company_id, context="automation execution listing")
    workflow_condition, workflow_params = optional_equals("workflow_id", workflow_id)
    status_condition, status_params = optional_equals("status", status)
    rows = fetch_all(
        f"""
        SELECT {_EXECUTION_COLUMNS} FROM automation_executions
        WHERE company_id = ?
          AND {workflow_condition}
          AND {status_condition}
        ORDER BY id DESC
        LIMIT ?
        """,
        (resolved_company_id, *workflow_params, *status_params, limit),
    )
    return [{**(normalize_row(row) or {}), "context": _loads(row.get("context_json"), {})} for row in rows]


def update_execution(
    *,
    company_id: int,
    execution_id: int,
    status: str | None = None,
    current_node_id: str | None = None,
    context: dict[str, Any] | None = None,
    failure_reason: str | None = None,
    increment_steps: int = 0,
    finished: bool = False,
    ) -> bool:
    resolved_company_id = require_company_id(company_id, context="automation execution update")
    return (
        execute_rowcount(
            """
            UPDATE automation_executions
            SET status = COALESCE(?, status),
                current_node_id = COALESCE(?, current_node_id),
                context_json = COALESCE(?, context_json),
                failure_reason = COALESCE(?, failure_reason),
                steps_executed = steps_executed + ?,
                finished_at = CASE WHEN ? = 1 THEN CURRENT_TIMESTAMP ELSE finished_at END,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND company_id = ?
            """,
            (
                status,
                current_node_id,
                json.dumps(context, ensure_ascii=False) if context is not None else None,
                failure_reason,
                int(increment_steps),
                int(finished),
                execution_id,
                resolved_company_id,
            ),
        )
        > 0
    )


def append_step(
    *,
    company_id: int,
    execution_id: int,
    node_id: str,
    node_type: str,
    status: str,
    attempt: int = 1,
    chosen_handle: str | None = None,
    detail: dict[str, Any] | None = None,
    failure_reason: str | None = None,
    ) -> int:
    resolved_company_id = require_company_id(company_id, context="automation step write")
    row = fetch_one(
        """
        SELECT MAX(sequence) AS max_sequence FROM automation_step_executions
        WHERE company_id = ? AND execution_id = ?
        """,
        (resolved_company_id, execution_id),
    )
    sequence = int((row or {}).get("max_sequence") or 0) + 1
    return execute(
        """
        INSERT INTO automation_step_executions (
            company_id, execution_id, sequence, node_id, node_type, status,
            attempt, chosen_handle, detail_json, failure_reason, finished_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (
            resolved_company_id,
            execution_id,
            sequence,
            node_id,
            node_type,
            status,
            int(attempt),
            chosen_handle,
            json.dumps(detail or {}, ensure_ascii=False),
            failure_reason,
        ),
    )


def list_steps(*, company_id: int, execution_id: int) -> list[dict[str, Any]]:
    resolved_company_id = require_company_id(company_id, context="automation step listing")
    rows = fetch_all(
        """
        SELECT id, company_id, execution_id, sequence, node_id, node_type, status,
               attempt, chosen_handle, detail_json, failure_reason, started_at, finished_at
        FROM automation_step_executions
        WHERE company_id = ? AND execution_id = ?
        ORDER BY sequence ASC
        """,
        (resolved_company_id, execution_id),
    )
    return [{**(normalize_row(row) or {}), "detail": _loads(row.get("detail_json"), {})} for row in rows]


# --- jobs duráveis -------------------------------------------------------------------------


def enqueue_job(
    *,
    company_id: int,
    execution_id: int,
    job_kind: str,
    node_id: str | None = None,
    run_at: str | None = None,
    max_attempts: int = 5,
    payload: dict[str, Any] | None = None,
    ) -> int:
    resolved_company_id = require_company_id(company_id, context="automation job enqueue")
    return execute(
        """
        INSERT INTO automation_jobs (
            company_id, execution_id, job_kind, node_id, status,
            max_attempts, run_at, payload_json
        )
        VALUES (?, ?, ?, ?, 'queued', ?, COALESCE(?, CURRENT_TIMESTAMP), ?)
        """,
        (
            resolved_company_id,
            execution_id,
            job_kind,
            node_id,
            int(max_attempts),
            run_at,
            json.dumps(payload or {}, ensure_ascii=False),
        ),
    )


def claim_job(*, lease_owner: str, lease_seconds: int = 60) -> dict[str, Any] | None:
    """Reserva um job pronto para rodar. Atômico nos dois backends."""
    lease_until = _iso(datetime.now(UTC) + timedelta(seconds=lease_seconds))
    row = _claim_one(
        table="automation_jobs",
        lease_owner=lease_owner,
        lease_until=lease_until,
        ready_column="run_at",
        claimable_status="queued",
    )
    return {**(normalize_row(row) or {}), "payload": _loads(row.get("payload_json"), {})} if row else None


_CLAIM_MAX_ATTEMPTS = 5


def _claim_one(
    *,
    table: str,
    lease_owner: str,
    lease_until: str,
    ready_column: str,
    claimable_status: str,
    exclude_ids: frozenset[int] = frozenset(),
) -> dict[str, Any] | None:
    """Claim atômico de uma linha pronta.

    `table` e `ready_column` vêm de constantes deste módulo, nunca de entrada
    do usuário, e ainda assim passam por `assert_identifier` antes de serem
    interpolados: um parâmetro é indistinguível de dado do chamador para quem
    lê o código, e `tests/test_dynamic_sql_guard.py` rejeita a interpolação
    que não se prova. `claimable_status` é **valor** e vai em placeholder.

    A linha é escolhida e reservada sob guarda de `status = ?`: quem perde a
    corrida atualiza zero linhas. Em caso de disputa, tenta a próxima
    candidata em vez de desistir — desistir faria um worker parar de trabalhar
    mesmo com a fila cheia.
    """
    safe_table = assert_identifier(table, kind="table")
    safe_ready = assert_identifier(ready_column, kind="column")

    for _ in range(_CLAIM_MAX_ATTEMPTS):
        with db_transaction() as connection:
            cursor = connection.cursor()
            now = now_iso()

            if is_postgres():
                # SKIP LOCKED é a primitiva certa: réplicas concorrentes pulam
                # a linha já travada em vez de enfileirar atrás dela.
                cursor.execute(
                    f"""
                    UPDATE {safe_table}
                    SET status = 'running',
                        lease_owner = ?,
                        lease_expires_at = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = (
                        SELECT id FROM {safe_table}
                        WHERE status = ?
                          AND {safe_ready} <= ?
                        ORDER BY {safe_ready} ASC, id ASC
                        FOR UPDATE SKIP LOCKED
                        LIMIT 1
                    )
                    RETURNING *
                    """,
                    (lease_owner, lease_until, claimable_status, now),
                )
                row = cursor.fetchone()
                if row is None:
                    return None
                claimed = dict(row)
                if int(claimed["id"]) in exclude_ids:
                    continue
                return claimed

            # SQLite: escolhe a candidata e reserva com o status como guarda.
            cursor.execute(
                f"""
                SELECT id FROM {safe_table}
                WHERE status = ?
                  AND {safe_ready} <= ?
                ORDER BY {safe_ready} ASC, id ASC
                LIMIT 1
                """,
                (claimable_status, now),
            )
            candidate = cursor.fetchone()
            if candidate is None:
                return None

            candidate_id = int(dict(candidate)["id"])
            if candidate_id in exclude_ids:
                # Já entregue nesta rodada; tenta a próxima candidata.
                cursor.execute(
                    f"""
                    SELECT id FROM {safe_table}
                    WHERE status = ?
                      AND {safe_ready} <= ?
                      AND id > ?
                    ORDER BY {safe_ready} ASC, id ASC
                    LIMIT 1
                    """,
                    (claimable_status, now, candidate_id),
                )
                nxt = cursor.fetchone()
                if nxt is None:
                    return None
                candidate_id = int(dict(nxt)["id"])

            cursor.execute(
                f"""
                UPDATE {safe_table}
                SET status = 'running',
                    lease_owner = ?,
                    lease_expires_at = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                  AND status = ?
                """,
                (lease_owner, lease_until, candidate_id, claimable_status),
            )
            if cursor.rowcount != 1:
                # Outro worker levou esta linha entre o SELECT e o UPDATE.
                continue

            cursor.execute(f"SELECT * FROM {safe_table} WHERE id = ?", (candidate_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    return None


def finish_job(
    *,
    company_id: int,
    job_id: int,
    status: str,
    error: str | None = None,
    run_at: str | None = None,
) -> None:
    resolved_company_id = require_company_id(company_id, context="automation job finish")
    execute(
        """
        UPDATE automation_jobs
        SET status = ?,
            attempts = attempts + 1,
            last_error = ?,
            run_at = COALESCE(?, run_at),
            lease_owner = NULL,
            lease_expires_at = NULL,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND company_id = ?
        """,
        (status, error, run_at, job_id, resolved_company_id),
    )


def get_job(*, company_id: int, job_id: int) -> dict[str, Any] | None:
    resolved_company_id = require_company_id(company_id, context="automation job read")
    row = fetch_one(
        f"SELECT {_JOB_COLUMNS} FROM automation_jobs WHERE company_id = ? AND id = ?",
        (resolved_company_id, job_id),
    )
    return {**(normalize_row(row) or {}), "payload": _loads(row.get("payload_json"), {})} if row else None


def list_jobs(
    *,
    company_id: int,
    execution_id: int | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    resolved_company_id = require_company_id(company_id, context="automation job listing")
    execution_condition, execution_params = optional_equals("execution_id", execution_id)
    status_condition, status_params = optional_equals("status", status)
    rows = fetch_all(
        f"""
        SELECT {_JOB_COLUMNS} FROM automation_jobs
        WHERE company_id = ?
          AND {execution_condition}
          AND {status_condition}
        ORDER BY id DESC
        LIMIT ?
        """,
        (resolved_company_id, *execution_params, *status_params, limit),
    )
    return [{**(normalize_row(row) or {}), "payload": _loads(row.get("payload_json"), {})} for row in rows]


def recover_expired_leases(*, older_than_iso: str | None = None) -> int:
    """Devolve à fila os jobs cujo lease venceu.

    Um worker que morreu no meio do passo deixa o job em `running` com lease
    vencido. Sem esta recuperação, a execução ficaria parada para sempre.
    """
    cutoff = older_than_iso or now_iso()
    return execute_rowcount(
        """
        UPDATE automation_jobs
        SET status = 'queued',
            lease_owner = NULL,
            lease_expires_at = NULL,
            last_error = 'lease_expired',
            updated_at = CURRENT_TIMESTAMP
        WHERE status = 'running'
          AND lease_expires_at IS NOT NULL
          AND lease_expires_at <= ?
        """,
        (cutoff,),
    )


def request_job_cancellation(*, company_id: int, execution_id: int) -> int:
    """Cancelamento cooperativo: o job em voo termina e não reagenda."""
    resolved_company_id = require_company_id(company_id, context="automation job cancel")
    return execute_rowcount(
        """
        UPDATE automation_jobs
        SET cancel_requested = 1, updated_at = CURRENT_TIMESTAMP
        WHERE company_id = ? AND execution_id = ? AND status IN ('queued', 'running')
        """,
        (resolved_company_id, execution_id),
    )


# --- efeitos --------------------------------------------------------------------------------


def reserve_effect(
    *,
    company_id: int,
    execution_id: int,
    effect_key: str,
    effect_kind: str,
    node_id: str,
    occurrence: int,
    request: dict[str, Any],
) -> dict[str, Any]:
    """Reserva o efeito no ledger antes de qualquer chamada externa.

    Devolve `{"reserved": bool, "effect": row, "outcome": str}`. O estado
    anterior decide, e os três casos são diferentes:

    - `succeeded`: **não** reserva. O efeito já aconteceu; repetir seria o
      envio duplicado que o ledger existe para impedir.
    - `failed`: reserva de novo. O adapter reportou falha, então por contrato
      a ação externa não se completou e um retry é seguro.
    - `pending`: **não** reserva. Uma tentativa anterior morreu entre a
      reserva e o resultado, e não há como saber se a ação externa ocorreu.
      Repetir arriscaria duplicar; o efeito fica marcado como indeterminado
      para inspeção humana. É o limite honesto da idempotência aqui.
    """
    resolved_company_id = require_company_id(company_id, context="automation effect reserve")
    existing = fetch_one(
        f"""
        SELECT {_EFFECT_COLUMNS}
        FROM automation_effects
        WHERE company_id = ? AND effect_key = ?
        """,
        (resolved_company_id, effect_key),
    )
    if existing:
        previous_status = str(existing.get("status") or "")
        hydrated = {**existing, "result": _loads(existing.get("result_json"), None)}

        if previous_status == "failed":
            execute(
                """
                UPDATE automation_effects
                SET status = 'pending',
                    failure_reason = NULL,
                    completed_at = NULL,
                    request_json = ?
                WHERE id = ? AND company_id = ?
                """,
                (
                    json.dumps(request, ensure_ascii=False),
                    int(existing["id"]),
                    resolved_company_id,
                ),
            )
            return {
                "reserved": True,
                "outcome": "retried_after_failure",
                "effect": {**hydrated, "status": "pending"},
            }

        if previous_status == "pending":
            execute(
                """
                UPDATE automation_effects
                SET failure_reason = 'indeterminate_previous_attempt'
                WHERE id = ? AND company_id = ? AND status = 'pending'
                """,
                (int(existing["id"]), resolved_company_id),
            )
            return {"reserved": False, "outcome": "indeterminate", "effect": hydrated}

        return {"reserved": False, "outcome": "already_succeeded", "effect": hydrated}

    try:
        effect_id = execute(
            """
            INSERT INTO automation_effects (
                company_id, execution_id, effect_key, effect_kind,
                node_id, occurrence, status, request_json
            )
            VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (
                resolved_company_id,
                execution_id,
                effect_key,
                effect_kind,
                node_id,
                int(occurrence),
                json.dumps(request, ensure_ascii=False),
            ),
        )
    except Exception as exc:
        if "UNIQUE" in str(exc).upper() or "DUPLICATE" in str(exc).upper():
            # Outro worker reservou primeiro: quem perdeu não executa.
            row = fetch_one(
                f"""
                SELECT {_EFFECT_COLUMNS}
                FROM automation_effects
                WHERE company_id = ? AND effect_key = ?
                """,
                (resolved_company_id, effect_key),
            )
            return {"reserved": False, "outcome": "claimed_by_another_worker", "effect": row}
        raise

    return {
        "reserved": True,
        "outcome": "reserved",
        "effect": {"id": effect_id, "effect_key": effect_key, "status": "pending"},
    }


def complete_effect(
    *,
    company_id: int,
    effect_id: int,
    status: str,
    result: dict[str, Any] | None = None,
    failure_reason: str | None = None,
) -> None:
    resolved_company_id = require_company_id(company_id, context="automation effect complete")
    execute(
        """
        UPDATE automation_effects
        SET status = ?,
            result_json = ?,
            failure_reason = ?,
            completed_at = CURRENT_TIMESTAMP
        WHERE id = ? AND company_id = ?
        """,
        (
            status,
            json.dumps(result, ensure_ascii=False) if result is not None else None,
            failure_reason,
            effect_id,
            resolved_company_id,
        ),
    )


def list_effects(*, company_id: int, execution_id: int) -> list[dict[str, Any]]:
    resolved_company_id = require_company_id(company_id, context="automation effect listing")
    rows = fetch_all(
        """
        SELECT id, company_id, execution_id, effect_key, effect_kind, node_id,
               occurrence, status, result_json, failure_reason, created_at, completed_at
        FROM automation_effects
        WHERE company_id = ? AND execution_id = ?
        ORDER BY id ASC
        """,
        (resolved_company_id, execution_id),
    )
    return [{**(normalize_row(row) or {}), "result": _loads(row.get("result_json"), None)} for row in rows]


# --- portões humanos ---------------------------------------------------------------------------


def create_gate(
    *,
    company_id: int,
    execution_id: int,
    node_id: str,
    occurrence: int,
    title: str,
    summary: str | None,
    payload: dict[str, Any],
    expires_at: str | None = None,
) -> int | None:
    """Cria o portão. `None` quando ele já existe (retry do mesmo passo)."""
    resolved_company_id = require_company_id(company_id, context="automation gate create")
    existing = fetch_one(
        """
        SELECT id FROM automation_approval_gates
        WHERE company_id = ? AND execution_id = ? AND node_id = ? AND occurrence = ?
        """,
        (resolved_company_id, execution_id, node_id, int(occurrence)),
    )
    if existing:
        return None

    return execute(
        """
        INSERT INTO automation_approval_gates (
            company_id, execution_id, node_id, occurrence, status,
            title, summary, payload_json, expires_at
        )
        VALUES (?, ?, ?, ?, 'awaiting_human', ?, ?, ?, ?)
        """,
        (
            resolved_company_id,
            execution_id,
            node_id,
            int(occurrence),
            title,
            summary,
            json.dumps(payload, ensure_ascii=False),
            expires_at,
        ),
    )


def get_gate(*, company_id: int, gate_id: int) -> dict[str, Any] | None:
    resolved_company_id = require_company_id(company_id, context="automation gate read")
    row = fetch_one(
        f"SELECT {_GATE_COLUMNS} FROM automation_approval_gates WHERE company_id = ? AND id = ?",
        (resolved_company_id, gate_id),
    )
    return {**(normalize_row(row) or {}), "payload": _loads(row.get("payload_json"), {})} if row else None


def get_open_gate_for_node(
    *,
    company_id: int,
    execution_id: int,
    node_id: str,
    occurrence: int,
) -> dict[str, Any] | None:
    resolved_company_id = require_company_id(company_id, context="automation gate read")
    return fetch_one(
        f"""
        SELECT {_GATE_COLUMNS} FROM automation_approval_gates
        WHERE company_id = ? AND execution_id = ? AND node_id = ? AND occurrence = ?
        """,
        (resolved_company_id, execution_id, node_id, int(occurrence)),
    )


def list_gates(
    *,
    company_id: int,
    status: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    resolved_company_id = require_company_id(company_id, context="automation gate listing")
    status_condition, status_params = optional_equals("status", status)
    rows = fetch_all(
        f"""
        SELECT {_GATE_COLUMNS} FROM automation_approval_gates
        WHERE company_id = ?
          AND {status_condition}
        ORDER BY id DESC
        LIMIT ?
        """,
        (resolved_company_id, *status_params, limit),
    )
    return [{**(normalize_row(row) or {}), "payload": _loads(row.get("payload_json"), {})} for row in rows]


def claim_gate(
    *,
    company_id: int,
    gate_id: int,
    claimed_by: str,
    lease_seconds: int = 300,
) -> bool:
    """Claim atômico do portão por um operador.

    `WHERE status = 'awaiting_human'` é o que garante um dono só: dois
    operadores clicando ao mesmo tempo produzem um vencedor e um 409.
    """
    resolved_company_id = require_company_id(company_id, context="automation gate claim")
    lease_until = _iso(datetime.now(UTC) + timedelta(seconds=lease_seconds))
    return (
        execute_rowcount(
            """
            UPDATE automation_approval_gates
            SET status = 'processing',
                claimed_by = ?,
                claimed_at = CURRENT_TIMESTAMP,
                lease_expires_at = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND company_id = ?
              AND status = 'awaiting_human'
            """,
            (claimed_by, lease_until, gate_id, resolved_company_id),
        )
        > 0
    )


def release_gate(
    *,
    company_id: int,
    gate_id: int,
    status: str,
    decision: str | None = None,
    decided_by: str | None = None,
    decision_note: str | None = None,
) -> bool:
    """Conclui o portão. Idempotente: soltar duas vezes não muda a decisão."""
    resolved_company_id = require_company_id(company_id, context="automation gate release")
    return (
        execute_rowcount(
            """
            UPDATE automation_approval_gates
            SET status = ?,
                decision = COALESCE(decision, ?),
                decided_by = COALESCE(decided_by, ?),
                decision_note = COALESCE(decision_note, ?),
                decided_at = COALESCE(decided_at, CURRENT_TIMESTAMP),
                lease_expires_at = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND company_id = ?
              AND status IN ('awaiting_human', 'processing')
            """,
            (status, decision, decided_by, decision_note, gate_id, resolved_company_id),
        )
        > 0
    )


def recover_expired_gate_claims(*, older_than_iso: str | None = None) -> int:
    """Portão reservado e abandonado volta para a fila."""
    cutoff = older_than_iso or now_iso()
    return execute_rowcount(
        """
        UPDATE automation_approval_gates
        SET status = 'awaiting_human',
            claimed_by = NULL,
            claimed_at = NULL,
            lease_expires_at = NULL,
            updated_at = CURRENT_TIMESTAMP
        WHERE status = 'processing'
          AND lease_expires_at IS NOT NULL
          AND lease_expires_at <= ?
        """,
        (cutoff,),
    )


def expire_overdue_gates(*, now_iso_value: str | None = None) -> int:
    cutoff = now_iso_value or now_iso()
    return execute_rowcount(
        """
        UPDATE automation_approval_gates
        SET status = 'expired', updated_at = CURRENT_TIMESTAMP
        WHERE status IN ('awaiting_human', 'processing')
          AND expires_at IS NOT NULL
          AND expires_at <= ?
        """,
        (cutoff,),
    )
