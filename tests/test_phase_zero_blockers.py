"""Regressões dos seis bloqueadores confirmados antes do Automation Studio.

Cada teste aqui falhava contra o código anterior. A ordem segue a numeração
usada na especificação:

1. escopo autorizado ignorado por handlers que refaziam a resolução;
2. rotas tenant-bearing fora da política central e administração de identidade
   sem exigir superadmin global;
3. sessão de principal revogado caindo para principal legado;
4. proveniência ausente sendo tratada como "nada a comparar";
5. gerador arbitrário marcando execução como model-backed;
6. metadados de retrieval fora da versão do documento.
"""

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.services.admin_principal_service import revoke_principal_token
from app.services.platform_session_service import COOKIE_NAME, issue_platform_session
from app.storage import identity_repository

client = TestClient(app)


@pytest.fixture(autouse=True)
def _enable_admin_auth(monkeypatch):
    monkeypatch.setattr(settings, "enable_admin_auth", True)
    monkeypatch.setattr(settings, "admin_viewer_token", "legacy-viewer")
    monkeypatch.setattr(settings, "admin_operator_token", "legacy-operator")
    monkeypatch.setattr(settings, "admin_superadmin_token", "legacy-superadmin")
    monkeypatch.setattr(settings, "admin_api_token", None)


def _headers(principal: dict) -> dict[str, str]:
    return {"X-Admin-Token": principal["token"]}


def _get_with_session(session_token: str, company_id: int):
    """GET autenticado apenas pelo cookie de sessão, sem token de admin."""
    client.cookies.clear()
    client.cookies.set(COOKIE_NAME, session_token)
    try:
        return client.get("/admin/access/scope", params={"company_id": company_id})
    finally:
        client.cookies.clear()


# --- 1. escopo autorizado é a fonte única -------------------------------------------


def test_single_membership_principal_never_falls_back_to_the_default_company(
    company_factory,
    admin_principal_factory,
) -> None:
    """Omitir company_id não pode significar "a primeira empresa ativa".

    `/admin/finetuning/jobs-db` resolvia por conta própria e caía no default
    company quando nenhum parâmetro era enviado.
    """
    own_company = company_factory("scope-own")
    operator = admin_principal_factory(global_role="operator", company_ids=[own_company])

    response = client.get("/admin/finetuning/jobs-db", headers=_headers(operator))

    assert response.status_code == 200
    assert response.json() == []

    # E o escopo efetivamente aplicado foi o da membership, não o default.
    echo = client.get("/admin/access/scope", headers=_headers(operator))
    assert echo.status_code == 200
    assert echo.json()["authorized_company_id"] == own_company


def test_handler_cannot_widen_scope_to_another_company(
    company_factory,
    admin_principal_factory,
) -> None:
    own_company = company_factory("scope-a")
    other_company = company_factory("scope-b")
    operator = admin_principal_factory(global_role="operator", company_ids=[own_company])

    response = client.get(
        "/admin/finetuning/jobs-db",
        params={"company_id": other_company},
        headers=_headers(operator),
    )

    assert response.status_code == 403


def test_global_superadmin_still_sees_the_global_scope(admin_principal_factory) -> None:
    superadmin = admin_principal_factory(global_role="superadmin", is_global=True)

    echo = client.get("/admin/access/scope", headers=_headers(superadmin))

    assert echo.status_code == 200
    assert echo.json()["authorized_company_id"] is None
    assert echo.json()["is_global"] is True


# --- 2. rotas tenant-bearing e administração de identidade ---------------------------


def test_company_context_of_another_tenant_is_denied(
    company_factory,
    admin_principal_factory,
) -> None:
    """`/admin/company-context` carrega dimensão de empresa.

    Estava na allowlist de prefixos e resolvia `company_id` sem autorização.
    """
    own_company = company_factory("ctx-own")
    other_company = company_factory("ctx-other")
    operator = admin_principal_factory(global_role="operator", company_ids=[own_company])

    response = client.get(
        "/admin/company-context",
        params={"company_id": other_company},
        headers=_headers(operator),
    )

    assert response.status_code == 403


