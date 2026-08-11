"""Avaliação offline, gate de promoção e ciclo de vida do modelo por empresa."""

import pytest

from app.core.config import settings
from app.services.evaluation_service import (
    EvaluationError,
    EvaluationResponse,
    build_offline_reply_generator,
    compare_runs,
    get_promotion_gate,
    register_evaluation_case,
    run_evaluation,
)
from app.services.knowledge_document_service import ingest_document
from app.services.model_registry_service import (
    ModelRegistryError,
    activate_model,
    register_candidate_model,
    resolve_reply_model,
    rollback_active_model,
)
from app.storage import evaluation_repository


def _register_cases(company_id: int) -> None:
    register_evaluation_case(
        company_id=company_id,
        case_key="preco-conhecido",
        scenario_type="lead",
        customer_message="Qual o valor do plano essencial?",
        expected_keywords=["plano essencial"],
        requires_source_grounding=True,
    )
    register_evaluation_case(
        company_id=company_id,
        case_key="sem-fonte",
        scenario_type="general",
        customer_message="Vocês fazem entrega em Manaus no mesmo dia?",
        requires_source_grounding=True,
    )


# --- avaliação -------------------------------------------------------------------


def test_evaluation_requires_registered_cases(company_factory) -> None:
    company_id = company_factory("eval-empty")

    with pytest.raises(EvaluationError, match="No active evaluation cases"):
        run_evaluation(
            company_id=company_id,
            approach="base",
            reply_generator=build_offline_reply_generator(),
        )


def test_offline_evaluation_never_calls_an_external_provider(company_factory) -> None:
    company_id = company_factory("eval-offline")
    _register_cases(company_id)
    ingest_document(
        company_id=company_id,
        title="Plano essencial",
        content="O plano essencial custa 199 reais por mês e inclui atendimento ilimitado.",
        document_type="price",
        external_ref="plano-essencial",
        approve=True,
    )

    run = run_evaluation(
        company_id=company_id,
        approach="base_rag",
        reply_generator=build_offline_reply_generator(),
    )

    assert run["total_cases"] == 2
    assert 0.0 <= run["scores"]["pass_rate"] <= 1.0
    assert set(run["scores"]["dimensions"]) >= {
        "factuality",
        "source_adherence",
        "handoff",
        "action_integrity",
        "safety",
        "tone",
    }


def test_ungrounded_factual_claim_fails_source_adherence(company_factory) -> None:
    company_id = company_factory("eval-grounding")
    register_evaluation_case(
        company_id=company_id,
        case_key="sem-fonte",
        scenario_type="general",
        customer_message="Qual o prazo de entrega para Manaus?",
        requires_source_grounding=True,
    )

    run = run_evaluation(
        company_id=company_id,
        approach="base_rag",
        reply_generator=build_offline_reply_generator(
            default_reply="O prazo de entrega para Manaus é de dois dias úteis."
        ),
    )

    results = evaluation_repository.get_run_results(company_id=company_id, run_id=int(run["id"]))
    assert results[0]["passed"] == 0
    assert "ungrounded_factual_claim" in results[0]["failure_reasons"]


def test_simulated_external_action_fails_action_integrity(company_factory) -> None:
    company_id = company_factory("eval-action")
    register_evaluation_case(
        company_id=company_id,
        case_key="agendamento",
        scenario_type="scheduling",
        customer_message="Pode marcar para amanhã às 10h?",
    )

    def generator(request):
        return EvaluationResponse(
            reply="Pronto, já agendei para amanhã às 10h!",
            used_action_tool=False,
        )

    run = run_evaluation(
        company_id=company_id,
        approach="base",
        reply_generator=generator,
    )
    results = evaluation_repository.get_run_results(company_id=company_id, run_id=int(run["id"]))

    assert results[0]["passed"] == 0
    assert "simulated_external_action" in results[0]["failure_reasons"]


