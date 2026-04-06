

import json

from app.services import training_export_service


class FakeRepository:
    def get_exportable_message_examples(self, company_id: int | None = None) -> list[dict]:
        return [
            {
                "id": 1,
                "profile_name": "default",
                "scenario_type": "lead",
                "customer_message": "Olá, queria entender melhor como funciona o atendimento.",
                "approved_reply": "Claro. Posso te explicar como funciona e te orientar pela melhor opção para o seu caso.",
                "style_notes": "Tom consultivo, claro e natural.",
                "quality_score": 9.5,
                "used_for_training": 1,
                "active": 1,
                "created_at": "2026-03-14 10:00:00",
            },
            {
                "id": 2,
                "profile_name": "default",
                "scenario_type": "lead",
                "customer_message": "Olá, queria entender melhor como funciona o atendimento.",
                "approved_reply": "Claro. Posso te explicar como funciona e te orientar pela melhor opção para o seu caso.",
                "style_notes": "Tom consultivo, claro e natural.",
                "quality_score": 9.4,
                "used_for_training": 1,
                "active": 1,
                "created_at": "2026-03-14 10:01:00",
            },
            {
                "id": 3,
                "profile_name": "default",
                "scenario_type": "support",
                "customer_message": "Não consigo acessar a plataforma.",
                "approved_reply": "Entendi. Vamos verificar isso juntos.",
                "style_notes": "Tom técnico e acolhedor.",
                "quality_score": 9.7,
                "used_for_training": 1,
                "active": 1,
                "created_at": "2026-03-14 10:02:00",
            },
            {
                "id": 4,
                "profile_name": "default",
                "scenario_type": "qualification",
                "customer_message": "Queria saber valores e como funciona o atendimento.",
                "approved_reply": "Consigo te orientar sim. Os valores variam conforme a necessidade. Me conta só um pouco do que você está buscando.",
                "style_notes": "Puxa contexto antes de falar de preço.",
                "quality_score": 7.0,
                "used_for_training": 1,
                "active": 1,
                "created_at": "2026-03-14 10:03:00",
            },
            {
                "id": 5,
                "profile_name": "default",
                "scenario_type": "closing",
                "customer_message": "Gostei e quero seguir. Qual é o próximo passo?",
                "approved_reply": "Perfeito. O próximo passo é me confirmar algumas informações para eu te orientar da forma correta.",
                "style_notes": "Tom seguro e objetivo.",
                "quality_score": 9.2,
                "used_for_training": 1,
                "active": 1,
                "created_at": "2026-03-14 10:04:00",
            },
        ]


def test_get_curated_training_examples_filters_and_deduplicates(monkeypatch) -> None:
    monkeypatch.setattr(training_export_service, "repository", FakeRepository())

    curated_examples = training_export_service.get_curated_training_examples()

    assert len(curated_examples) == 2
    scenario_types = {example["scenario_type"] for example in curated_examples}
    example_ids = {example["id"] for example in curated_examples}

    assert scenario_types == {"lead", "closing"}
    assert 1 in example_ids
    assert 5 in example_ids
    assert 2 not in example_ids
    assert 3 not in example_ids
    assert 4 not in example_ids


def test_get_exportable_training_records_returns_preview_records(monkeypatch) -> None:
    monkeypatch.setattr(training_export_service, "repository", FakeRepository())

    records = training_export_service.get_exportable_training_records()

    assert len(records) == 2
    first_record = records[0]

    assert "messages" in first_record
    assert "metadata" in first_record
    assert first_record["messages"][0]["role"] == "system"
    assert first_record["messages"][1]["role"] == "user"
    assert first_record["messages"][2]["role"] == "assistant"


def test_get_finetuning_split_records_returns_train_and_validation(monkeypatch) -> None:
    monkeypatch.setattr(training_export_service, "repository", FakeRepository())

    split_records = training_export_service.get_finetuning_split_records(validation_ratio=0.5, seed=123)

    assert "train" in split_records
    assert "validation" in split_records
    assert len(split_records["train"]) == 1
    assert len(split_records["validation"]) == 1

    for record in split_records["train"] + split_records["validation"]:
        assert "messages" in record
        assert "metadata" not in record
        assert [message["role"] for message in record["messages"]] == ["system", "user", "assistant"]


def test_export_finetuning_train_jsonl_returns_valid_jsonl(monkeypatch) -> None:
    monkeypatch.setattr(training_export_service, "repository", FakeRepository())

    jsonl_output = training_export_service.export_finetuning_train_jsonl(validation_ratio=0.5, seed=123)

    lines = [line for line in jsonl_output.splitlines() if line.strip()]
    assert len(lines) == 1

    parsed = json.loads(lines[0])
    assert "messages" in parsed
    assert [message["role"] for message in parsed["messages"]] == ["system", "user", "assistant"]