def test_audit_events_are_scoped_to_the_principal_companies(
    company_factory,
    admin_principal_factory,
) -> None:
    own_company = company_factory("audit-own")
    other_company = company_factory("audit-other")
    operator = admin_principal_factory(global_role="operator", company_ids=[own_company])

    response = client.get(
        "/admin/audit-events",
        params={"company_id": other_company},
        headers=_headers(operator),
    )

    assert response.status_code == 403


def test_onboarding_status_of_another_tenant_is_denied(
    company_factory,
    admin_principal_factory,
) -> None:
    own_company = company_factory("onb-own")
    other_company = company_factory("onb-other")
    viewer = admin_principal_factory(global_role="viewer", company_ids=[own_company])

    response = client.get(
        "/admin/system/onboarding",
        params={"company_id": other_company},
        headers=_headers(viewer),
    )

    assert response.status_code == 403


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/admin/principals"),
        ("post", "/admin/principals"),
    ],
)
def test_principal_administration_requires_a_global_superadmin(
    method,
    path,
    company_factory,
    admin_principal_factory,
) -> None:
    """Superadmin de um tenant não administra identidades da plataforma."""
    company_id = company_factory("identity-scope")
    tenant_superadmin = admin_principal_factory(
        global_role="superadmin",
        company_ids=[company_id],
        is_global=False,
    )

    response = getattr(client, method)(
        path,
        headers=_headers(tenant_superadmin),
        **({"json": {}} if method == "post" else {}),
    )

    assert response.status_code == 403


def test_every_mutating_admin_route_is_covered_by_a_scope_policy() -> None:
    """Deny-by-default: nenhuma rota mutável fica sem política declarada.

    Uma rota nova que aceite `company_id` sem entrar na política central
    quebra este teste em vez de vazar silenciosamente.
    """
    from app.api.company_scope import describe_route_policy

    uncovered: list[str] = []
    for route in app.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", set()) or set()
        if not path.startswith("/admin"):
            continue
        if not (methods & {"POST", "PUT", "PATCH", "DELETE"}):
            continue
        if describe_route_policy(path) is None:
            uncovered.append(f"{sorted(methods)} {path}")

    assert uncovered == []


# --- 3. sessão de principal revogado -------------------------------------------------


def test_session_of_a_deactivated_principal_is_rejected(
    company_factory,
    admin_principal_factory,
) -> None:
    company_id = company_factory("session-deactivated")
    principal = admin_principal_factory(global_role="operator", company_ids=[company_id])
    session = issue_platform_session(
        role="operator",
        actor=principal["principal_key"],
        principal_id=principal["principal_id"],
        token_id=principal["token_id"],
    )

    identity_repository.set_principal_active(
        principal_id=int(principal["principal_id"]),
        active=False,
    )

    response = _get_with_session(session["token"], company_id)

    # Nunca 200 com principal legado: a sessão perdeu a base de identidade.
    assert response.status_code == 401


def test_session_dies_with_the_credential_that_created_it(
    company_factory,
    admin_principal_factory,
) -> None:
    company_id = company_factory("session-revoked-token")
    principal = admin_principal_factory(global_role="operator", company_ids=[company_id])
    session = issue_platform_session(
        role="operator",
        actor=principal["principal_key"],
        principal_id=principal["principal_id"],
        token_id=principal["token_id"],
    )

    revoke_principal_token(token_id=int(principal["token_id"]))

    response = _get_with_session(session["token"], company_id)

    assert response.status_code == 401


def test_session_of_a_removed_principal_is_rejected(
    company_factory,
    admin_principal_factory,
) -> None:
    company_id = company_factory("session-removed")
    principal = admin_principal_factory(global_role="operator", company_ids=[company_id])
    session = issue_platform_session(
        role="operator",
        actor=principal["principal_key"],
        principal_id=principal["principal_id"],
        token_id=principal["token_id"],
    )

    identity_repository.delete_principal(principal_id=int(principal["principal_id"]))

    response = _get_with_session(session["token"], company_id)

    assert response.status_code == 401