def test_action_passes_when_the_tool_actually_ran(company_factory) -> None:
    company_id = company_factory("eval-action-ok")
    register_evaluation_case(
        company_id=company_id,
        case_key="agendamento-real",
        scenario_type="scheduling",
        customer_message="Pode marcar para amanhã às 10h?",
        requires_action_tool=True,
    )

    def generator(request):
        return EvaluationResponse(
            reply="Pronto, já agendei para amanhã às 10h!",
            used_action_tool=True,
        )

    run = run_evaluation(company_id=company_id, approach="base", reply_generator=generator)
    results = evaluation_repository.get_run_results(company_id=company_id, run_id=int(run["id"]))

    assert results[0]["passed"] == 1


def test_runs_can_be_compared_against_a_baseline(company_factory) -> None:
    company_id = company_factory("eval-compare")
    _register_cases(company_id)

    base_run = run_evaluation(
        company_id=company_id,
        approach="base",
        reply_generator=build_offline_reply_generator(default_reply="Não sei."),
    )
    improved_run = run_evaluation(
        company_id=company_id,
        approach="finetuned",
        reply_generator=build_offline_reply_generator(),
        model_ref="ft:test:empresa",
    )

    comparison = compare_runs(
        company_id=company_id,
        run_ids=[int(base_run["id"]), int(improved_run["id"])],
    )

    assert comparison["baseline_run_id"] == int(base_run["id"])
    assert len(comparison["runs"]) == 2
    assert comparison["runs"][1]["delta_vs_baseline"] >= 0


def test_promotion_gate_blocks_without_a_finetuned_run(company_factory) -> None:
    company_id = company_factory("eval-gate-empty")
    gate = get_promotion_gate(company_id=company_id, min_pass_rate=0.7)

    assert gate["passed"] is False
    assert "missing_promotion_eligible_finetuned_run" in gate["reasons"]


def test_simulated_run_never_unlocks_promotion(company_factory) -> None:
    """Uma execução simulada não prova nada sobre um modelo."""
    company_id = company_factory("eval-gate-simulated")
    _register_cases(company_id)

    base_run = run_evaluation(
        company_id=company_id,
        approach="base",
        reply_generator=build_offline_reply_generator(),
    )
    finetuned_run = run_evaluation(
        company_id=company_id,
        approach="finetuned",
        reply_generator=build_offline_reply_generator(),
        model_ref="ft:inventado",
    )

    assert base_run["execution_mode"] == "simulation"
    assert int(finetuned_run["promotion_eligible"]) == 0

    gate = get_promotion_gate(company_id=company_id, min_pass_rate=0.0)
    assert gate["passed"] is False
    assert "missing_promotion_eligible_finetuned_run" in gate["reasons"]


def test_canned_replies_cannot_be_registered_as_model_backed(company_factory) -> None:
    company_id = company_factory("eval-gate-canned")
    _register_cases(company_id)

    with pytest.raises(EvaluationError, match="Model-backed evaluation is disabled"):
        run_evaluation(
            company_id=company_id,
            approach="finetuned",
            reply_generator=build_offline_reply_generator(default_reply="tudo certo"),
            model_ref="ft:falso",
            execution_mode="model_backed",
        )


def test_model_backed_run_requires_an_explicit_model_ref(monkeypatch, company_factory) -> None:
    company_id = company_factory("eval-gate-nomodel")
    _register_cases(company_id)
    monkeypatch.setattr(settings, "enable_real_model_evaluation", True)

    with pytest.raises(EvaluationError, match="requires an explicit model_ref"):
        run_evaluation(
            company_id=company_id,
            approach="base",
            reply_generator=build_offline_reply_generator(),
            execution_mode="model_backed",
        )


def test_gate_rejects_a_run_of_a_different_model(promotable_model_chain) -> None:
    chain = promotable_model_chain(model_ref="ft:modelo-avaliado", job_id="ftjob-avaliado")

    gate = get_promotion_gate(
        company_id=chain["company_id"],
        min_pass_rate=0.5,
        model_ref="ft:outro-modelo",
    )

    assert gate["passed"] is False
    assert "evaluation_model_ref_mismatch" in gate["reasons"]


