"""Global pytest setup: isolate environment and bootstrap the test database.

The app reads configuration from environment variables at import time (see
``app.core.config.Settings``), so we must pin env vars BEFORE any ``app.*``
module is imported. For the same reason, ``.env`` is loaded with
``override=False`` by ``app.core.config``, meaning the values we set here win
over whatever ``.env`` contains.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TEST_ENV_DEFAULTS: dict[str, str] = {
    "ENVIRONMENT": "test",
    "STRICT_RUNTIME_VALIDATION": "false",
    "ENABLE_RATE_LIMITING": "false",
    "ENABLE_ADMIN_AUTH": "false",
    "OUTBOUND_PROVIDER": "mock",
    "CLASSIFICATION_PROVIDER": "mock",
    "CALENDAR_PROVIDER": "mock",
    "REMINDER_WORKER_ENABLED": "false",
    # Ingestão exige credencial por padrão, inclusive nos testes: o caminho
    # seguro é o caminho testado.
    "ALLOW_UNAUTHENTICATED_INGEST": "false",
    "PII_PSEUDONYMIZATION_KEY": "test-pseudonymization-key",
}
for key, value in _TEST_ENV_DEFAULTS.items():
    os.environ[key] = value

# A suíte roda em SQLite por padrão, que é rápido e não exige serviço.
#
# `WAPP_TEST_DATABASE_URL` aponta a mesma suíte para um PostgreSQL descartável.
# Isso existe porque SQLite **não prova** comportamento PostgreSQL: fila,
# `SKIP LOCKED`, isolamento de transação e corrida de escrita são diferentes.
# O defeito que só apareceu no PostgreSQL real está documentado em
# `tests/test_schema_type_translation.py`.
_EXTERNAL_DATABASE_URL = os.environ.get("WAPP_TEST_DATABASE_URL", "").strip()

# Cada processo pytest recebe um banco SQLite EXCLUSIVO.
#
# Antes o caminho era fixo (`<tmp>/project_wapp_test.db`) e apagado na
# importação do conftest. Duas sessões simultâneas — o desenvolvedor rodando a
# suíte enquanto o CI roda a dele, ou uma sessão SQLite ao lado de uma sessão
# PostgreSQL — usavam o mesmo arquivo, e a segunda apagava o banco da primeira
# no meio da execução. O sintoma é o pior possível: falhas em massa, em testes
# que não têm relação nenhuma entre si, que somem quando você roda o arquivo
# sozinho. Aconteceu nesta própria rodada, com 146 falhas.
#
# O PID no nome garante exclusividade; o `finalize` no fim do arquivo remove
# **apenas** o banco desta sessão.
_TEST_DB_PATH = Path(tempfile.gettempdir()) / f"project_wapp_test_{os.getpid()}.db"

if _EXTERNAL_DATABASE_URL:
    os.environ["DATABASE_URL"] = _EXTERNAL_DATABASE_URL
    os.environ["POSTGRES_DATABASE_URL"] = _EXTERNAL_DATABASE_URL
else:
    if _TEST_DB_PATH.exists():
        _TEST_DB_PATH.unlink()
    os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB_PATH}"
    os.environ.pop("POSTGRES_DATABASE_URL", None)


def pytest_sessionfinish(session, exitstatus) -> None:
    """Remove o banco desta sessão, e só o dela."""
    if _EXTERNAL_DATABASE_URL:
        return
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{_TEST_DB_PATH}{suffix}")
        if candidate.exists():
            candidate.unlink()

import pytest  # noqa: E402

from app.services.rate_limit_service import reset_rate_limit_state  # noqa: E402
from app.storage.db import init_db  # noqa: E402

USING_EXTERNAL_DATABASE = bool(_EXTERNAL_DATABASE_URL)

# Marcador para o que exige PostgreSQL de verdade.
requires_postgres = pytest.mark.skipif(
    not USING_EXTERNAL_DATABASE,
    reason=(
        "Exige PostgreSQL real. Rode com "
        "WAPP_TEST_DATABASE_URL=postgresql://user@host:porta/banco"
    ),
)


@pytest.fixture(autouse=True, scope="session")
def _bootstrap_test_database() -> pytest.FixtureRequest:
    if USING_EXTERNAL_DATABASE:
        _reset_external_schema()
    init_db()
    yield
    if not USING_EXTERNAL_DATABASE and _TEST_DB_PATH.exists():
        _TEST_DB_PATH.unlink()


def _reset_external_schema() -> None:
    """Deixa o banco externo limpo antes da sessão.

    Só age num banco explicitamente apontado por `WAPP_TEST_DATABASE_URL`, que
    por contrato é descartável. Nunca toca o banco de desenvolvimento.
    """
    from app.storage.db import get_connection

    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute("DROP SCHEMA IF EXISTS public CASCADE")
        cursor.execute("CREATE SCHEMA public")
        connection.commit()
    finally:
        connection.close()


def build_stub_company_config(
    *,
    company_id: int = 1,
    company_slug: str = "default",
    company_name: str = "Default Company",
    business_type: str | None = "general",
    context: dict | None = None,
    capabilities: dict | None = None,
    policies: dict | None = None,
):
    """CompanyConfig determinístico para testes de orquestração.

    Mantém os testes de fluxo desacoplados do banco sem reabrir o caminho de
    fallback silencioso que o runtime proíbe.
    """
    from app.core.company_context import get_seed_company_context
    from app.core.vertical_packs import merge_capabilities, merge_policies, resolve_vertical_pack
    from app.services.company_config_service import CompanyConfig

    return CompanyConfig(
        company_id=company_id,
        company_slug=company_slug,
        company_name=company_name,
        business_type=business_type,
        vertical_pack=resolve_vertical_pack(business_type).key,
        version=1,
        status="published",
        source="test_stub",
        context=context if context is not None else get_seed_company_context(None),
        capabilities=merge_capabilities(business_type, capabilities),
        policies=merge_policies(business_type, policies),
    )


@pytest.fixture()
def stub_company_config(monkeypatch):
    """Injeta um company config estável no orquestrador."""

    def _apply(module, **kwargs):
        config = build_stub_company_config(**kwargs)
        monkeypatch.setattr(module, "get_company_config", lambda company_id: config)
        return config

    return _apply


@pytest.fixture()
def company_factory():
    """Cria empresas isoladas por teste, com slug único."""
    from uuid import uuid4

    from app.storage.repository import RuntimeRepository

    repository = RuntimeRepository()

    def _create(prefix: str = "tenant", business_type: str | None = "general") -> int:
        suffix = uuid4().hex[:12]
        return repository.create_company(
            slug=f"{prefix}-{suffix}",
            name=f"{prefix.title()} {suffix}",
            business_type=business_type,
        )

    return _create


@pytest.fixture(autouse=True)
def _reset_rate_limit_between_tests() -> None:
    reset_rate_limit_state()
    yield
    reset_rate_limit_state()


@pytest.fixture()
def tenant_credential_factory(company_factory):
    """Emite credenciais reais de ingestão para uma empresa."""
    from app.services.tenant_credential_service import issue_tenant_credential

    def _create(company_id: int | None = None, scopes: list[str] | None = None) -> dict:
        resolved_company_id = company_id if company_id is not None else company_factory("ingest")
        return issue_tenant_credential(
            company_id=resolved_company_id,
            scopes=scopes,
            description="test credential",
        )

    return _create


@pytest.fixture()
def admin_principal_factory(company_factory):
    """Cria principals administrativos com membership explícita."""
    from uuid import uuid4

    from app.services.admin_principal_service import create_principal_with_token

    def _create(
        *,
        global_role: str = "viewer",
        company_ids: list[int] | None = None,
        is_global: bool = False,
        company_roles: dict[int, str] | None = None,
    ) -> dict:
        """`company_roles` rebaixa (ou eleva) o papel dentro de uma empresa.

        Sem isto não dá para testar o contrato de papel efetivo: um superadmin
        global com membership `viewer` numa empresa precisa ser recusado ali,
        e a fábrica só sabia criar memberships com o papel global.
        """
        from app.storage import identity_repository

        suffix = uuid4().hex[:10]
        principal = create_principal_with_token(
            principal_key=f"{global_role}-{suffix}",
            display_name=f"{global_role} {suffix}",
            global_role=global_role,
            is_global=is_global,
            company_ids=company_ids or [],
        )
        for company_id, role in (company_roles or {}).items():
            identity_repository.upsert_membership(
                principal_id=int(principal["principal_id"]),
                company_id=int(company_id),
                role=role,
            )
        return principal

    return _create


@pytest.fixture()
def promotable_model_chain(company_factory, monkeypatch):
    """Monta a cadeia completa exigida para promover um modelo.

    dataset congelado -> job succeeded -> modelo real -> avaliação
    model-backed aprovada. Nenhuma chamada externa: o executor é o adapter
    atestado de teste e o job é registrado diretamente no repositório.
    """
    from app.core.config import settings
    from app.services.evaluation_service import (
        EXECUTION_MODE_MODEL_BACKED,
        register_evaluation_case,
        run_evaluation,
    )
    from app.services.model_registry_service import register_candidate_model
    from app.storage.repository import RuntimeRepository
    from tests.support.attestation import attested_test_executor

    repository = RuntimeRepository()

    def _build(
        *,
        company_id: int | None = None,
        model_ref: str | None = None,
        job_id: str | None = None,
        dataset_version_id: int = 1,
        pass_rate_reply: str = "Vou confirmar essa informação com a equipe e já te retorno.",
    ) -> dict:
        from uuid import uuid4

        resolved_company_id = company_id if company_id is not None else company_factory("promote")
        # `finetuning_jobs.job_id` é único globalmente: fixar o valor fazia dois
        # testes que usam esta fixture colidirem entre si.
        suffix = uuid4().hex[:10]
        model_ref = model_ref or f"ft:model-real-{suffix}"
        job_id = job_id or f"ftjob-real-{suffix}"
        monkeypatch.setattr(settings, "enable_real_model_evaluation", True)
        monkeypatch.setattr(settings, "finetuning_min_eval_pass_rate", 0.5)

        repository.create_finetuning_job_record(
            job_id=job_id,
            company_id=resolved_company_id,
            status="succeeded",
            base_model="gpt-4o-mini-2024-07-18",
            fine_tuned_model=model_ref,
            dataset_version_id=dataset_version_id,
        )

        register_evaluation_case(
            company_id=resolved_company_id,
            case_key="promocao",
            scenario_type="general",
            customer_message="Vocês atendem no feriado?",
        )
        base_run = run_evaluation(
            company_id=resolved_company_id,
            approach="base",
            executor=attested_test_executor(
                model_ref="gpt-4o-mini",
                default_reply=pass_rate_reply,
            ),
            dataset_version_id=dataset_version_id,
            model_ref="gpt-4o-mini",
            finetuning_job_id=job_id,
            execution_mode=EXECUTION_MODE_MODEL_BACKED,
        )
        finetuned_run = run_evaluation(
            company_id=resolved_company_id,
            approach="finetuned",
            executor=attested_test_executor(
                model_ref=model_ref,
                default_reply=pass_rate_reply,
            ),
            dataset_version_id=dataset_version_id,
            model_ref=model_ref,
            finetuning_job_id=job_id,
            execution_mode=EXECUTION_MODE_MODEL_BACKED,
        )

        model = register_candidate_model(
            company_id=resolved_company_id,
            model_ref=model_ref,
            base_model="gpt-4o-mini-2024-07-18",
            dataset_version_id=dataset_version_id,
            finetuning_job_id=job_id,
            evaluation_run_id=int(finetuned_run["id"]),
        )
        return {
            "company_id": resolved_company_id,
            "model": model,
            "model_ref": model_ref,
            "job_id": job_id,
            "dataset_version_id": dataset_version_id,
            "base_run": base_run,
            "finetuned_run": finetuned_run,
        }

    return _build
