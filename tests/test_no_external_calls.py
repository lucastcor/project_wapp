"""Garantia de que a suíte padrão não faz chamada externa.

O socket é bloqueado durante os cenários que tocam LLM, embeddings, avaliação
e fine-tuning. Qualquer tentativa de rede real falha o teste em vez de sair
silenciosamente para a internet.
"""

import socket

import pytest

from app.core.config import settings
from app.llm.embeddings import (
    HashingEmbeddingProvider,
    NullEmbeddingProvider,
    get_embedding_provider,
)
from app.services.evaluation_service import (
    build_offline_reply_generator,
    register_evaluation_case,
    run_evaluation,
)
from app.services.knowledge_document_service import ingest_document
from app.services.knowledge_retrieval_service import retrieve_knowledge


@pytest.fixture()
def blocked_network(monkeypatch):
    def _fail(*args, **kwargs):
        raise AssertionError("Tests must not open network connections.")

    monkeypatch.setattr(socket.socket, "connect", _fail)
    monkeypatch.setattr(socket, "create_connection", _fail)
    yield


def test_default_embedding_provider_is_offline() -> None:
    assert settings.embeddings_provider == "none"
    assert isinstance(get_embedding_provider(), NullEmbeddingProvider)
    assert get_embedding_provider().is_semantic is False


def test_hashing_provider_is_deterministic_and_offline(blocked_network) -> None:
    provider = HashingEmbeddingProvider(dimensions=64)

    first = provider.embed(["orçamento de serviço"])
    second = provider.embed(["orçamento de serviço"])

    assert first == second
    assert len(first[0]) == 64
    assert provider.is_semantic is False


def test_knowledge_ingestion_and_retrieval_work_offline(blocked_network, company_factory) -> None:
    company_id = company_factory("offline-rag")

    ingest_document(
        company_id=company_id,
        title="Formas de pagamento",
        content="Aceitamos pix, cartão de crédito e boleto bancário.",
        document_type="general_faq",
        external_ref="pagamento",
        approve=True,
    )
    result = retrieve_knowledge(company_id=company_id, query="quais as formas de pagamento?")

    assert result.has_relevant_source
    assert result.mode == "lexical"


def test_evaluation_runs_offline(blocked_network, company_factory) -> None:
    company_id = company_factory("offline-eval")
    register_evaluation_case(
        company_id=company_id,
        case_key="offline",
        scenario_type="general",
        customer_message="Vocês atendem aos domingos?",
    )

    run = run_evaluation(
        company_id=company_id,
        approach="base",
        reply_generator=build_offline_reply_generator(
            default_reply="Vou confirmar com a equipe e já te retorno."
        ),
    )

    assert run["total_cases"] == 1


def test_dataset_and_import_pipelines_work_offline(blocked_network, company_factory) -> None:
    from app.services.conversation_import_service import import_conversations
    from app.services.dataset_service import build_dataset_version

    company_id = company_factory("offline-pipeline")

    import_conversations(
        company_id=company_id,
        batch_key="offline-batch",
        source_format="csv",
        raw_payload=(
            "conversation_id,contact_id,role,content,occurred_at\n"
            "c1,5511999990000,cliente,Bom dia,2024-03-12 09:00:00\n"
            "c1,5511999990000,atendente,Bom dia! Como posso ajudar?,2024-03-12 09:01:00\n"
        ),
        dry_run=False,
    )
    version = build_dataset_version(company_id=company_id)

    assert version["id"]


def test_provider_defaults_keep_the_suite_offline() -> None:
    assert settings.classification_provider == "mock"
    assert settings.outbound_provider == "mock"
    assert settings.calendar_provider == "mock"
    assert settings.enable_vector_search is False


# --- Automation Studio ---------------------------------------------------------------


def test_automation_validation_and_simulation_are_offline(blocked_network, company_factory) -> None:
    """Validar e simular nunca tocam rede: são funções puras sobre o grafo."""
    from app.services.automation_workflow_service import (
        simulate_workflow_graph,
        validate_workflow_graph,
    )
    from tests.support.automation_graphs import SAMPLE_MESSAGE_CONTEXT, full_e2e_graph

    company_id = company_factory("offline-automation")

    validated = validate_workflow_graph(company_id=company_id, graph=full_e2e_graph())
    simulated = simulate_workflow_graph(
        company_id=company_id,
        graph=full_e2e_graph(),
        sample_context=SAMPLE_MESSAGE_CONTEXT,
        approval_decisions={"gate": "approved"},
    )

    assert validated["is_valid"] is True
    assert simulated["simulation"]["produced_no_effects"] is True
    assert simulated["simulation"]["status"] == "succeeded"


def test_the_full_automation_run_is_offline(blocked_network, company_factory, monkeypatch) -> None:
    """Evento, dispatch, execução e efeito, tudo sem sair para a internet."""
    from app.automations import engine
    from app.automations.semantics import AiOutput
    from app.core.config import settings as runtime_settings
    from app.services import automation_event_service, automation_execution_service
    from app.services import automation_workflow_service as workflow_service
    from app.services.automation_trigger_service import emit_manual_trigger
    from app.storage import automation_repository
    from app.storage.repository import RuntimeRepository
    from tests.support.automation_graphs import effect_graph

    monkeypatch.setattr(runtime_settings, "automation_engine_enabled", True)
    monkeypatch.setattr(
        engine,
        "_resolve_ai_output",
        lambda **kwargs: AiOutput(text="offline", simulated=False),
    )

    company_id = company_factory("offline-run")
    RuntimeRepository().save_contact("offline-contact", "Contato", company_id=company_id)

    created = workflow_service.create_workflow(
        company_id=company_id,
        workflow_key="fluxo-offline",
        name="Fluxo offline",
    )
    workflow_id = int(created["id"])
    workflow_service.save_draft(
        company_id=company_id,
        workflow_id=workflow_id,
        graph=effect_graph(),
        expected_revision=1,
    )
    workflow_service.publish_workflow(company_id=company_id, workflow_id=workflow_id)
    workflow_service.set_workflow_state(
        company_id=company_id,
        workflow_id=workflow_id,
        state="active",
    )

    emit_manual_trigger(
        company_id=company_id,
        workflow_id=workflow_id,
        triggered_by="tester",
        contact_id="offline-contact",
        nonce="offline-1",
    )
    automation_event_service.dispatch_pending_events()
    for _ in range(10):
        if not automation_execution_service.run_due_jobs(limit=5)["processed"]:
            break

    execution = automation_repository.list_executions(company_id=company_id)[0]
    assert execution["status"] == "succeeded"


def test_the_ai_node_reuses_the_mocked_provider(blocked_network, company_factory) -> None:
    """O nó de IA não cria um segundo caminho de prompt: usa reply_services."""
    from app.automations.domain import WorkflowNode
    from app.automations.engine import _resolve_ai_output

    company_id = company_factory("offline-ai")
    node = WorkflowNode(
        id="compose",
        node_type="ai.composeMessage",
        config={"objective": "Retomar contato com o cliente."},
    )

    output = _resolve_ai_output(company_id=company_id, node=node, context={})

    assert isinstance(output.text, str)
    assert output.simulated is False
