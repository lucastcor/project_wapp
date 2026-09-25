"""Estado OAuth e metadados da conexão Google Agenda de cada empresa (074).

Funções livres sobre `sql_support` (placeholders `?`, linha como `dict`), no
molde de `operation_repository`. Duas responsabilidades:

- `oauth_states`: o `state` de uma autorização em andamento. O callback do
  Google só é aceito se o state existir, não tiver sido consumido e não tiver
  expirado — e é dele que sai a empresa dona do token, nunca da URL de
  retorno. Consumir é compare-and-set numa transação, para que dois callbacks
  com o mesmo state não passem os dois.
- `calendar_integrations`: colunas de conexão (`connection_source`,
  `connected_account_email`, `connected_at`, `connected_by`) que o
  `RuntimeRepository` não seleciona, e o UPDATE parcial que a reconexão usa
  para manter o mesmo `integration_id` (os bookings apontam para ele).

O refresh token nunca passa por aqui em claro: quem grava `config` entrega o
valor já cifrado pelo cofre, e este módulo o trata como texto opaco.
"""
from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from app.storage.sql_support import execute_rowcount, fetch_all, fetch_one, transaction

GOOGLE_CALENDAR_PROVIDER = "google_calendar"
DEFAULT_STATE_TTL_MINUTES = 15

# Sentinela para "campo não informado": `None` é um valor legítimo em várias
# colunas (limpar o e-mail, por exemplo), então não serve como ausência.
_UNSET: Any = object()

_STATE_COLUMNS = (
    "state, provider, company_id, user_id, actor, auth_mode, created_at, expires_at, consumed_at"
)
_INTEGRATION_COLUMNS = (
    "id, company_id, provider, external_calendar_id, config_json, active, created_at, updated_at, "
    "connection_source, connected_account_email, connected_at, connected_by"
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _parse_timestamp(value: Any) -> datetime | None:
    """Aceita `datetime` (PostgreSQL) ou texto ISO (SQLite); naive é UTC."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _decode_integration(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    config_json = item.pop("config_json", None)
    try:
        item["config"] = json.loads(config_json) if config_json else {}
    except json.JSONDecodeError:
        item["config"] = {"raw": config_json}
    return item


# ── oauth_states ──────────────────────────────────────────────────────


def create_state(
    *,
    provider: str,
    company_id: int,
    user_id: int | None,
    actor: str | None,
    auth_mode: str | None,
    ttl_minutes: int = DEFAULT_STATE_TTL_MINUTES,
) -> str:
    """Registra um state novo e devolve o valor a enviar ao provedor."""
    state = secrets.token_urlsafe(32)
    now = _utc_now()
    expires_at = now + timedelta(minutes=int(ttl_minutes))
    with transaction() as cursor:
        cursor.execute(
            """
            INSERT INTO oauth_states (
                state, provider, company_id, user_id, actor, auth_mode, created_at, expires_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                state,
                provider,
                int(company_id),
                int(user_id) if user_id is not None else None,
                str(actor) if actor else None,
                str(auth_mode) if auth_mode else None,
                _iso(now),
                _iso(expires_at),
            ),
        )
    return state


def consume_state(state: str, provider: str) -> dict[str, Any] | None:
    """Devolve a linha do state e marca `consumed_at`, tudo numa transação.

    `None` para state inexistente, de outro provedor, já consumido ou
    expirado. O UPDATE condicionado a `consumed_at IS NULL` é o que impede
    dois callbacks concorrentes de consumirem o mesmo state.
    """
    normalized = str(state or "").strip()
    if not normalized:
        return None
    now = _utc_now()
    with transaction() as cursor:
        cursor.execute(
            f"SELECT {_STATE_COLUMNS} FROM oauth_states WHERE state = ? AND provider = ?",
            (normalized, provider),
        )
        row = cursor.fetchone()
        if not row:
            return None
        item = dict(row)
        if item.get("consumed_at") is not None:
            return None
        expires_at = _parse_timestamp(item.get("expires_at"))
        if expires_at is None or expires_at <= now:
            return None
        cursor.execute(
            "UPDATE oauth_states SET consumed_at = ? WHERE state = ? AND consumed_at IS NULL",
            (_iso(now), normalized),
        )
        if int(cursor.rowcount or 0) != 1:
            return None
    item["consumed_at"] = _iso(now)
    item["company_id"] = int(item["company_id"])
    if item.get("user_id") is not None:
        item["user_id"] = int(item["user_id"])
    return item


def purge_expired_states() -> int:
    """Remove states vencidos ou já consumidos; devolve quantos saíram."""
    return execute_rowcount(
        "DELETE FROM oauth_states WHERE expires_at <= ? OR consumed_at IS NOT NULL",
        (_iso(_utc_now()),),
    )


# ── calendar_integrations ─────────────────────────────────────────────


