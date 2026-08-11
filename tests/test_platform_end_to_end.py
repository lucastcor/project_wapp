"""Fluxo completo pela API: canal → contexto → knowledge → import → revisão
→ dataset → avaliação → readiness → modelo → rollback.

Nenhuma chamada externa: providers ficam em mock e a avaliação usa o gerador
offline determinístico.
"""

import csv
import io

import pytest
from fastapi.testclient import TestClient

from app.core.company_context import get_seed_company_context
from app.core.config import settings
from app.main import app
from app.services.company_config_service import invalidate_company_config

client = TestClient(app)

# Conversas com assunto genuinamente distinto: conteúdo uniforme demais seria
# colapsado pela detecção de near-duplicate, que é comportamento desejado.
CONVERSATION_TOPICS = [
    ("qual o horario de funcionamento aos sabados", "Aos sabados abrimos das nove ate as treze horas"),
    ("voces fazem entrega na zona sul", "Entregamos na zona sul com taxa fixa combinada na contratacao"),
    ("preciso remarcar meu compromisso da terca", "Consigo remarcar sua terca, me diga qual novo dia prefere"),
    ("quais formas de pagamento voces aceitam", "Aceitamos pix, cartao de credito e transferencia bancaria"),
    ("existe garantia sobre o servico prestado", "Sim, o servico tem garantia formal registrada em contrato"),
    ("quanto tempo demora o procedimento completo", "O procedimento completo costuma levar cerca de duas horas"),
    ("posso levar acompanhante no atendimento", "Pode levar acompanhante, basta avisar na recepcao ao chegar"),
    ("voces atendem convenio empresarial", "Atendemos convenio empresarial mediante cadastro previo da empresa"),
    ("qual documento devo levar no primeiro dia", "Traga um documento com foto e o comprovante do agendamento"),
    ("tem estacionamento proprio no local", "Temos estacionamento proprio gratuito no subsolo do predio"),
    ("consigo antecipar meu atendimento", "Posso te colocar na fila de antecipacao e aviso se abrir vaga"),
    ("voces emitem nota para pessoa juridica", "Emitimos nota para pessoa juridica no fechamento do mes"),
    ("preciso cancelar e queria saber a regra", "Cancelamento com vinte e quatro horas de antecedencia nao gera custo"),
    ("qual profissional vai me atender amanha", "Amanha o atendimento fica com a equipe do turno da manha"),
]

