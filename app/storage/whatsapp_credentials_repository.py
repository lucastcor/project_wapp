"""Credenciais WhatsApp por empresa (colunas da migration 074).

`whatsapp_channel_bindings` já resolvia o tenant de um evento do Meta e o
número de saída de cada empresa. Com o Embedded Signup o mesmo vínculo passa
a carregar o token de negócio da empresa e o PIN de registro — cifrados por
`app.core.credential_vault` antes de chegar aqui — mais os metadados que o
painel mostra ("Conectado em", nome verificado, qualidade).

Este módulo é a única leitura das colunas `*_encrypted`. A listagem de
bindings do `RuntimeRepository` (usada pela API pública e auditada por teste
que proíbe a palavra "token" na resposta) continua sem elas de propósito:
quem precisa do segredo é o provider no momento do envio, e mais ninguém.

SQL com `?` como o resto do repositório; a camada de conexão traduz para o
PostgreSQL.
"""

from __future__ import annotations

from typing import Any

from app.storage.db import db_session, db_transaction

# Colunas devolvidas por `get_credentials_for_company`. A ordem importa só
# para leitura humana; o dict resultante é normalizado por nome.
_CREDENTIAL_COLUMNS = (
    "id",
    "company_id",
    "phone_number_id",
    "display_phone_number",
    "active",
    "waba_id",
    "business_id",
    "verified_name",
    "quality_rating",
    "platform_type",
    "access_secret_encrypted",
    "registration_pin_encrypted",
    "credential_source",
    "connected_at",
    "connected_by",
    "last_verified_at",
    "needs_reconnection",
    "created_at",
    "updated_at",
)
_TIMESTAMP_COLUMNS = ("connected_at", "last_verified_at", "created_at", "updated_at")


def _normalize_row(row: Any) -> dict[str, Any]:
    """Tipos independentes de backend: SQLite devolve texto, Postgres datetime."""
    binding = dict(row)
    for column in _TIMESTAMP_COLUMNS:
        value = binding.get(column)
        binding[column] = str(value) if value is not None else None
    binding["id"] = int(binding["id"])
    binding["company_id"] = int(binding["company_id"])
    binding["active"] = int(binding.get("active") or 0)
    binding["needs_reconnection"] = int(binding.get("needs_reconnection") or 0)
    return binding


def get_credentials_for_company(company_id: int) -> dict[str, Any] | None:
    """O binding ativo único da empresa, com as colunas de credencial.

    Zero ou mais de um binding ativo devolve `None`: as duas situações
    bloqueiam o envio, e o chamador não deve escolher um "por acaso".
    """
    columns = ", ".join(_CREDENTIAL_COLUMNS)
    with db_session() as connection:
        cursor = connection.cursor()
        cursor.execute(
            f"""
            SELECT {columns}
            FROM whatsapp_channel_bindings
            WHERE company_id = ?
              AND active = 1
            ORDER BY id ASC
            """,
            (int(company_id),),
        )
        rows = cursor.fetchall()
    if len(rows) != 1:
        return None
    return _normalize_row(rows[0])


def store_connection(
    company_id: int,
    *,
    phone_number_id: str,
    display_phone_number: str | None,
    waba_id: str | None,
    business_id: str | None,
    verified_name: str | None,
    quality_rating: str | None,
    platform_type: str | None,
    access_secret_encrypted: str,
    registration_pin_encrypted: str | None,
    credential_source: str,
    connected_by: str | None,
) -> dict[str, Any] | None:
    """Grava a conexão da empresa e a torna o único binding ativo dela.

    Reutiliza `upsert_whatsapp_binding`, que é quem conhece a regra de
    unicidade do número entre empresas: devolve `None` quando o número está
    ativo em outra empresa, e o chamador transforma isso em 409 em vez de
    apontar o tráfego de um tenant para outro.

    Os segredos chegam já cifrados; este módulo nunca vê um token em claro.
    Os outros bindings da empresa ficam inativos e perdem o token: um token
    válido guardado numa linha que ninguém mais usa é credencial esquecida,
    não histórico.
    """
    from app.storage.repository import RuntimeRepository

    binding = RuntimeRepository().upsert_whatsapp_binding(
        company_id=int(company_id),
        phone_number_id=phone_number_id,
        display_phone_number=display_phone_number,
        active=True,
        updated_by=connected_by,
    )
    if binding is None:
        return None
    binding_id = int(binding["id"])

    with db_transaction() as connection:
        cursor = connection.cursor()
        cursor.execute(
            """
            UPDATE whatsapp_channel_bindings
            SET waba_id = ?,
                business_id = ?,
                verified_name = ?,
                quality_rating = ?,
                platform_type = ?,
                access_secret_encrypted = ?,
                registration_pin_encrypted = ?,
                credential_source = ?,
                connected_at = CURRENT_TIMESTAMP,
                connected_by = ?,
                last_verified_at = CURRENT_TIMESTAMP,
                needs_reconnection = 0,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND company_id = ?
            """,
            (
                waba_id,
                business_id,
                verified_name,
                quality_rating,
                platform_type,
                access_secret_encrypted,
                registration_pin_encrypted,
                credential_source,
                connected_by,
                binding_id,
                int(company_id),
            ),
        )
        # Um número por empresa: o envio exige exatamente um binding ativo, e
        # um número antigo que ficasse ativo bloquearia a empresa recém
        # conectada. O histórico permanece (active = 0), mas sem o token — a
        # linha desativada não tem mais quem a use. O PIN fica: ele pertence
        # ao número na Meta (ver `clear_connection`).
        cursor.execute(
            """
            UPDATE whatsapp_channel_bindings
            SET active = 0,
                access_secret_encrypted = NULL,
                needs_reconnection = 0,
                updated_at = CURRENT_TIMESTAMP,
                updated_by = ?
            WHERE company_id = ?
              AND active = 1
              AND id <> ?
            """,
            (connected_by, int(company_id), binding_id),
        )

    return get_credentials_for_company(int(company_id))


