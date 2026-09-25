"""Migration 071 — WhatsApp: janela de atendimento, consentimento, templates e status.

Contrato: docs/piloto/CONTRATOS.md, "Canal WhatsApp". Tudo aqui é por empresa:
o mesmo telefone em duas empresas tem janela, opt-out, consentimento e status
independentes.
"""
from typing import Any

from app.storage.schema_utils import create_table, ensure_column

DELIVERY_PURPOSES: tuple[str, ...] = (
    "reply",
    "service_followup",
    "reminders",
    "internal_notice",
    "queue_offers",
    "automation",
    "marketing",
)
CONSENT_PURPOSES: tuple[str, ...] = tuple(purpose for purpose in DELIVERY_PURPOSES if purpose != "reply")
DELIVERY_STATUSES: tuple[str, ...] = ("sent", "delivered", "read", "failed")
TEMPLATE_STATUSES: tuple[str, ...] = ("approved", "pending", "rejected", "paused", "disabled")
TEMPLATE_CATEGORIES: tuple[str, ...] = ("utility", "authentication", "marketing")
MAX_TEMPLATE_PARAMETERS = 10
CHANNEL_ID_TABLES = {"contact_consents", "whatsapp_templates", "whatsapp_delivery_events"}


def _sql_list(values: tuple[str, ...]) -> str:
    # Constantes do módulo, nunca dado de chamador.
    return ", ".join(f"'{value}'" for value in values)


def create_channel_delivery(cursor: Any) -> None:
    create_table(cursor, "whatsapp_contact_states", """
            company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
            contact_id TEXT NOT NULL,
            last_inbound_at TEXT,
            opted_out_at TEXT,
            opt_out_source TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (company_id, contact_id)
    """)
    create_table(cursor, "contact_consents", f"""
            id ID_PK,
            company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
            contact_id TEXT NOT NULL,
            purpose TEXT NOT NULL CHECK (purpose IN ({_sql_list(CONSENT_PURPOSES)})),
            status TEXT NOT NULL CHECK (status IN ('granted', 'revoked')),
            source TEXT NOT NULL,
            evidence_ref TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CONSTRAINT uq_contact_consents_purpose UNIQUE (company_id, contact_id, purpose)
    """)
    create_table(cursor, "whatsapp_templates", f"""
            id ID_PK,
            company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
            purpose TEXT NOT NULL CHECK (purpose IN ({_sql_list(DELIVERY_PURPOSES)})),
            name TEXT NOT NULL,
            language_code TEXT NOT NULL,
            category TEXT NOT NULL CHECK (category IN ({_sql_list(TEMPLATE_CATEGORIES)})),
            status TEXT NOT NULL CHECK (status IN ({_sql_list(TEMPLATE_STATUSES)})),
            parameter_count INTEGER NOT NULL DEFAULT 0
                CHECK (parameter_count BETWEEN 0 AND {MAX_TEMPLATE_PARAMETERS}),
            active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CONSTRAINT uq_whatsapp_templates_purpose_language UNIQUE (company_id, purpose, language_code)
    """)
    create_table(cursor, "whatsapp_delivery_events", f"""
            id ID_PK,
            company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
            provider_message_id TEXT NOT NULL,
            outbound_message_id INTEGER,
            status TEXT NOT NULL CHECK (status IN ({_sql_list(DELIVERY_STATUSES)})),
            provider_timestamp TEXT,
            error_code INTEGER,
            received_at TEXT NOT NULL,
            CONSTRAINT uq_whatsapp_delivery_events_status UNIQUE (company_id, provider_message_id, status),
            CONSTRAINT fk_whatsapp_delivery_events_outbound
                FOREIGN KEY (company_id, outbound_message_id) REFERENCES outbound_messages (company_id, id)
    """)
    ensure_column(cursor, "outbound_messages", "template_parameters_json", "TEXT")
    ensure_column(cursor, "outbound_messages", "delivery_status", "TEXT")
    ensure_column(cursor, "outbound_messages", "delivery_updated_at", "TEXT")
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_outbound_company_provider_message "
        "ON outbound_messages (company_id, provider_message_id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_whatsapp_delivery_events_outbound "
        "ON whatsapp_delivery_events (company_id, outbound_message_id)"
    )


CHANNEL_MIGRATIONS = [
    (
        "071_whatsapp_delivery_policy",
        "WhatsApp service window, consent, templates and delivery status",
        create_channel_delivery,
    ),
]
