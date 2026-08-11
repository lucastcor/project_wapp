"""Lineage da importação, idempotência da aprovação e contexto multi-turno."""

import json

import pytest

from app.core.config import settings
from app.services.conversation_import_service import (
    CandidateStateError,
    import_conversations,
    review_candidate,
)
from app.services.dataset_service import build_dataset_version, collect_dataset_candidates
from app.services.style_service import select_style_examples
from app.services.training_export_service import build_finetuning_record
from app.storage import dataset_repository, import_repository
from app.storage.repository import RuntimeRepository

repository = RuntimeRepository()

MULTI_TURN_CSV = (
    "conversation_id,contact_id,role,content,occurred_at\n"
    "conv-1,5511900000001,cliente,Bom dia queria saber sobre o plano anual,2024-05-01 09:00:00\n"
    "conv-1,5511900000001,atendente,Bom dia! O plano anual tem doze meses de cobertura,2024-05-01 09:01:00\n"
    "conv-1,5511900000001,cliente,E qual a forma de pagamento,2024-05-01 09:02:00\n"
    "conv-1,5511900000001,atendente,Pode pagar em ate seis vezes sem juros no cartao,2024-05-01 09:03:00\n"
    "conv-2,5511900000002,cliente,Voces tem atendimento aos domingos,2024-05-02 10:00:00\n"
    "conv-2,5511900000002,atendente,Aos domingos atendemos apenas urgencias combinadas,2024-05-02 10:01:00\n"
)


def _import(company_id: int, csv_text: str = MULTI_TURN_CSV, batch_key: str = "lote") -> dict:
    return import_conversations(
        company_id=company_id,
        batch_key=batch_key,
        source_format="csv",
        raw_payload=csv_text,
        dry_run=False,
    )


# --- lineage e idempotência ---------------------------------------------------------


def test_reimport_preserves_conversation_identity_and_lineage(company_factory) -> None:
    company_id = company_factory("lineage")

    first = _import(company_id)
    conversations_before = import_repository.get_staged_conversations(
        company_id=company_id,
        batch_id=first["batch_id"],
    )
    candidates_before = import_repository.list_candidates(company_id=company_id)
    ids_before = {int(row["id"]) for row in conversations_before}
    candidate_ids_before = {int(row["id"]) for row in candidates_before}

    second = _import(company_id)
    conversations_after = import_repository.get_staged_conversations(
        company_id=company_id,
        batch_id=second["batch_id"],
    )
    candidates_after = import_repository.list_candidates(company_id=company_id)

    # Mesmo lote: nada duplicado, ids estáveis.
    assert second["batch_id"] == first["batch_id"]
    assert {int(row["id"]) for row in conversations_after} == ids_before
    assert {int(row["id"]) for row in candidates_after} == candidate_ids_before
    assert second["candidates_created"] == 0

    # Todo candidato aponta para uma conversa existente do lote atual.
    valid_conversation_ids = {int(row["id"]) for row in conversations_after}
    for candidate in candidates_after:
        assert candidate["conversation_id"] in valid_conversation_ids


def test_reimport_with_removed_conversation_does_not_leave_orphans(company_factory) -> None:
    company_id = company_factory("lineage-shrink")
    _import(company_id)

    shrunk_csv = "\n".join(MULTI_TURN_CSV.splitlines()[:5])
    second = _import(company_id, csv_text=shrunk_csv)

    conversations = import_repository.get_staged_conversations(
        company_id=company_id,
        batch_id=second["batch_id"],
    )
    valid_ids = {int(row["id"]) for row in conversations}

    for candidate in import_repository.list_candidates(company_id=company_id):
        conversation_id = candidate["conversation_id"]
        # Ou aponta para conversa viva, ou foi desligado — nunca para um id morto.
        assert conversation_id is None or conversation_id in valid_ids


def test_double_approval_creates_only_one_message_example(company_factory) -> None:
    company_id = company_factory("approve-once")
    _import(company_id)
    candidate = import_repository.list_candidates(company_id=company_id)[0]

    first = review_candidate(
        company_id=company_id,
        candidate_id=int(candidate["id"]),
        approved=True,
        reviewer="revisor",
        quality_score=9.0,
    )
    second = review_candidate(
        company_id=company_id,
        candidate_id=int(candidate["id"]),
        approved=True,
        reviewer="revisor",
        quality_score=9.0,
    )

    assert first["promoted_message_example_id"] == second["promoted_message_example_id"]
    assert first["already_promoted"] is False
    assert second["already_promoted"] is True

    promoted = [
        example
        for example in repository.get_message_examples(company_id=company_id)
        if example.get("source_candidate_id") == int(candidate["id"])
    ]
    assert len(promoted) == 1


