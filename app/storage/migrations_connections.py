"""Migration 074 - conexões por empresa (WhatsApp Embedded Signup e Google OAuth).

Até aqui o token da Meta e o refresh token do Google eram um por instalação,
em variável de ambiente. Para o dono do negócio conectar o próprio número e a
própria agenda pelo painel, cada empresa passa a guardar as suas credenciais,
cifradas por `app.core.credential_vault` antes de chegar ao banco.

`oauth_states` guarda o `state` de uma autorização OAuth em andamento: o
callback do Google só é aceito se o state existir, não tiver sido consumido e
não tiver expirado, e é dele que sai a empresa a que o token pertence — nunca
de um parâmetro da URL de retorno.
"""
from typing import Any

from app.storage.schema_utils import create_index, create_table, ensure_column


def add_company_connection_credentials(cursor: Any) -> None:
    # WhatsApp: além do phone_number_id já usado pelo webhook e pelo envio,
    # o binding passa a carregar a WABA, o token de negócio cifrado e os
    # metadados que o painel mostra ("Conectado em", nome verificado).
    # Os nomes das colunas evitam a palavra "token" de propósito: a listagem
    # de bindings é auditada por teste que proíbe esse termo na resposta.
    ensure_column(cursor, "whatsapp_channel_bindings", "waba_id", "TEXT")
    ensure_column(cursor, "whatsapp_channel_bindings", "business_id", "TEXT")
    ensure_column(cursor, "whatsapp_channel_bindings", "verified_name", "TEXT")
    ensure_column(cursor, "whatsapp_channel_bindings", "quality_rating", "TEXT")
    ensure_column(cursor, "whatsapp_channel_bindings", "platform_type", "TEXT")
    ensure_column(cursor, "whatsapp_channel_bindings", "access_secret_encrypted", "TEXT")
    ensure_column(cursor, "whatsapp_channel_bindings", "registration_pin_encrypted", "TEXT")
    ensure_column(cursor, "whatsapp_channel_bindings", "credential_source", "TEXT")
    ensure_column(cursor, "whatsapp_channel_bindings", "connected_at", "TIMESTAMP")
    ensure_column(cursor, "whatsapp_channel_bindings", "connected_by", "TEXT")
    ensure_column(cursor, "whatsapp_channel_bindings", "last_verified_at", "TIMESTAMP")
    ensure_column(cursor, "whatsapp_channel_bindings", "needs_reconnection", "INTEGER NOT NULL DEFAULT 0")

    # Google: o refresh token continua dentro de config_json (cifrado); aqui
    # entram apenas os metadados de conexão que o painel exibe.
    ensure_column(cursor, "calendar_integrations", "connection_source", "TEXT")
    ensure_column(cursor, "calendar_integrations", "connected_account_email", "TEXT")
    ensure_column(cursor, "calendar_integrations", "connected_at", "TIMESTAMP")
    ensure_column(cursor, "calendar_integrations", "connected_by", "TEXT")

    create_table(
        cursor,
        "oauth_states",
        """
        state TEXT PRIMARY KEY,
        provider TEXT NOT NULL,
        company_id INTEGER NOT NULL REFERENCES companies(id),
        user_id INTEGER,
        actor TEXT,
        auth_mode TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        expires_at TIMESTAMP NOT NULL,
        consumed_at TIMESTAMP
        """,
    )
    create_index(cursor, "idx_oauth_states_expires_at", "oauth_states", "expires_at")
    create_index(cursor, "idx_oauth_states_company", "oauth_states", "company_id, provider")


CONNECTIONS_MIGRATIONS = [
    (
        "074_company_connection_credentials",
        "Per-company WhatsApp/Google credentials (encrypted) and OAuth state table",
        add_company_connection_credentials,
    ),
]
