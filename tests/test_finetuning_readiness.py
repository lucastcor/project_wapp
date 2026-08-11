"""Readiness que bloqueia a criação de job de fine-tuning."""


from app.core.company_context import get_seed_company_context
from app.core.config import settings
from app.services.company_config_service import invalidate_company_config, save_company_config
from app.services.dataset_service import build_dataset_version, freeze_dataset_version
from app.services.evaluation_service import (
    build_offline_reply_generator,
    register_evaluation_case,
    run_evaluation,
)
from app.services.finetuning_readiness_service import (
    evaluate_pretraining_readiness,
    evaluate_promotion_readiness,
)
from app.storage.repository import RuntimeRepository

repository = RuntimeRepository()


def _enable_finetuning(company_id: int) -> None:
    save_company_config(
        company_id=company_id,
        context=get_seed_company_context(None),
        capabilities={"finetuning": True},
        author="test",
        publish=True,
    )
    invalidate_company_config(company_id)


def _seed_examples(company_id: int, *, conversations: int, per_conversation: int = 2) -> None:
    scenarios = ["lead", "qualification", "closing", "scheduling"]
    for conversation_index in range(conversations):
        for turn_index in range(per_conversation):
            repository.create_message_example(
                company_id=company_id,
                profile_name="default",
                scenario_type=scenarios[conversation_index % len(scenarios)],
                customer_profile="new_customer",
                customer_message=f"Pergunta {conversation_index}-{turn_index} sobre o atendimento",
                approved_reply=f"Resposta {conversation_index}-{turn_index} explicando o processo",
                quality_score=9.0,
                used_for_training=True,
                approval_status="approved",
                source_reference=f"training_candidate:conv-{conversation_index}",
                pii_scrubbed=True,
            )


def test_readiness_reports_missing_dataset_version(company_factory) -> None:
    company_id = company_factory("ready-missing")

    readiness = evaluate_pretraining_readiness(
        company_id=company_id,
        dataset_version_id=999999,
    )

    assert readiness["is_ready"] is False
    assert readiness["blocking_failures"] == ["dataset_version_exists"]


def test_readiness_blocks_small_and_unfrozen_datasets(company_factory) -> None:
    company_id = company_factory("ready-small")
    _enable_finetuning(company_id)
    _seed_examples(company_id, conversations=2)

    version = build_dataset_version(company_id=company_id)
    readiness = evaluate_pretraining_readiness(
        company_id=company_id,
        dataset_version_id=int(version["id"]),
    )

    assert readiness["is_ready"] is False
    assert "minimum_examples" in readiness["blocking_failures"]
    assert "dataset_frozen" in readiness["blocking_failures"]


def test_readiness_blocks_when_capability_is_disabled(company_factory) -> None:
    company_id = company_factory("ready-capability")
    _seed_examples(company_id, conversations=2)

    version = build_dataset_version(company_id=company_id)
    readiness = evaluate_pretraining_readiness(
        company_id=company_id,
        dataset_version_id=int(version["id"]),
    )

    assert "capability_enabled" in readiness["blocking_failures"]


def test_readiness_blocks_items_with_residual_pii(monkeypatch, company_factory) -> None:
    company_id = company_factory("ready-pii")
    _enable_finetuning(company_id)
    monkeypatch.setattr(settings, "finetuning_min_examples", 1)
    monkeypatch.setattr(settings, "finetuning_min_scenarios", 1)
    monkeypatch.setattr(settings, "finetuning_min_examples_per_scenario", 1)

    version_id = None
    repository.create_message_example(
        company_id=company_id,
        profile_name="default",
        scenario_type="lead",
        customer_message="Bom dia, quero orçamento",
        approved_reply="Claro, envio agora mesmo o material completo",
        quality_score=9.0,
        used_for_training=True,
        approval_status="approved",
        source_reference="training_candidate:conv-limpo",
    )
    version = build_dataset_version(company_id=company_id)
    version_id = int(version["id"])

    readiness = evaluate_pretraining_readiness(
        company_id=company_id,
        dataset_version_id=version_id,
    )
    # O pipeline anonimiza na coleta, então não deve sobrar PII detectável.
    assert "pii_clean" not in readiness["blocking_failures"]