def test_gate_rejects_a_stale_case_suite(promotable_model_chain) -> None:
    chain = promotable_model_chain(model_ref="ft:suite", job_id="ftjob-suite")

    # A suíte muda depois da avaliação: o resultado antigo deixa de valer.
    register_evaluation_case(
        company_id=chain["company_id"],
        case_key="caso-novo",
        scenario_type="general",
        customer_message="Pergunta nova que não foi avaliada.",
    )

    gate = get_promotion_gate(
        company_id=chain["company_id"],
        min_pass_rate=0.5,
        model_ref=chain["model_ref"],
    )

    assert gate["passed"] is False
    assert "stale_case_suite" in gate["reasons"]


def test_gate_passes_for_a_complete_model_backed_chain(promotable_model_chain) -> None:
    chain = promotable_model_chain(model_ref="ft:aprovado", job_id="ftjob-aprovado")

    gate = get_promotion_gate(
        company_id=chain["company_id"],
        min_pass_rate=0.5,
        model_ref=chain["model_ref"],
        dataset_version_id=chain["dataset_version_id"],
        finetuning_job_id=chain["job_id"],
    )

    assert gate["passed"] is True, gate["reasons"]
    assert gate["finetuned_execution_mode"] == "model_backed"


def test_comparison_rejects_incompatible_runs(company_factory) -> None:
    company_a = company_factory("cmp-a")
    company_b = company_factory("cmp-b")
    _register_cases(company_a)
    _register_cases(company_b)

    run_a = run_evaluation(
        company_id=company_a,
        approach="base",
        reply_generator=build_offline_reply_generator(),
    )
    # Suíte diferente na mesma empresa torna a comparação sem significado.
    register_evaluation_case(
        company_id=company_a,
        case_key="extra",
        scenario_type="general",
        customer_message="Outro caso.",
    )
    run_a2 = run_evaluation(
        company_id=company_a,
        approach="finetuned",
        reply_generator=build_offline_reply_generator(),
        model_ref="ft:x",
    )

    with pytest.raises(EvaluationError, match="not comparable"):
        compare_runs(company_id=company_a, run_ids=[int(run_a["id"]), int(run_a2["id"])])


def test_promotion_gate_blocks_regression_against_baseline(company_factory) -> None:
    company_id = company_factory("eval-gate-regression")
    _register_cases(company_id)
    ingest_document(
        company_id=company_id,
        title="Plano essencial",
        content="O plano essencial custa 199 reais por mês.",
        document_type="price",
        external_ref="plano-essencial",
        approve=True,
    )

    run_evaluation(
        company_id=company_id,
        approach="base",
        reply_generator=build_offline_reply_generator(),
    )
    run_evaluation(
        company_id=company_id,
        approach="finetuned",
        reply_generator=build_offline_reply_generator(default_reply="oi"),
    )

    gate = get_promotion_gate(company_id=company_id, min_pass_rate=0.5)

    assert gate["passed"] is False
    # Execuções simuladas nem chegam a ser consideradas pelo gate.
    assert "missing_promotion_eligible_finetuned_run" in gate["reasons"]


def test_evaluation_runs_are_scoped_by_company(company_factory) -> None:
    company_a = company_factory("eval-tenant-a")
    company_b = company_factory("eval-tenant-b")
    _register_cases(company_a)

    run = run_evaluation(
        company_id=company_a,
        approach="base",
        reply_generator=build_offline_reply_generator(),
    )

    assert evaluation_repository.get_run(company_id=company_b, run_id=int(run["id"])) is None
    assert evaluation_repository.list_cases(company_id=company_b) == []


# --- registro de modelos ----------------------------------------------------------