def get_active_google_integration(company_id: int) -> dict[str, Any] | None:
    """Integração Google ativa mais recente da empresa, com as colunas de conexão."""
    row = fetch_one(
        f"""
        SELECT {_INTEGRATION_COLUMNS}
        FROM calendar_integrations
        WHERE company_id = ? AND provider = ? AND active = 1
        ORDER BY id DESC
        LIMIT 1
        """,
        (int(company_id), GOOGLE_CALENDAR_PROVIDER),
    )
    return _decode_integration(row) if row else None


def get_calendar_integration_row(integration_id: int, *, company_id: int) -> dict[str, Any] | None:
    row = fetch_one(
        f"SELECT {_INTEGRATION_COLUMNS} FROM calendar_integrations WHERE id = ? AND company_id = ?",
        (int(integration_id), int(company_id)),
    )
    return _decode_integration(row) if row else None


def get_connection_metadata(company_id: int | None) -> dict[int, dict[str, Any]]:
    """`{integration_id: {connection_source, connected_account_email, ...}}`.

    Existe porque as consultas do `RuntimeRepository` (arquivo compartilhado)
    não selecionam as colunas da migration 074; o serviço de agenda junta isto
    às linhas que já lê para o painel mostrar "Conectado em" e a conta.
    """
    if company_id is None:
        rows = fetch_all(
            """
            SELECT id, connection_source, connected_account_email, connected_at, connected_by
            FROM calendar_integrations
            """
        )
    else:
        rows = fetch_all(
            """
            SELECT id, connection_source, connected_account_email, connected_at, connected_by
            FROM calendar_integrations
            WHERE company_id = ?
            """,
            (int(company_id),),
        )
    result: dict[int, dict[str, Any]] = {}
    for row in rows:
        item = dict(row)
        integration_id = int(item.pop("id"))
        connected_at = item.get("connected_at")
        if isinstance(connected_at, datetime):
            item["connected_at"] = _iso(connected_at)
        result[integration_id] = item
    return result


def deactivate_calendar_integrations(company_id: int, *, except_id: int | None = None) -> int:
    """Desativa as integrações ativas da empresa (menos `except_id`); devolve o total."""
    if except_id is None:
        return execute_rowcount(
            """
            UPDATE calendar_integrations
            SET active = 0, updated_at = CURRENT_TIMESTAMP
            WHERE company_id = ? AND active = 1
            """,
            (int(company_id),),
        )
    return execute_rowcount(
        """
        UPDATE calendar_integrations
        SET active = 0, updated_at = CURRENT_TIMESTAMP
        WHERE company_id = ? AND active = 1 AND id <> ?
        """,
        (int(company_id), int(except_id)),
    )


def update_calendar_integration(
    integration_id: int,
    *,
    company_id: int,
    external_calendar_id: str | None = _UNSET,
    config: dict[str, Any] | None = _UNSET,
    active: bool | int = _UNSET,
    connection_source: str | None = _UNSET,
    connected_account_email: str | None = _UNSET,
    connected_at: str | datetime | None = _UNSET,
    connected_by: str | None = _UNSET,
) -> int:
    """UPDATE parcial: campo não informado mantém o valor atual.

    Lê a linha e regrava todas as colunas com SQL fixo em vez de montar o SET
    dinamicamente — o guard de SQL dinâmico da suíte só aceita identificadores
    provenientes de constantes, e aqui a lista de colunas é sempre a mesma.
    Devolve 0 quando a integração não é da empresa informada.
    """
    with transaction() as cursor:
        cursor.execute(
            f"SELECT {_INTEGRATION_COLUMNS} FROM calendar_integrations WHERE id = ? AND company_id = ?",
            (int(integration_id), int(company_id)),
        )
        row = cursor.fetchone()
        if not row:
            return 0
        current = dict(row)

        next_external_id = (
            current.get("external_calendar_id") if external_calendar_id is _UNSET else external_calendar_id
        )
        next_config_json = (
            current.get("config_json")
            if config is _UNSET
            else json.dumps(config or {}, ensure_ascii=False)
        )
        next_active = int(bool(current.get("active"))) if active is _UNSET else int(bool(active))
        next_source = current.get("connection_source") if connection_source is _UNSET else connection_source
        next_email = (
            current.get("connected_account_email")
            if connected_account_email is _UNSET
            else connected_account_email
        )
        if connected_at is _UNSET:
            next_connected_at = current.get("connected_at")
            if isinstance(next_connected_at, datetime):
                next_connected_at = _iso(next_connected_at)
        elif isinstance(connected_at, datetime):
            next_connected_at = _iso(connected_at)
        else:
            next_connected_at = connected_at
        next_connected_by = current.get("connected_by") if connected_by is _UNSET else connected_by

        cursor.execute(
            """
            UPDATE calendar_integrations
            SET external_calendar_id = ?,
                config_json = ?,
                active = ?,
                connection_source = ?,
                connected_account_email = ?,
                connected_at = ?,
                connected_by = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND company_id = ?
            """,
            (
                next_external_id,
                next_config_json,
                next_active,
                next_source,
                next_email,
                next_connected_at,
                next_connected_by,
                int(integration_id),
                int(company_id),
            ),
        )
        return int(cursor.rowcount or 0)