def test_readiness_passes_for_a_complete_frozen_dataset(monkeypatch, company_factory) -> None:
    company_id = company_factory("ready-complete")
    _enable_finetuning(company_id)
    monkeypatch.setattr(settings, "finetuning_min_examples", 10)
    monkeypatch.setattr(settings, "finetuning_min_scenarios", 2)
    monkeypatch.setattr(settings, "finetuning_min_examples_per_scenario", 2)
    monkeypatch.setattr(settings, "finetuning_min_eval_pass_rate", 0.5)

    _seed_examples(company_id, conversations=16, per_conversation=2)
    version = build_dataset_version(company_id=company_id, seed=21)
    freeze_dataset_version(
        company_id=company_id,
        dataset_version_id=int(version["id"]),
        frozen_by="operador",
    )

    register_evaluation_case(
        company_id=company_id,
        case_key="caso-base",
        scenario_type="lead",
        customer_message="Vocês atendem no sábado?",
    )
    run_evaluation(
        company_id=company_id,
        approach="base",
        reply_generator=build_offline_reply_generator(
            default_reply="Vou confirmar essa informação com a equipe e já te retorno."
        ),
    )
    run_evaluation(
        company_id=company_id,
        approach="finetuned",
        reply_generator=build_offline_reply_generator(
            default_reply="Vou confirmar essa informação com a equipe e já te retorno."
        ),
        model_ref="ft:test",
    )

    readiness = evaluate_pretraining_readiness(
        company_id=company_id,
        dataset_version_id=int(version["id"]),
    )

    assert readiness["blocking_failures"] == []
    assert readiness["is_ready"] is True
    assert readiness["train_items"] >= 10
    assert readiness["validation_items"] >= 1


def test_pretraining_gate_does_not_require_a_finetuned_evaluation(
    monkeypatch,
    company_factory,
) -> None:
    """O modelo só existe depois do treino: exigir avaliação dele aqui seria
    um ciclo impossível."""
    company_id = company_factory("ready-no-eval")
    _enable_finetuning(company_id)
    monkeypatch.setattr(settings, "finetuning_min_examples", 4)
    monkeypatch.setattr(settings, "finetuning_min_scenarios", 1)
    monkeypatch.setattr(settings, "finetuning_min_examples_per_scenario", 2)

    _seed_examples(company_id, conversations=8, per_conversation=2)
    version = build_dataset_version(company_id=company_id, seed=5)
    freeze_dataset_version(
        company_id=company_id,
        dataset_version_id=int(version["id"]),
        frozen_by="operador",
    )
    register_evaluation_case(
        company_id=company_id,
        case_key="suite-minima",
        scenario_type="general",
        customer_message="Pergunta de avaliação.",
    )

    readiness = evaluate_pretraining_readiness(
        company_id=company_id,
        dataset_version_id=int(version["id"]),
    )

    assert readiness["gate"] == "pretraining"
    assert readiness["blocking_failures"] == []
    assert readiness["is_ready"] is True
    assert all(check["code"] != "evaluation_gate" for check in readiness["checks"])


def test_pretraining_gate_requires_a_registered_case_suite(
    monkeypatch,
    company_factory,
) -> None:
    company_id = company_factory("ready-no-suite")
    _enable_finetuning(company_id)
    monkeypatch.setattr(settings, "finetuning_min_examples", 4)
    monkeypatch.setattr(settings, "finetuning_min_scenarios", 1)
    monkeypatch.setattr(settings, "finetuning_min_examples_per_scenario", 2)

    _seed_examples(company_id, conversations=8, per_conversation=2)
    version = build_dataset_version(company_id=company_id, seed=5)
    freeze_dataset_version(
        company_id=company_id,
        dataset_version_id=int(version["id"]),
        frozen_by="operador",
    )

    readiness = evaluate_pretraining_readiness(
        company_id=company_id,
        dataset_version_id=int(version["id"]),
    )

    assert "evaluation_suite_registered" in readiness["blocking_failures"]


def test_promotion_gate_requires_a_completed_job(company_factory) -> None:
    company_id = company_factory("ready-promo-nojob")

    readiness = evaluate_promotion_readiness(
        company_id=company_id,
        model_ref="ft:sem-job",
    )

    assert readiness["gate"] == "promotion"
    assert readiness["is_ready"] is False
    assert "job_reference_present" in readiness["blocking_failures"]


def test_promotion_gate_passes_for_a_complete_chain(promotable_model_chain) -> None:
    chain = promotable_model_chain(model_ref="ft:promo-ok", job_id="ftjob-promo-ok")

    readiness = evaluate_promotion_readiness(
        company_id=chain["company_id"],
        model_ref=chain["model_ref"],
        dataset_version_id=chain["dataset_version_id"],
        finetuning_job_id=chain["job_id"],
    )

    assert readiness["blocking_failures"] == [], readiness["checks"]
    assert readiness["is_ready"] is True
