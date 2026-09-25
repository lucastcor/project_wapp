"""Migration 072 - posse humana da conversa (P06, H01 a H03).

Contrato: docs/piloto/CONTRATOS.md, "Recuperação e posse humana". Sem linha a
conversa é do bot; a linha registra quem assumiu, por quê e desde quando, com
versão para compare-and-set. Posse é por empresa: o mesmo telefone em outra
empresa continua com o bot dela.
"""
from typing import Any

from app.storage.schema_utils import create_table


def create_conversation_ownership(cursor: Any) -> None:
    create_table(cursor, "conversation_ownerships", """
            company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
            contact_id TEXT NOT NULL,
            owner TEXT NOT NULL DEFAULT 'bot' CHECK (owner IN ('bot', 'human')),
            owner_user_id INTEGER REFERENCES saas_users(id) ON DELETE SET NULL,
            reason TEXT,
            version INTEGER NOT NULL DEFAULT 1,
            taken_at TEXT,
            released_at TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (company_id, contact_id)
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_conversation_ownerships_owner "
        "ON conversation_ownerships (company_id, owner, updated_at)"
    )


OWNERSHIP_MIGRATIONS = [
    (
        "072_conversation_ownership",
        "Explicit human ownership of a conversation per company",
        create_conversation_ownership,
    ),
]