def _build_csv_export() -> str:
    """CSV com aspas de verdade.

    A versão anterior montava as linhas por f-string, e 4 dos 14 tópicos têm
    vírgula no texto ("Aceitamos pix, cartao de credito..."). Isso deslocava
    as colunas e o conteúdo da mensagem caía no campo `occurred_at`. O teste
    passava porque o parser devolvia o texto cru e o SQLite aceitava qualquer
    coisa numa coluna TIMESTAMP — a corrupção mascarava o defeito da fixture.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["conversation_id", "contact_id", "role", "content", "occurred_at"])
    for index, (question, answer) in enumerate(CONVERSATION_TOPICS):
        contact = f"551199900{index:04d}"
        writer.writerow(
            [f"conv-{index}", contact, "cliente", question, f"2024-03-{index + 1:02d} 09:00:00"]
        )
        writer.writerow(
            [f"conv-{index}", contact, "atendente", answer, f"2024-03-{index + 1:02d} 09:01:00"]
        )
    return buffer.getvalue().rstrip("\n")


CSV_EXPORT = _build_csv_export()


@pytest.fixture()
def tenant(company_factory):
    company_id = company_factory("e2e")
    invalidate_company_config()
    return company_id


def _params(company_id: int) -> dict:
    return {"company_id": company_id}


def test_full_governed_pipeline(tenant, monkeypatch) -> None:
    monkeypatch.setattr(settings, "finetuning_min_examples", 8)
    monkeypatch.setattr(settings, "finetuning_min_scenarios", 1)
    monkeypatch.setattr(settings, "finetuning_min_examples_per_scenario", 4)
    monkeypatch.setattr(settings, "finetuning_min_eval_pass_rate", 0.5)
    params = _params(tenant)

    # 1. Vínculo de canal
    binding = client.post(
        "/admin/channel-bindings",
        params=params,
        json={"provider": "whatsapp", "external_id": f"phone-{tenant}", "label": "Principal"},
    )
    assert binding.status_code == 200
    assert client.get("/admin/channel-bindings", params=params).json()[0]["active"] == 1

    # 2. Company context versionado com fine-tuning habilitado
    context = get_seed_company_context(None)
    version = client.post(
        "/admin/company-config/versions",
        params=params,
        json={
            "context": {**context, "company_name": "Empresa E2E"},
            "capabilities": {"finetuning": True},
            "change_note": "setup e2e",
            "publish": True,
        },
    )
    assert version.status_code == 200
    assert version.json()["version"] == 1

    config = client.get("/admin/company-config", params=params).json()
    assert config["context"]["company_name"] == "Empresa E2E"
    assert config["capabilities"]["finetuning"] is True
    assert config["source"] == "database"

    # 3. Knowledge aprovado e recuperável
    document = client.post(
        "/admin/knowledge/documents",
        params=params,
        json={
            "title": "Horário de atendimento",
            "content": "Atendemos de segunda a sexta das 9h às 18h.",
            "document_type": "schedule_rule",
            "external_ref": "horario",
            "approve": True,
        },
    )
    assert document.status_code == 200
    assert document.json()["chunk_count"] >= 1

    search = client.post(
        "/admin/knowledge/search",
        params=params,
        json={"query": "qual o horário de atendimento?"},
    ).json()
    assert search["has_relevant_source"] is True
    assert search["sources"][0]["chunk_id"] == search["chunks"][0]["chunk_id"]

    # 4. Importação em dry-run e depois persistida
    dry_run = client.post(
        "/admin/conversation-import",
        params=params,
        json={
            "batch_key": "e2e-lote",
            "source_format": "csv",
            "raw_text": CSV_EXPORT,
            "dry_run": True,
        },
    ).json()
    assert dry_run["dry_run"] is True
    assert dry_run["batch_id"] is None
    assert client.get("/admin/conversation-import/batches", params=params).json() == []

    imported = client.post(
        "/admin/conversation-import",
        params=params,
        json={
            "batch_key": "e2e-lote",
            "source_format": "csv",
            "raw_text": CSV_EXPORT,
            "dry_run": False,
        },
    ).json()
    assert imported["candidates_created"] == 14

    # Reimportar é idempotente.
    again = client.post(
        "/admin/conversation-import",
        params=params,
        json={
            "batch_key": "e2e-lote",
            "source_format": "csv",
            "raw_text": CSV_EXPORT,
            "dry_run": False,
        },
    ).json()
    assert again["candidates_created"] == 0
    assert again["batch_id"] == imported["batch_id"]

    # 5. Revisão humana promove os candidatos
    candidates = client.get(
        "/admin/training-candidates",
        params={**params, "review_status": "pending"},
    ).json()
    assert len(candidates) == 14

    for candidate in candidates:
        review = client.post(
            f"/admin/training-candidates/{candidate['id']}/review",
            params=params,
            json={"approved": True, "scenario_type": "lead", "quality_score": 9.0},
        )
        assert review.status_code == 200
        assert review.json()["promoted_message_example_id"] is not None

    assert client.get(
        "/admin/training-candidates",
        params={**params, "review_status": "pending"},
    ).json() == []

    # 6. Dataset versionado, sem vazamento entre splits
    dataset = client.post(
        "/admin/dataset-versions",
        params=params,
        json={"name": "e2e", "seed": 42, "validation_ratio": 0.2, "holdout_ratio": 0.1},
    ).json()
    dataset_id = dataset["id"]
    assert dataset["status"] == "draft"
    assert dataset["stats"]["total_items"] == 14

    leakage = client.get(f"/admin/dataset-versions/{dataset_id}/leakage", params=params).json()
    assert leakage["has_leakage"] is False

    # 7. Readiness bloqueia enquanto a versão não está congelada
    readiness = client.get(
        f"/admin/dataset-versions/{dataset_id}/readiness",
        params={**params, "require_evaluation": False},
    ).json()
    assert "dataset_frozen" in readiness["blocking_failures"]

    frozen = client.post(f"/admin/dataset-versions/{dataset_id}/freeze", params=params).json()
    assert frozen["status"] == "frozen"

    # Versão congelada é imutável.
    rebuild = client.post(f"/admin/dataset-versions/{dataset_id}/rebuild", params=params)
    assert rebuild.status_code == 409

    # 8. Avaliação comparando base e fine-tuned
    client.post(
        "/admin/evaluation/cases",
        params=params,
        json={
            "case_key": "e2e-caso",
            "scenario_type": "lead",
            "customer_message": "Qual o horário de atendimento?",
            "expected_keywords": ["9h"],
        },
    )
    base_run = client.post(
        "/admin/evaluation/runs",
        params=params,
        json={"approach": "base_rag"},
    ).json()
    finetuned_run = client.post(
        "/admin/evaluation/runs",
        params=params,
        json={"approach": "finetuned"},
    ).json()

    comparison = client.post(
        "/admin/evaluation/compare",
        params=params,
        json={"run_ids": [base_run["id"], finetuned_run["id"]]},
    ).json()
    assert len(comparison["runs"]) == 2

    # A rota de avaliação é sempre simulação: ela não executa modelo algum.
    assert base_run["execution_mode"] == "simulation"
    assert int(finetuned_run["promotion_eligible"]) == 0
    assert all(run["promotion_eligible"] is False for run in comparison["runs"])

    # Portanto o gate de promoção continua fechado, mesmo com pass rate alto.
    gate = client.get("/admin/evaluation/promotion-gate", params=params).json()
    assert gate["passed"] is False
    assert "missing_promotion_eligible_finetuned_run" in gate["reasons"]

    # 9. Preflight aprovado, sem efeito externo
    preflight = client.post(
        "/admin/finetuning/preflight",
        params=params,
        json={"dataset_version_id": dataset_id},
    ).json()
    assert preflight["is_ready"] is True, preflight["blocking_failures"]

    # 10. Nenhum job real é criado: sem credencial e com o pipeline desligado,
    # a rota recusa antes de qualquer efeito externo.
    job = client.post(
        "/admin/finetuning/jobs",
        params=params,
        json={"dataset_version_id": dataset_id},
    )
    assert job.status_code == 400
    detail = job.json()["detail"]
    assert "OPENAI_API_KEY is not configured" in detail or "disabled" in detail
    assert client.get("/admin/finetuning/jobs-db", params=params).json() == []

    # 11. Ativação de modelo exige a cadeia real, não só um run_id qualquer
    candidate = client.post(
        "/admin/models",
        params=params,
        json={"model_ref": "ft:e2e-v1", "evaluation_run_id": finetuned_run["id"]},
    ).json()

    denied = client.post(f"/admin/models/{candidate['id']}/activate", params=params)
    assert denied.status_code == 409
    assert "Promotion readiness failed" in denied.json()["detail"]

    # Nenhum modelo foi ativado: o runtime continua no modelo base.
    models = client.get("/admin/models", params=params).json()
    assert models["effective_model_status"] == "base_model"
    assert all(model["status"] != "active" for model in models["models"])


def test_activation_and_rollback_require_a_real_chain(promotable_model_chain) -> None:
    chain = promotable_model_chain(model_ref="ft:e2e-real", job_id="ftjob-e2e-real")
    params = _params(chain["company_id"])

    activated = client.post(
        f"/admin/models/{chain['model']['id']}/activate",
        params=params,
    )
    assert activated.status_code == 200
    assert client.get("/admin/models", params=params).json()["effective_model_ref"] == "ft:e2e-real"

    rollback = client.post("/admin/models/rollback", params=params).json()
    assert rollback["restored_model"] is None
    assert client.get("/admin/models", params=params).json()["effective_model_status"] == "base_model"


def test_pipeline_artifacts_are_invisible_to_another_company(tenant, company_factory) -> None:
    other_company = company_factory("e2e-outra")
    params = _params(tenant)
    other_params = _params(other_company)

    client.post(
        "/admin/knowledge/documents",
        params=params,
        json={
            "title": "Segredo comercial",
            "content": "A margem praticada nesta empresa é de trinta por cento.",
            "document_type": "business_rule",
            "external_ref": "margem",
            "approve": True,
        },
    )

    assert client.get("/admin/knowledge/documents", params=other_params).json() == []
    other_search = client.post(
        "/admin/knowledge/search",
        params=other_params,
        json={"query": "qual a margem praticada?"},
    ).json()
    assert other_search["chunks"] == []
    assert client.get("/admin/dataset-versions", params=other_params).json() == []
    assert client.get("/admin/training-candidates", params=other_params).json() == []
    assert client.get("/admin/evaluation/runs", params=other_params).json() == []
    assert client.get("/admin/models", params=other_params).json()["models"] == []