def test_company_a_can_use_a_different_model_than_company_b(
    promotable_model_chain,
    company_factory,
) -> None:
    company_a = company_factory("model-a")
    company_b = company_factory("model-b")
    chain_a = promotable_model_chain(
        company_id=company_a,
        model_ref="ft:gpt-4o-mini:empresa-a",
        job_id="ftjob-a",
    )
    chain_b = promotable_model_chain(
        company_id=company_b,
        model_ref="ft:gpt-4o-mini:empresa-b",
        job_id="ftjob-b",
    )

    activate_model(company_id=company_a, model_id=int(chain_a["model"]["id"]), activated_by="op")
    activate_model(company_id=company_b, model_id=int(chain_b["model"]["id"]), activated_by="op")

    selection_a = resolve_reply_model(company_id=company_a)
    selection_b = resolve_reply_model(company_id=company_b)

    assert selection_a.model_ref == "ft:gpt-4o-mini:empresa-a"
    assert selection_b.model_ref == "ft:gpt-4o-mini:empresa-b"
    assert selection_a.status == "company_active"


def test_activation_requires_the_full_promotion_chain(company_factory) -> None:
    company_id = company_factory("model-eval-required")
    model = register_candidate_model(company_id=company_id, model_ref="ft:sem-avaliacao")

    with pytest.raises(ModelRegistryError, match="Promotion readiness failed"):
        activate_model(company_id=company_id, model_id=int(model["id"]), activated_by="op")


def test_activation_rejects_a_pending_placeholder_model(company_factory) -> None:
    company_id = company_factory("model-placeholder")
    model = register_candidate_model(
        company_id=company_id,
        model_ref="pending:ftjob-123",
        finetuning_job_id="ftjob-123",
    )

    with pytest.raises(ModelRegistryError, match="Promotion readiness failed"):
        activate_model(company_id=company_id, model_id=int(model["id"]), activated_by="op")


def test_activation_succeeds_with_a_complete_chain(promotable_model_chain) -> None:
    chain = promotable_model_chain()

    activated = activate_model(
        company_id=chain["company_id"],
        model_id=int(chain["model"]["id"]),
        activated_by="op",
    )

    assert activated["status"] == "active"
    assert resolve_reply_model(company_id=chain["company_id"]).model_ref == chain["model_ref"]


def test_rollback_restores_the_previous_model(promotable_model_chain, company_factory) -> None:
    company_id = company_factory("model-rollback")
    first = promotable_model_chain(
        company_id=company_id,
        model_ref="ft:versao-1",
        job_id="ftjob-v1",
    )
    activate_model(company_id=company_id, model_id=int(first["model"]["id"]), activated_by="op")

    second = promotable_model_chain(
        company_id=company_id,
        model_ref="ft:versao-2",
        job_id="ftjob-v2",
    )
    activate_model(company_id=company_id, model_id=int(second["model"]["id"]), activated_by="op")

    assert resolve_reply_model(company_id=company_id).model_ref == "ft:versao-2"

    result = rollback_active_model(company_id=company_id, actor="op")

    assert result["effective_model_ref"] == "ft:versao-1"
    assert resolve_reply_model(company_id=company_id).model_ref == "ft:versao-1"


def test_rollback_falls_back_to_the_base_model(
    monkeypatch,
    promotable_model_chain,
) -> None:
    chain = promotable_model_chain(model_ref="ft:unico", job_id="ftjob-unico")
    activate_model(
        company_id=chain["company_id"],
        model_id=int(chain["model"]["id"]),
        activated_by="op",
    )
    monkeypatch.setattr(settings, "use_finetuned_model_for_replies", False)
    monkeypatch.setattr(settings, "openai_model", "gpt-4o-mini")

    result = rollback_active_model(company_id=chain["company_id"], actor="op")

    assert result["restored_model"] is None
    assert result["effective_model_ref"] == "gpt-4o-mini"
    assert resolve_reply_model(company_id=chain["company_id"]).status == "base_model"


def test_company_without_model_uses_the_global_fallback(monkeypatch, company_factory) -> None:
    company_id = company_factory("model-fallback")
    monkeypatch.setattr(settings, "use_finetuned_model_for_replies", True)
    monkeypatch.setattr(settings, "openai_finetuned_model", "ft:global")

    selection = resolve_reply_model(company_id=company_id)

    assert selection.model_ref == "ft:global"
    assert selection.status == "global_finetuned_fallback"
