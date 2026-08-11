"""Alinhamento treino/inferência e ligação do customer_profile ao estilo."""

import json

from app.core.reply_contract import REPLY_SYSTEM_PROMPT, build_reply_user_content
from app.services import training_export_service
from app.services.style_service import (
    format_style_examples_for_prompt,
    infer_style_scenario_type,
    select_style_examples,
)
from app.storage.repository import RuntimeRepository

repository = RuntimeRepository()


# --- contrato de prompt ------------------------------------------------------------


def test_training_record_uses_the_runtime_system_prompt() -> None:
    record = training_export_service.build_finetuning_record(
        {
            "id": 1,
            "profile_name": "default",
            "scenario_type": "lead",
            "customer_profile": "new_customer",
            "customer_message": "Quanto custa?",
            "approved_reply": "Depende do escopo, me conta mais?",
            "style_notes": "Tom consultivo",
        }
    )

    assert record["messages"][0]["role"] == "system"
    assert record["messages"][0]["content"] == REPLY_SYSTEM_PROMPT


def test_training_user_block_matches_the_inference_contract() -> None:
    training_record = training_export_service.build_finetuning_record(
        {
            "id": 1,
            "profile_name": "default",
            "scenario_type": "lead",
            "customer_profile": "new_customer",
            "customer_message": "Quanto custa?",
            "approved_reply": "Depende do escopo.",
            "style_notes": "Tom consultivo",
        }
    )
    training_user_block = training_record["messages"][1]["content"]

    inference_user_block = build_reply_user_content(
        contact_name="Ana",
        classification="lead_novo",
        last_classification="lead_novo",
        pipeline_stage="novo_lead",
        conversation_summary="Sem histórico",
        customer_message="Quanto custa?",
    )

    # Os dois blocos precisam ter a mesma estrutura de campos, na mesma ordem.
    training_fields = [line.split(":", 1)[0] for line in training_user_block.splitlines()]
    inference_fields = [line.split(":", 1)[0] for line in inference_user_block.splitlines()]

    assert training_fields == inference_fields
    assert training_fields[0] == "Nome do contato"
    assert training_fields[-1] == "Mensagem atual do contato"


def test_absent_fields_keep_their_position_in_the_contract() -> None:
    content = build_reply_user_content(customer_message="Oi")

    assert "Perfil do cliente: não informado" in content
    assert "Tipo de cenário: não informado" in content
    assert "Mensagem atual do contato: Oi" in content


def test_multi_turn_context_is_rendered_in_the_contract() -> None:
    content = build_reply_user_content(
        customer_message="E o prazo?",
        conversation_context=[
            {"role": "customer", "content": "Quanto custa?"},
            {"role": "agent", "content": "Depende do escopo."},
        ],
    )

    assert "Turnos anteriores da conversa:" in content
    assert "customer: Quanto custa?" in content
    assert "agent: Depende do escopo." in content


def test_training_record_anonymizes_pii() -> None:
    record = training_export_service.build_finetuning_record(
        {
            "id": 1,
            "profile_name": "default",
            "scenario_type": "lead",
            "customer_message": "Meu email é cliente@empresa.com",
            "approved_reply": "Enviei para cliente@empresa.com",
        }
    )

    serialized = json.dumps(record, ensure_ascii=False)
    assert "cliente@empresa.com" not in serialized
    assert "[EMAIL]" in serialized


# --- seleção de estilo -----------------------------------------------------------------


def test_customer_profile_drives_style_selection(company_factory) -> None:
    company_id = company_factory("style-profile")

    repository.create_message_example(
        company_id=company_id,
        profile_name="default",
        scenario_type="lead",
        customer_profile="new_customer",
        customer_message="Oi",
        approved_reply="Resposta para cliente novo",
        quality_score=9.0,
    )
    repository.create_message_example(
        company_id=company_id,
        profile_name="default",
        scenario_type="lead",
        customer_profile="vip",
        customer_message="Oi",
        approved_reply="Resposta para cliente VIP",
        quality_score=9.5,
    )

    new_customer = select_style_examples(
        company_id=company_id,
        scenario_type="lead",
        customer_profile="novo",
    )
    vip = select_style_examples(
        company_id=company_id,
        scenario_type="lead",
        customer_profile="premium",
    )

    assert new_customer.strategy == "customer_profile+scenario"
    assert new_customer.examples[0]["approved_reply"] == "Resposta para cliente novo"
    assert vip.examples[0]["approved_reply"] == "Resposta para cliente VIP"


def test_style_selection_falls_back_predictably(company_factory) -> None:
    company_id = company_factory("style-fallback")

    repository.create_message_example(
        company_id=company_id,
        profile_name="default",
        scenario_type="closing",
        customer_message="Vamos fechar",
        approved_reply="Ótimo, vou preparar tudo",
        quality_score=9.0,
    )

    # Perfil sem exemplo próprio cai para o cenário.
    selection = select_style_examples(
        company_id=company_id,
        scenario_type="closing",
        customer_profile="vip",
    )
    assert selection.strategy == "profile_name+scenario"
    assert selection.examples[0]["approved_reply"] == "Ótimo, vou preparar tudo"

    # Sem nada compatível, cai para o default da empresa.
    unrelated = select_style_examples(
        company_id=company_id,
        scenario_type="reminder",
        customer_profile="vip",
    )
    assert unrelated.strategy == "company_default"


def test_inactive_examples_are_never_used_as_few_shot(company_factory) -> None:
    company_id = company_factory("style-inactive")

    repository.create_message_example(
        company_id=company_id,
        profile_name="default",
        scenario_type="lead",
        customer_message="Oi",
        approved_reply="Exemplo desativado",
        active=False,
    )

    selection = select_style_examples(company_id=company_id, scenario_type="lead")
    assert selection.examples == []


def test_few_shot_use_is_independent_from_training_release(company_factory) -> None:
    company_id = company_factory("style-independent")

    repository.create_message_example(
        company_id=company_id,
        profile_name="default",
        scenario_type="lead",
        customer_message="Oi",
        approved_reply="Serve como few-shot mas não vai para treino",
        quality_score=9.0,
        active=True,
        used_for_training=False,
    )

    assert select_style_examples(company_id=company_id, scenario_type="lead").examples
    assert repository.get_exportable_message_examples(company_id=company_id) == []


def test_scenario_inference_uses_the_canonical_taxonomy() -> None:
    assert infer_style_scenario_type("suporte", "aguardando_confirmacao") == "support"
    assert infer_style_scenario_type("lead_novo", "qualificando") == "qualification"


def test_prompt_rendering_includes_the_customer_profile() -> None:
    rendered = format_style_examples_for_prompt(
        [
            {
                "profile_name": "default",
                "customer_profile": "vip",
                "scenario_type": "lead",
                "customer_message": "Oi",
                "approved_reply": "Olá",
                "style_notes": "",
            }
        ]
    )

    assert "Perfil do cliente: vip" in rendered