def clear_connection(company_id: int, binding_id: int) -> bool:
    """Desconecta: desativa o binding e apaga o token de negócio.

    `credential_source` fica para o histórico dizer como aquele número foi
    conectado; sem o token a linha não serve para enviar nada.

    O PIN de registro fica cifrado na linha de propósito. Ele não dá acesso a
    nada sozinho (registrar exige um token do número), mas é a verificação em
    duas etapas que a Meta mantém no número mesmo depois da desconexão: a
    próxima conexão desse número — nesta ou em outra empresa — só consegue
    registrá-lo de novo com o mesmo PIN, e o dono não tem outro lugar de
    onde recuperá-lo.
    """
    with db_transaction() as connection:
        cursor = connection.cursor()
        cursor.execute(
            """
            UPDATE whatsapp_channel_bindings
            SET active = 0,
                access_secret_encrypted = NULL,
                needs_reconnection = 0,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND company_id = ?
            """,
            (int(binding_id), int(company_id)),
        )
        return int(cursor.rowcount or 0) > 0


def get_registration_pin_encrypted(phone_number_id: str) -> str | None:
    """O último PIN cifrado gravado para este número, em qualquer empresa.

    A verificação em duas etapas é do número na Meta, não do tenant: quando
    o número volta a ser conectado (reconexão, ou migração entre empresas
    desta instalação), o `register` precisa do PIN que já está lá, senão a
    Meta recusa com 133005. Sem PIN gravado o serviço gera um novo.
    """
    with db_session() as connection:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT registration_pin_encrypted
            FROM whatsapp_channel_bindings
            WHERE phone_number_id = ?
              AND registration_pin_encrypted IS NOT NULL
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            (str(phone_number_id),),
        )
        row = cursor.fetchone()
    if row is None:
        return None
    value = dict(row).get("registration_pin_encrypted")
    return str(value) if value else None


def list_bindings_with_token(company_id: int) -> list[dict[str, Any]]:
    """Toda linha da empresa que ainda guarda um token de negócio, ativa ou não.

    "Desconectar" precisa alcançar também as linhas desativadas por outros
    caminhos (o endpoint legado de estado do vínculo não apaga segredos):
    um token válido que ficou numa linha inativa continua sendo credencial
    da empresa até alguém apagá-lo.
    """
    columns = ", ".join(_CREDENTIAL_COLUMNS)
    with db_session() as connection:
        cursor = connection.cursor()
        cursor.execute(
            f"""
            SELECT {columns}
            FROM whatsapp_channel_bindings
            WHERE company_id = ?
              AND access_secret_encrypted IS NOT NULL
            ORDER BY id ASC
            """,
            (int(company_id),),
        )
        rows = cursor.fetchall()
    return [_normalize_row(row) for row in rows]


def waba_in_use_elsewhere(waba_id: str, *, excluding_binding_id: int) -> bool:
    """Outra linha ativa com token ainda depende desta WABA?

    A inscrição do app nos webhooks é por WABA, e uma WABA pode ter vários
    números (agência com um número por cliente). Cancelar a inscrição ao
    desconectar um número derrubaria o recebimento dos outros; quem chama
    só cancela quando este era o último.
    """
    with db_session() as connection:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT COUNT(*) AS total
            FROM whatsapp_channel_bindings
            WHERE waba_id = ?
              AND active = 1
              AND access_secret_encrypted IS NOT NULL
              AND id <> ?
            """,
            (str(waba_id), int(excluding_binding_id)),
        )
        row = cursor.fetchone()
    if row is None:
        return False
    value = dict(row).get("total") if not isinstance(row, dict) else row.get("total")
    return int(value or 0) > 0


def mark_needs_reconnection(binding_id: int, flag: bool) -> None:
    """Sinaliza que a Meta recusou o token (código 190) para o painel avisar."""
    with db_transaction() as connection:
        cursor = connection.cursor()
        cursor.execute(
            """
            UPDATE whatsapp_channel_bindings
            SET needs_reconnection = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (1 if flag else 0, int(binding_id)),
        )


def touch_last_verified(binding_id: int) -> None:
    """Registra que a Meta confirmou o acesso ao número agora."""
    with db_transaction() as connection:
        cursor = connection.cursor()
        cursor.execute(
            """
            UPDATE whatsapp_channel_bindings
            SET last_verified_at = CURRENT_TIMESTAMP,
                needs_reconnection = 0
            WHERE id = ?
            """,
            (int(binding_id),),
        )


def count_connected_companies() -> int:
    """Quantas empresas têm binding ativo com token próprio.

    Usado pela prontidão do sistema: sem `WHATSAPP_PHONE_NUMBER_ID` global, o
    que prova que o canal funciona é alguma empresa ter conectado pelo painel.
    """
    with db_session() as connection:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT COUNT(DISTINCT company_id) AS total
            FROM whatsapp_channel_bindings
            WHERE active = 1
              AND access_secret_encrypted IS NOT NULL
            """
        )
        row = cursor.fetchone()
    if row is None:
        return 0
    value = dict(row).get("total") if not isinstance(row, dict) else row.get("total")
    return int(value or 0)
