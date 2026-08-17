"""Migration 067 - integridade de tenant nas tabelas da plataforma.

A migration 035 fechou essa lacuna para as 22 tabelas que existiam então:
toda linha com `company_id` passa a exigir que a empresa exista, por chave
estrangeira no PostgreSQL e por trigger no SQLite.

As 26 tabelas criadas depois (plataforma multiempresa 038-060 e Automation
Studio 061-066) nasceram sem essa guarda. Elas carregam `company_id` e as
queries filtram por ele, mas nada no banco impedia gravar uma linha
apontando para uma empresa que não existe — e uma linha órfã não aparece em
nenhuma listagem tenant-scoped, então o defeito é invisível até alguém
auditar contagens.

A guarda é de banco, e não de aplicação, de propósito: o isolamento não pode
depender de todo caminho de escrita lembrar de validar. Existem hoje o
repositório, os serviços, o restore de backup e a ferramenta de migração
SQLite->PostgreSQL escrevendo nessas tabelas.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.storage.schema_utils import assert_identifier, is_postgres

Migration = tuple[str, str, Callable[[Any], None]]

# Tabelas tenant-scoped criadas nas migrations 038-066.
#
# `service_accounts` e `service_account_tokens` ficam de fora porque já
# nasceram com `REFERENCES companies(id)` inline.
PLATFORM_TENANT_TABLES: tuple[str, ...] = (
    "company_channel_bindings",
    "company_context_versions",
    "knowledge_documents",
    "knowledge_document_versions",
    "knowledge_chunks",
    "conversation_import_batches",
    "imported_conversations",
    "imported_messages",
    "training_candidates",
    "dataset_versions",
    "dataset_version_items",
    "evaluation_cases",
    "evaluation_runs",
    "evaluation_results",
    "company_models",
    "automation_workflows",
    "automation_workflow_versions",
    "automation_workflow_drafts",
    "automation_deployments",
    "automation_events",
    "automation_outbox",
    "automation_executions",
    "automation_step_executions",
    "automation_jobs",
    "automation_effects",
    "automation_approval_gates",
)


def _constraint_exists(cursor: Any, *, table: str, constraint: str) -> bool:
    cursor.execute(
        """
        SELECT 1
        FROM pg_constraint
        WHERE conname = ?
          AND conrelid = ?::regclass
        """,
        (constraint, table),
    )
    return cursor.fetchone() is not None


def _guard_postgres(cursor: Any, table: str) -> None:
    constraint = f"fk_{table}_company"
    if _constraint_exists(cursor, table=table, constraint=constraint):
        return
    safe_table = assert_identifier(table, kind="table")
    safe_constraint = assert_identifier(constraint, kind="constraint")
    # NOT VALID seguido de VALIDATE: a adição não trava a tabela para escrita,
    # e a validação das linhas existentes acontece com um lock mais fraco.
    # Adicionar a constraint já validada bloquearia escrita durante o release.
    cursor.execute(
        f"""
        ALTER TABLE {safe_table}
        ADD CONSTRAINT {safe_constraint}
        FOREIGN KEY (company_id)
        REFERENCES companies(id)
        ON DELETE RESTRICT
        NOT VALID
        """
    )
    cursor.execute(f"ALTER TABLE {safe_table} VALIDATE CONSTRAINT {safe_constraint}")


def _guard_sqlite(cursor: Any, table: str) -> None:
    """Triggers de INSERT e UPDATE.

    O SQLite não adiciona chave estrangeira a uma tabela existente sem
    recriá-la, e recriar tabela num release é a operação que mais dá errado.
    O trigger dá a mesma garantia sem tocar no esquema.
    """
    safe_table = assert_identifier(table, kind="table")
    # Os literais já vêm no par (sufixo, palavra-chave SQL). Derivar a
    # segunda com `.upper()` interpolaria o resultado de uma chamada de
    # método, que `tests/test_dynamic_sql_guard.py` não consegue provar
    # segura — e com razão: quem lê não distingue isso de dado do chamador.
    for suffix, keyword in (("insert", "INSERT"), ("update", "UPDATE")):
        trigger = assert_identifier(
            f"trg_{table}_company_{suffix}", kind="trigger"
        )
        cursor.execute(f"DROP TRIGGER IF EXISTS {trigger}")
        cursor.execute(
            f"""
            CREATE TRIGGER {trigger}
            BEFORE {keyword} ON {safe_table}
            FOR EACH ROW
            WHEN NEW.company_id IS NOT NULL
              AND (SELECT 1 FROM companies WHERE id = NEW.company_id) IS NULL
            BEGIN
                SELECT RAISE(ABORT, 'invalid tenant company reference');
            END
            """
        )


def _extend_tenant_integrity(cursor: Any) -> None:
    postgres = is_postgres()
    for table in PLATFORM_TENANT_TABLES:
        # Linha órfã pré-existente faria a constraint falhar no meio do
        # release. Detectar antes transforma isso numa mensagem que nomeia a
        # tabela, em vez de um erro do driver no fim da migration.
        safe_table = assert_identifier(table, kind="table")
        cursor.execute(
            f"""
            SELECT COUNT(*) AS total
            FROM {safe_table} AS tenant_row
            WHERE tenant_row.company_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM companies AS company
                  WHERE company.id = tenant_row.company_id
              )
            """
        )
        orphans = int(dict(cursor.fetchone())["total"] or 0)
        if orphans:
            raise RuntimeError(
                f"Tenant integrity migration blocked: {table} has "
                f"{orphans} orphan company reference(s). Resolve them before upgrading."
            )

        if postgres:
            _guard_postgres(cursor, table)
        else:
            _guard_sqlite(cursor, table)


TENANT_INTEGRITY_MIGRATIONS: list[Migration] = [
    (
        "067_platform_tenant_integrity",
        "Enforce company references on platform and automation tables",
        _extend_tenant_integrity,
    ),
]
