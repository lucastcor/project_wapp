"""Golden tests: treino e inferência precisam falar exatamente a mesma língua.

Se o runtime deixar de fornecer um campo que o dataset preenche, o modelo
treinado recebe em produção uma estrutura diferente da que viu no treino.
"""

import json

import pytest

from app.core.config import settings
from app.core.reply_contract import REPLY_SYSTEM_PROMPT, build_reply_user_content
from app.services import reply_services
from app.services.reply_services import generate_business_reply
from app.services.training_export_service import build_finetuning_record
from app.storage.repository import RuntimeRepository
from tests.conftest import build_stub_company_config

repository = RuntimeRepository()

CONTRACT_FIELD_ORDER = [
    "Nome do contato",
    "Perfil do cliente",
    "Perfil de atendimento",
    "Classificação atual",
    "Última classificação conhecida",
    "Tipo de cenário",
    "Estágio atual do funil",
    "Observações de estilo",
    "Resumo anterior da conversa",
    "Turnos anteriores da conversa",
    "Mensagem atual do contato",
]


def _field_order(block: str) -> list[str]:
    return [
        line.split(":", 1)[0]
        for line in block.splitlines()
        if line and not line.startswith(" ")
    ]


@pytest.fixture()
def captured_provider(monkeypatch):
    """Provider fake que registra os argumentos recebidos."""
    captured: dict = {}

    class FakeProvider:
        def generate_business_reply(self, **kwargs):
            captured.update(kwargs)
            return "resposta"

    monkeypatch.setattr(reply_services, "OpenAIProvider", FakeProvider)
    monkeypatch.setattr(settings, "classification_provider", "openai")
    return captured


def test_training_block_matches_the_canonical_field_order() -> None:
    record = build_finetuning_record(
        {
            "id": 1,
            "profile_name": "default",
            "scenario_type": "lead",
            "customer_profile": "new_customer",
            "customer_message": "Quanto custa?",
            "approved_reply": "Depende do escopo.",
            "style_notes": "Tom consultivo",
            "context": [{"role": "customer", "content": "Oi"}],
        }
    )

    assert record["messages"][0]["content"] == REPLY_SYSTEM_PROMPT
    assert _field_order(record["messages"][1]["content"]) == CONTRACT_FIELD_ORDER


def test_inference_block_matches_the_canonical_field_order() -> None:
    block = build_reply_user_content(
        contact_name="Ana",
        classification="lead_novo",
        last_classification="lead_novo",
        pipeline_stage="novo_lead",
        conversation_summary="Sem histórico",
        customer_message="Quanto custa?",
        scenario_type="lead",
        customer_profile="new_customer",
        profile_name="default",
        style_notes="Tom consultivo",
        conversation_context=[{"role": "customer", "content": "Oi"}],
    )

    assert _field_order(block) == CONTRACT_FIELD_ORDER


def test_runtime_supplies_every_contract_field_it_computed(
    monkeypatch,
    captured_provider,
    company_factory,
) -> None:
    company_id = company_factory("contract-runtime")
    config = build_stub_company_config(company_id=company_id)
    monkeypatch.setattr(reply_services, "get_company_config", lambda company_id: config)

    repository.create_message_example(
        company_id=company_id,
        profile_name="default",
        scenario_type="lead",
        customer_profile="new_customer",
        customer_message="Oi",
        approved_reply="Olá, como posso ajudar?",
        style_notes="Tom acolhedor e direto",
        quality_score=9.0,
        approval_status="approved",
    )

    generate_business_reply(
        classification="lead_novo",
        message="Quanto custa o serviço?",
        contact_name="Ana",
        company_id=company_id,
        customer_profile="novo",
        conversation_context=[{"role": "customer", "content": "Bom dia"}],
    )

    # O runtime calculou esses valores: eles não podem chegar vazios ao provider.
    assert captured_provider["scenario_type"] == "lead"
    assert captured_provider["customer_profile"] == "new_customer"
    assert captured_provider["profile_name"] == "default"
    assert captured_provider["style_notes"] == "Tom acolhedor e direto"
    assert captured_provider["conversation_context"] == [
        {"role": "customer", "content": "Bom dia"}
    ]


def test_runtime_and_training_blocks_are_structurally_identical(
    monkeypatch,
    captured_provider,
    company_factory,
) -> None:
    company_id = company_factory("contract-parity")
    config = build_stub_company_config(company_id=company_id)
    monkeypatch.setattr(reply_services, "get_company_config", lambda company_id: config)

    generate_business_reply(
        classification="lead_novo",
        message="Quanto custa o serviço?",
        contact_name="Ana",
        company_id=company_id,
        customer_profile="novo",
        conversation_context=[{"role": "customer", "content": "Bom dia"}],
    )

    runtime_block = build_reply_user_content(
        contact_name="Ana",
        classification="lead_novo",
        last_classification=None,
        pipeline_stage=None,
        conversation_summary=None,
        customer_message="Quanto custa o serviço?",
        scenario_type=captured_provider["scenario_type"],
        customer_profile=captured_provider["customer_profile"],
        profile_name=captured_provider["profile_name"],
        style_notes=captured_provider["style_notes"],
        conversation_context=captured_provider["conversation_context"],
    )
    training_block = build_finetuning_record(
        {
            "id": 1,
            "profile_name": captured_provider["profile_name"],
            "scenario_type": captured_provider["scenario_type"],
            "customer_profile": captured_provider["customer_profile"],
            "customer_message": "Quanto custa o serviço?",
            "approved_reply": "Depende do escopo.",
            "style_notes": captured_provider["style_notes"],
            "context": captured_provider["conversation_context"],
        }
    )["messages"][1]["content"]

    assert _field_order(runtime_block) == _field_order(training_block)


def test_missing_values_are_explicit_not_absent() -> None:
    block = build_reply_user_content(customer_message="Oi")

    assert _field_order(block) == CONTRACT_FIELD_ORDER
    assert block.count("não informado") >= 8


def test_prompt_contract_version_is_recorded_on_runs(company_factory) -> None:
    from app.services.evaluation_service import (
        PROMPT_CONTRACT_VERSION,
        build_offline_reply_generator,
        register_evaluation_case,
        run_evaluation,
    )

    company_id = company_factory("contract-version")
    register_evaluation_case(
        company_id=company_id,
        case_key="contrato",
        scenario_type="general",
        customer_message="Pergunta.",
    )
    run = run_evaluation(
        company_id=company_id,
        approach="base",
        reply_generator=build_offline_reply_generator(),
    )

    assert run["prompt_contract_version"] == PROMPT_CONTRACT_VERSION
    assert run["case_suite_hash"]
    assert run["retrieval_config_version"]


def test_training_record_is_valid_jsonl(company_factory) -> None:
    record = build_finetuning_record(
        {
            "id": 1,
            "profile_name": "default",
            "scenario_type": "lead",
            "customer_message": "Oi",
            "approved_reply": "Olá",
            "context": [{"role": "agent", "content": "Bom dia"}],
        }
    )
    line = json.dumps(record, ensure_ascii=False)

    parsed = json.loads(line)
    assert [message["role"] for message in parsed["messages"]] == [
        "system",
        "user",
        "assistant",
    ]