def test_rejecting_a_promoted_candidate_requires_explicit_transition(company_factory) -> None:
    company_id = company_factory("approve-then-reject")
    _import(company_id)
    candidate = import_repository.list_candidates(company_id=company_id)[0]

    review_candidate(
        company_id=company_id,
        candidate_id=int(candidate["id"]),
        approved=True,
        reviewer="revisor",
    )

    with pytest.raises(CandidateStateError, match="already promoted"):
        review_candidate(
            company_id=company_id,
            candidate_id=int(candidate["id"]),
            approved=False,
            reviewer="revisor",
        )


def test_approving_a_rejected_candidate_requires_explicit_transition(company_factory) -> None:
    company_id = company_factory("reject-then-approve")
    _import(company_id)
    candidate = import_repository.list_candidates(company_id=company_id)[0]

    review_candidate(
        company_id=company_id,
        candidate_id=int(candidate["id"]),
        approved=False,
        reviewer="revisor",
    )

    with pytest.raises(CandidateStateError, match="rejected"):
        review_candidate(
            company_id=company_id,
            candidate_id=int(candidate["id"]),
            approved=True,
            reviewer="revisor",
        )


# --- grupo e contexto multi-turno ----------------------------------------------------


def _approve_all(company_id: int) -> None:
    for candidate in import_repository.list_candidates(company_id=company_id):
        review_candidate(
            company_id=company_id,
            candidate_id=int(candidate["id"]),
            approved=True,
            reviewer="revisor",
            quality_score=9.0,
        )


def test_promotion_carries_group_key_and_context(company_factory) -> None:
    company_id = company_factory("promote-lineage")
    _import(company_id)
    _approve_all(company_id)

    examples = repository.get_message_examples(company_id=company_id)
    second_turn = next(
        example
        for example in examples
        if "forma de pagamento" in example["customer_message"].lower()
    )

    # O agrupamento é a identidade anonimizada, não uma string por exemplo.
    assert str(second_turn["source_group_key"]).startswith("anon_")
    assert second_turn["source_candidate_id"] is not None
    assert second_turn["source_conversation_id"] is not None
    assert second_turn["source_batch_id"] is not None

    context = json.loads(second_turn["conversation_context_json"])
    assert context, "o contexto anterior precisa acompanhar a promoção"
    assert context[0]["role"] == "customer"
    assert "plano anual" in context[0]["content"].lower()


def test_context_never_includes_future_turns(company_factory) -> None:
    company_id = company_factory("context-past-only")
    _import(company_id)
    _approve_all(company_id)

    examples = repository.get_message_examples(company_id=company_id)
    first_turn = next(
        example
        for example in examples
        if "plano anual" in example["customer_message"].lower()
    )
    context = json.loads(first_turn["conversation_context_json"])

    # O primeiro turno não tem passado, então não tem contexto algum.
    assert context == []

    second_turn = next(
        example
        for example in examples
        if "forma de pagamento" in example["customer_message"].lower()
    )
    second_context = json.loads(second_turn["conversation_context_json"])
    joined = " ".join(turn["content"].lower() for turn in second_context)
    # A resposta do próprio par nunca pode estar no contexto.
    assert "seis vezes sem juros" not in joined


def test_context_turn_limit_is_configurable(monkeypatch, company_factory) -> None:
    company_id = company_factory("context-limit")
    monkeypatch.setattr(settings, "import_max_context_turns", 1)

    _import(company_id)
    candidates = import_repository.list_candidates(company_id=company_id)
    for candidate in candidates:
        context = json.loads(candidate["context_json"])
        assert len(context) <= 1


def test_dataset_groups_by_conversation_identity(company_factory) -> None:
    company_id = company_factory("dataset-group")
    _import(company_id)
    _approve_all(company_id)

    items = collect_dataset_candidates(company_id=company_id)
    groups = {item.group_key for item in items}

    # Duas conversas -> dois grupos, mesmo com quatro pares.
    assert len(items) == 3
    assert len(groups) == 2


def test_all_pairs_of_a_conversation_stay_in_the_same_split(company_factory) -> None:
    company_id = company_factory("dataset-split-group")
    _import(company_id)
    _approve_all(company_id)

    version = build_dataset_version(company_id=company_id, seed=17)
    items = dataset_repository.get_items(
        company_id=company_id,
        dataset_version_id=int(version["id"]),
    )

    splits_by_group: dict[str, set[str]] = {}
    for item in items:
        splits_by_group.setdefault(str(item["group_key"]), set()).add(str(item["split"]))

    assert splits_by_group
    assert all(len(splits) == 1 for splits in splits_by_group.values())