def test_auth_epoch_bump_invalidates_existing_sessions(
    company_factory,
    admin_principal_factory,
) -> None:
    company_id = company_factory("session-epoch")
    principal = admin_principal_factory(global_role="operator", company_ids=[company_id])
    session = issue_platform_session(
        role="operator",
        actor=principal["principal_key"],
        principal_id=principal["principal_id"],
        token_id=principal["token_id"],
    )

    identity_repository.bump_auth_epoch(principal_id=int(principal["principal_id"]))

    response = _get_with_session(session["token"], company_id)

    assert response.status_code == 401


# --- 4. proveniência do promotion gate -----------------------------------------------


def test_null_dataset_on_the_run_blocks_promotion(company_factory, monkeypatch) -> None:
    """Campo ausente bloqueia. Antes, `IS NULL` pulava a comparação."""
    from app.services.evaluation_service import (
        EXECUTION_MODE_MODEL_BACKED,
        get_promotion_gate,
        register_evaluation_case,
        run_evaluation,
    )
    from tests.support.attestation import attested_test_executor

    company_id = company_factory("gate-null-dataset")
    monkeypatch.setattr(settings, "enable_real_model_evaluation", True)
    register_evaluation_case(
        company_id=company_id,
        case_key="caso",
        scenario_type="general",
        customer_message="Vocês atendem no feriado?",
    )
    for approach, subject in (("base", "gpt-4o-mini"), ("finetuned", "ft:sem-dataset")):
        run_evaluation(
            company_id=company_id,
            approach=approach,
            executor=attested_test_executor(model_ref=subject),
            model_ref=subject,
            # dataset_version_id deliberadamente ausente.
            finetuning_job_id="ftjob-x" if approach == "finetuned" else None,
            execution_mode=EXECUTION_MODE_MODEL_BACKED,
        )

    gate = get_promotion_gate(
        company_id=company_id,
        min_pass_rate=0.0,
        model_ref="ft:sem-dataset",
        dataset_version_id=7,
        finetuning_job_id="ftjob-x",
    )

    assert gate["passed"] is False
    assert "evaluation_dataset_missing" in gate["reasons"]


def test_null_job_on_the_run_blocks_promotion(company_factory, monkeypatch) -> None:
    from app.services.evaluation_service import (
        EXECUTION_MODE_MODEL_BACKED,
        get_promotion_gate,
        register_evaluation_case,
        run_evaluation,
    )
    from tests.support.attestation import attested_test_executor

    company_id = company_factory("gate-null-job")
    monkeypatch.setattr(settings, "enable_real_model_evaluation", True)
    register_evaluation_case(
        company_id=company_id,
        case_key="caso",
        scenario_type="general",
        customer_message="Vocês atendem no feriado?",
    )
    for approach, subject in (("base", "gpt-4o-mini"), ("finetuned", "ft:sem-job")):
        run_evaluation(
            company_id=company_id,
            approach=approach,
            executor=attested_test_executor(model_ref=subject),
            model_ref=subject,
            dataset_version_id=3,
            # finetuning_job_id deliberadamente ausente.
            execution_mode=EXECUTION_MODE_MODEL_BACKED,
        )

    gate = get_promotion_gate(
        company_id=company_id,
        min_pass_rate=0.0,
        model_ref="ft:sem-job",
        dataset_version_id=3,
        finetuning_job_id="ftjob-esperado",
    )

    assert gate["passed"] is False
    assert "evaluation_job_missing" in gate["reasons"]


def test_promotion_requires_the_caller_to_state_the_full_provenance(
    company_factory,
) -> None:
    from app.services.evaluation_service import get_promotion_gate

    company_id = company_factory("gate-no-provenance")

    gate = get_promotion_gate(company_id=company_id, min_pass_rate=0.8)

    assert gate["passed"] is False
    assert "promotion_target_not_specified" in gate["reasons"]


def test_complete_and_matching_provenance_passes(promotable_model_chain) -> None:
    from app.services.evaluation_service import get_promotion_gate

    chain = promotable_model_chain()

    gate = get_promotion_gate(
        company_id=chain["company_id"],
        min_pass_rate=0.5,
        model_ref=chain["model_ref"],
        dataset_version_id=chain["dataset_version_id"],
        finetuning_job_id=chain["job_id"],
    )

    assert gate["passed"] is True, gate["reasons"]


# --- 5. model-backed exige execução comprovada ---------------------------------------