def test_context_reaches_the_training_jsonl(company_factory) -> None:
    company_id = company_factory("jsonl-context")
    _import(company_id)
    _approve_all(company_id)

    version = build_dataset_version(company_id=company_id, seed=3)
    items = dataset_repository.get_items(
        company_id=company_id,
        dataset_version_id=int(version["id"]),
    )
    with_context = [item for item in items if item["context"]]
    assert with_context, "algum item precisa carregar contexto"

    record = build_finetuning_record(with_context[0])
    user_block = record["messages"][1]["content"]

    assert "Turnos anteriores da conversa:" in user_block
    assert "customer:" in user_block


def test_context_is_pii_scrubbed(company_factory) -> None:
    company_id = company_factory("context-pii")
    csv_with_pii = (
        "conversation_id,contact_id,role,content,occurred_at\n"
        "conv-9,5511900000009,cliente,Meu email e cliente.sigilo@empresa.com para contato,2024-05-01 09:00:00\n"
        "conv-9,5511900000009,atendente,Anotei seu contato para retorno,2024-05-01 09:01:00\n"
        "conv-9,5511900000009,cliente,Qual o proximo passo do processo,2024-05-01 09:02:00\n"
        "conv-9,5511900000009,atendente,O proximo passo e a analise documental completa,2024-05-01 09:03:00\n"
    )
    _import(company_id, csv_text=csv_with_pii, batch_key="lote-pii")
    _approve_all(company_id)

    items = collect_dataset_candidates(company_id=company_id)
    serialized = json.dumps([item.context for item in items], ensure_ascii=False)

    assert "cliente.sigilo@empresa.com" not in serialized
    assert "[EMAIL]" in serialized


def test_rebuild_reproduces_the_same_split_with_the_same_seed(company_factory) -> None:
    company_id = company_factory("dataset-reproducible")
    _import(company_id)
    _approve_all(company_id)

    first = build_dataset_version(company_id=company_id, seed=123)
    second = build_dataset_version(company_id=company_id, seed=123)

    assert first["content_hash"] == second["content_hash"]


# --- aprovação de exemplos de estilo -------------------------------------------------


def test_pending_examples_never_reach_the_prompt(company_factory) -> None:
    company_id = company_factory("style-pending")

    repository.create_message_example(
        company_id=company_id,
        profile_name="default",
        scenario_type="lead",
        customer_message="Oi",
        approved_reply="Resposta ainda pendente de revisão",
        approval_status="pending",
        active=True,
    )

    selection = select_style_examples(company_id=company_id, scenario_type="lead")

    assert selection.examples == []


@pytest.mark.parametrize("status", ["pending", "rejected", "archived"])
def test_non_approved_statuses_are_excluded_from_few_shot(company_factory, status) -> None:
    company_id = company_factory(f"style-{status}")

    repository.create_message_example(
        company_id=company_id,
        profile_name="default",
        scenario_type="lead",
        customer_message="Oi",
        approved_reply=f"Exemplo {status}",
        approval_status=status,
        active=True,
    )

    assert select_style_examples(company_id=company_id, scenario_type="lead").examples == []


def test_explicit_approval_makes_the_example_usable(company_factory) -> None:
    company_id = company_factory("style-approve")

    example_id = repository.create_message_example(
        company_id=company_id,
        profile_name="default",
        scenario_type="lead",
        customer_message="Oi",
        approved_reply="Resposta aprovada depois",
        approval_status="pending",
        active=True,
    )
    assert select_style_examples(company_id=company_id, scenario_type="lead").examples == []

    assert repository.set_message_example_approval(
        company_id=company_id,
        message_example_id=example_id,
        approval_status="approved",
        approved_by="revisor",
    )

    selection = select_style_examples(company_id=company_id, scenario_type="lead")
    assert selection.examples[0]["approved_reply"] == "Resposta aprovada depois"
    assert selection.examples[0]["approved_by"] == "revisor"


def test_imported_examples_do_not_influence_the_reply_before_approval(company_factory) -> None:
    """Importação legada cria exemplos pendentes: eles não entram no prompt."""
    from app.services.training_import_service import import_training_records

    company_id = company_factory("style-import-pending")
    import_training_records(
        company_id=company_id,
        fmt="json",
        raw_text=None,
        records=[
            {
                "customer_message": "Quanto custa a consultoria",
                "approved_reply": "A consultoria parte de mil reais por projeto",
                "scenario_type": "lead",
                "quality_score": 9,
            }
        ],
        dry_run=False,
    )

    selection = select_style_examples(company_id=company_id, scenario_type="lead")
    assert selection.examples == []