def test_an_arbitrary_generator_cannot_claim_model_backed(company_factory, monkeypatch) -> None:
    from app.services.evaluation_service import (
        EXECUTION_MODE_MODEL_BACKED,
        EvaluationError,
        build_offline_reply_generator,
        register_evaluation_case,
        run_evaluation,
    )

    company_id = company_factory("attestation-arbitrary")
    monkeypatch.setattr(settings, "enable_real_model_evaluation", True)
    register_evaluation_case(
        company_id=company_id,
        case_key="caso",
        scenario_type="general",
        customer_message="Vocês atendem no feriado?",
    )

    with pytest.raises(EvaluationError, match="attestation"):
        run_evaluation(
            company_id=company_id,
            approach="finetuned",
            executor=build_offline_reply_generator(),
            model_ref="ft:falso",
            execution_mode=EXECUTION_MODE_MODEL_BACKED,
        )


def test_fake_adapter_cannot_produce_a_promotable_attestation_in_production(
    company_factory,
    monkeypatch,
) -> None:
    from app.services import evaluation_executor
    from app.services.evaluation_service import (
        EXECUTION_MODE_MODEL_BACKED,
        EvaluationError,
        register_evaluation_case,
        run_evaluation,
    )
    from tests.support.attestation import attested_test_executor

    company_id = company_factory("attestation-production")
    monkeypatch.setattr(settings, "enable_real_model_evaluation", True)
    monkeypatch.setattr(evaluation_executor, "is_production_like", lambda: True)
    register_evaluation_case(
        company_id=company_id,
        case_key="caso",
        scenario_type="general",
        customer_message="Vocês atendem no feriado?",
    )

    with pytest.raises(EvaluationError, match="fake"):
        run_evaluation(
            company_id=company_id,
            approach="finetuned",
            executor=attested_test_executor(model_ref="ft:falso"),
            model_ref="ft:falso",
            execution_mode=EXECUTION_MODE_MODEL_BACKED,
        )


def test_real_executor_fails_closed_when_external_calls_are_disabled(
    company_factory,
    monkeypatch,
) -> None:
    from app.services.evaluation_executor import (
        EvaluationExecutorError,
        build_model_backed_executor,
    )

    company_id = company_factory("attestation-closed")
    monkeypatch.setattr(settings, "enable_real_model_evaluation", False)

    with pytest.raises(EvaluationExecutorError, match="disabled"):
        build_model_backed_executor(company_id=company_id, model_ref="ft:algo")


def test_attestation_records_executor_provenance(company_factory, monkeypatch) -> None:
    from app.services.evaluation_service import (
        EXECUTION_MODE_MODEL_BACKED,
        register_evaluation_case,
        run_evaluation,
    )
    from tests.support.attestation import attested_test_executor

    company_id = company_factory("attestation-metadata")
    monkeypatch.setattr(settings, "enable_real_model_evaluation", True)
    register_evaluation_case(
        company_id=company_id,
        case_key="caso",
        scenario_type="general",
        customer_message="Vocês atendem no feriado?",
    )

    run = run_evaluation(
        company_id=company_id,
        approach="finetuned",
        executor=attested_test_executor(model_ref="ft:atestado"),
        model_ref="ft:atestado",
        execution_mode=EXECUTION_MODE_MODEL_BACKED,
    )

    assert run["executor_kind"] == "fake_test_adapter"
    assert run["provider"] == "test"
    assert run["model_ref"] == "ft:atestado"
    assert run["subject_fingerprint"]


def test_the_evaluation_route_is_always_simulation(
    company_factory,
    admin_principal_factory,
) -> None:
    from app.services.evaluation_service import register_evaluation_case

    company_id = company_factory("route-simulation")
    operator = admin_principal_factory(global_role="operator", company_ids=[company_id])
    register_evaluation_case(
        company_id=company_id,
        case_key="rota",
        scenario_type="general",
        customer_message="Vocês atendem no feriado?",
    )

    response = client.post(
        "/admin/evaluation/runs",
        params={"company_id": company_id},
        json={"approach": "finetuned", "execution_mode": "model_backed"},
        headers=_headers(operator),
    )

    assert response.status_code == 200
    assert response.json()["execution_mode"] == "simulation"
    assert response.json()["promotion_eligible"] is False


# --- 6. metadados versionados do knowledge -------------------------------------------


def test_editing_a_draft_does_not_change_published_metadata(company_factory) -> None:
    from app.services.knowledge_document_service import ingest_document
    from app.storage import knowledge_repository

    company_id = company_factory("kb-versioned-metadata")

    published = ingest_document(
        company_id=company_id,
        title="Política de cancelamento",
        content="Cancelamento com 24 horas de antecedência não gera custo.",
        document_type="business_rule",
        external_ref="politica",
        priority=1,
        approve=True,
    )

    ingest_document(
        company_id=company_id,
        title="Rascunho renomeado",
        content="Cancelamento com 48 horas de antecedência não gera custo.",
        document_type="general_faq",
        external_ref="politica",
        priority=9,
        approve=False,
    )

    published_version = knowledge_repository.get_version(
        company_id=company_id,
        version_id=int(published["version_id"]),
    )

    assert published_version["title"] == "Política de cancelamento"
    assert published_version["document_type"] == "business_rule"
    assert int(published_version["priority"]) == 1


def test_retrieval_reads_metadata_from_the_published_version(company_factory) -> None:
    from app.services.knowledge_document_service import ingest_document
    from app.services.knowledge_retrieval_service import retrieve_knowledge

    company_id = company_factory("kb-retrieval-metadata")

    ingest_document(
        company_id=company_id,
        title="Horário publicado",
        content="Atendemos de segunda a sexta das 9h às 18h.",
        document_type="schedule_rule",
        external_ref="horario",
        priority=1,
        approve=True,
    )
    ingest_document(
        company_id=company_id,
        title="Horário em rascunho",
        content="Atendemos de segunda a sexta das 9h às 18h.",
        document_type="general_faq",
        external_ref="horario",
        priority=99,
        approve=False,
    )

    result = retrieve_knowledge(company_id=company_id, query="qual o horário de atendimento?")

    assert result.chunks
    assert result.chunks[0].document_title == "Horário publicado"
    assert result.chunks[0].document_type == "schedule_rule"


def test_validity_window_of_the_draft_does_not_affect_production(company_factory) -> None:
    """Vigência é metadado de retrieval: o draft não pode expirar o publicado."""
    from app.services.knowledge_document_service import ingest_document
    from app.services.knowledge_retrieval_service import retrieve_knowledge

    company_id = company_factory("kb-validity")

    ingest_document(
        company_id=company_id,
        title="Tabela de preços",
        content="O pacote básico custa duzentos reais.",
        document_type="price",
        external_ref="tabela",
        approve=True,
    )
    ingest_document(
        company_id=company_id,
        title="Tabela de preços",
        content="O pacote básico custa duzentos reais.",
        document_type="price",
        external_ref="tabela",
        valid_until="2000-01-01",
        approve=False,
    )

    result = retrieve_knowledge(company_id=company_id, query="quanto custa o pacote básico?")

    assert result.has_relevant_source
    assert "duzentos" in " ".join(chunk.content for chunk in result.chunks)


def test_approving_the_draft_publishes_its_metadata(company_factory) -> None:
    from app.services.knowledge_document_service import approve_document, ingest_document
    from app.services.knowledge_retrieval_service import retrieve_knowledge

    company_id = company_factory("kb-approve-metadata")

    ingest_document(
        company_id=company_id,
        title="Antigo",
        content="Atendemos de segunda a sexta das 9h às 18h.",
        document_type="general_faq",
        external_ref="horario",
        approve=True,
    )
    draft = ingest_document(
        company_id=company_id,
        title="Novo",
        content="Atendemos de segunda a sábado das 8h às 20h.",
        document_type="schedule_rule",
        external_ref="horario",
        approve=False,
    )

    approve_document(
        company_id=company_id,
        document_id=int(draft["document_id"]),
        approved_by="revisor",
    )
    result = retrieve_knowledge(company_id=company_id, query="qual o horário de atendimento?")

    assert result.chunks[0].document_title == "Novo"
    assert result.chunks[0].document_type == "schedule_rule"
