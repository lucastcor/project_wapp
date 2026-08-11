"""Importação governada de conversas: parsing, staging, revisão e lineage."""

import pytest

from app.core.config import settings
from app.services.conversation_import_service import (
    ConversationImportError,
    import_conversations,
    review_candidate,
)
from app.services.conversation_parsers import (
    ConversationParseError,
    decode_payload,
    parse_csv,
    parse_json,
    parse_whatsapp_txt,
)
from app.storage import import_repository
from app.storage.repository import RuntimeRepository

repository = RuntimeRepository()

WHATSAPP_EXPORT = """12/03/2024 09:41 - As mensagens e as chamadas são criptografadas de ponta a ponta.
12/03/2024 09:42 - Maria: Bom dia, vocês atendem no sábado?
12/03/2024 09:43 - Atendimento Salão: Bom dia, Maria! Atendemos sim, das 9h às 13h.
12/03/2024 09:44 - Maria: Perfeito, quero marcar
12/03/2024 09:45 - Atendimento Salão: Fechado. Sábado às 10h está livre.
Posso confirmar para você?
12/03/2024 09:46 - Maria: Pode sim, meu email é maria.teste@exemplo.com
"""

CSV_EXPORT = (
    "conversation_id,contact_id,role,content,occurred_at\n"
    "conv-1,5511999990000,cliente,Bom dia quero um orçamento,2024-03-12 09:00:00\n"
    "conv-1,5511999990000,atendente,Bom dia! Posso te ajudar com isso,2024-03-12 09:01:00\n"
    "conv-1,5511999990000,cliente,Qual o prazo?,2024-03-12 09:02:00\n"
    "conv-1,5511999990000,atendente,O prazo é de cinco dias úteis,2024-03-12 09:03:00\n"
)


# --- parsers -------------------------------------------------------------------


def test_whatsapp_parser_preserves_order_and_roles() -> None:
    result = parse_whatsapp_txt(
        WHATSAPP_EXPORT,
        conversation_ref="salao",
        agent_authors=["Atendimento Salão"],
    )

    assert [message.role for message in result.messages] == [
        "customer",
        "agent",
        "customer",
        "agent",
        "customer",
    ]
    assert result.messages[0].content == "Bom dia, vocês atendem no sábado?"
    # Continuação multilinha é anexada à mensagem anterior.
    assert "Posso confirmar para você?" in result.messages[3].content
    # A linha de criptografia é ignorada como ruído, não como erro fatal.
    assert any(issue.reason == "ignored_system_notice" for issue in result.issues)


def test_whatsapp_parser_infers_agent_when_authors_not_declared() -> None:
    result = parse_whatsapp_txt(WHATSAPP_EXPORT, conversation_ref="salao")

    roles = [message.role for message in result.messages]
    assert roles.count("agent") == 2
    assert roles[0] == "customer"


def test_parser_handles_invalid_lines_without_dropping_the_batch() -> None:
    text = WHATSAPP_EXPORT + "linha totalmente invalida sem timestamp no inicio do arquivo\n"
    result = parse_whatsapp_txt(
        "linha invalida no topo\n" + text,
        conversation_ref="salao",
    )

    assert result.messages
    assert any(issue.reason == "unparsable_line" for issue in result.issues)


def test_decode_payload_handles_encoding_and_size_limits() -> None:
    latin_bytes = "Ação e coração".encode("cp1252")
    assert "Ação" in decode_payload(latin_bytes, max_bytes=1024)

    with pytest.raises(ConversationParseError, match="exceeds the configured limit"):
        decode_payload(b"x" * 100, max_bytes=10)


def test_csv_parser_requires_a_header() -> None:
    with pytest.raises(ConversationParseError, match="header row"):
        parse_csv("")


def test_json_parser_supports_nested_conversations() -> None:
    result = parse_json(
        {
            "conversations": [
                {
                    "conversation_id": "c1",
                    "contact_id": "5511999990001",
                    "messages": [
                        {"role": "cliente", "content": "Oi"},
                        {"role": "atendente", "content": "Olá, tudo bem?"},
                    ],
                }
            ]
        }
    )

    assert len(result.messages) == 2
    assert result.messages[0].conversation_ref == "c1"
    assert result.messages[1].role == "agent"


# --- importação ------------------------------------------------------------------


def test_dry_run_executes_the_pipeline_without_writing(company_factory) -> None:
    company_id = company_factory("import-dryrun")

    result = import_conversations(
        company_id=company_id,
        batch_key="lote-dry",
        source_format="whatsapp_txt",
        raw_payload=WHATSAPP_EXPORT,
        conversation_ref="salao",
        agent_authors=["Atendimento Salão"],
        dry_run=True,
    )

    assert result["dry_run"] is True
    assert result["batch_id"] is None
    assert result["stats"]["conversations"] == 1
    assert result["stats"]["candidates"] >= 1
    assert import_repository.list_batches(company_id=company_id) == []
    assert import_repository.list_candidates(company_id=company_id) == []


def test_import_is_idempotent_for_the_same_batch_key(company_factory) -> None:
    company_id = company_factory("import-idempotent")

    first = import_conversations(
        company_id=company_id,
        batch_key="lote-1",
        source_format="csv",
        raw_payload=CSV_EXPORT,
        dry_run=False,
    )
    second = import_conversations(
        company_id=company_id,
        batch_key="lote-1",
        source_format="csv",
        raw_payload=CSV_EXPORT,
        dry_run=False,
    )

    assert first["batch_id"] == second["batch_id"]
    assert first["candidates_created"] == 2
    assert second["candidates_created"] == 0
    assert second["candidates_existing"] == 2
    assert len(import_repository.list_batches(company_id=company_id)) == 1
    assert len(import_repository.list_candidates(company_id=company_id)) == 2


def test_multi_turn_conversation_keeps_order_and_context(company_factory) -> None:
    company_id = company_factory("import-multiturn")

    result = import_conversations(
        company_id=company_id,
        batch_key="lote-multiturn",
        source_format="csv",
        raw_payload=CSV_EXPORT,
        dry_run=False,
    )

    conversations = import_repository.get_staged_conversations(
        company_id=company_id,
        batch_id=result["batch_id"],
    )
    assert len(conversations) == 1

    messages = import_repository.get_staged_messages(
        company_id=company_id,
        conversation_id=int(conversations[0]["id"]),
    )
    assert [message["sequence"] for message in messages] == [1, 2, 3, 4]
    assert [message["role"] for message in messages] == [
        "customer",
        "agent",
        "customer",
        "agent",
    ]

    candidates = import_repository.list_candidates(company_id=company_id)
    second_turn = next(
        candidate for candidate in candidates if "prazo" in candidate["customer_message"].lower()
    )
    # O candidato multi-turno guarda os turnos anteriores como contexto.
    assert second_turn["context_json"] != "[]"


def test_staging_only_stores_anonymized_content(company_factory) -> None:
    company_id = company_factory("import-pii")

    result = import_conversations(
        company_id=company_id,
        batch_key="lote-pii",
        source_format="whatsapp_txt",
        raw_payload=WHATSAPP_EXPORT,
        conversation_ref="salao",
        agent_authors=["Atendimento Salão"],
        dry_run=False,
    )

    conversations = import_repository.get_staged_conversations(
        company_id=company_id,
        batch_id=result["batch_id"],
    )
    messages = import_repository.get_staged_messages(
        company_id=company_id,
        conversation_id=int(conversations[0]["id"]),
    )
    all_content = " ".join(str(message["anonymized_content"]) for message in messages)

    assert "maria.teste@exemplo.com" not in all_content
    assert "[EMAIL]" in all_content
    assert result["stats"]["pii_replacements"].get("email") == 1
    # Nenhum identificador de contato em claro no staging.
    assert conversations[0]["contact_ref_hash"].startswith("anon_")


def test_raw_content_is_never_auto_promoted_to_training(company_factory) -> None:
    company_id = company_factory("import-no-autopromote")

    import_conversations(
        company_id=company_id,
        batch_key="lote-review",
        source_format="csv",
        raw_payload=CSV_EXPORT,
        dry_run=False,
    )

    candidates = import_repository.list_candidates(company_id=company_id)
    assert candidates
    assert all(candidate["review_status"] == "pending" for candidate in candidates)
    assert all(candidate["promoted_message_example_id"] is None for candidate in candidates)
    # Nada foi para o dataset de treino.
    assert repository.get_exportable_message_examples(company_id=company_id) == []


def test_approving_a_candidate_promotes_it_with_lineage(company_factory) -> None:
    company_id = company_factory("import-approve")

    import_conversations(
        company_id=company_id,
        batch_key="lote-approve",
        source_format="csv",
        raw_payload=CSV_EXPORT,
        dry_run=False,
    )
    candidate = import_repository.list_candidates(company_id=company_id)[0]

    result = review_candidate(
        company_id=company_id,
        candidate_id=int(candidate["id"]),
        approved=True,
        reviewer="revisor",
        scenario_type="reschedule",
        customer_profile="retorno",
        quality_score=9.0,
    )

    assert result["review_status"] == "approved"
    assert result["promoted_message_example_id"] is not None

    examples = repository.get_message_examples(company_id=company_id)
    promoted = next(
        example
        for example in examples
        if int(example["id"]) == int(result["promoted_message_example_id"])
    )
    # Taxonomia normalizada e lineage até o candidato de origem.
    assert promoted["scenario_type"] == "scheduling"
    assert promoted["customer_profile"] == "returning_customer"
    assert promoted["source_reference"] == f"training_candidate:{candidate['id']}"
    assert promoted["source_type"] == "conversation_import"
    assert int(promoted["pii_scrubbed"]) == 1
    assert promoted["approval_status"] == "approved"


def test_rejecting_a_candidate_does_not_promote_it(company_factory) -> None:
    company_id = company_factory("import-reject")

    import_conversations(
        company_id=company_id,
        batch_key="lote-reject",
        source_format="csv",
        raw_payload=CSV_EXPORT,
        dry_run=False,
    )
    candidate = import_repository.list_candidates(company_id=company_id)[0]

    result = review_candidate(
        company_id=company_id,
        candidate_id=int(candidate["id"]),
        approved=False,
        reviewer="revisor",
        review_notes="conversa fora do padrão",
    )

    assert result["review_status"] == "rejected"
    assert result["promoted_message_example_id"] is None
    assert repository.get_exportable_message_examples(company_id=company_id) == []


def test_import_rejects_payload_above_the_message_limit(monkeypatch, company_factory) -> None:
    company_id = company_factory("import-limit")
    monkeypatch.setattr(settings, "import_max_messages_per_batch", 2)

    with pytest.raises(ConversationImportError, match="above the configured limit"):
        import_conversations(
            company_id=company_id,
            batch_key="lote-grande",
            source_format="csv",
            raw_payload=CSV_EXPORT,
            dry_run=True,
        )


def test_import_rejects_payload_above_the_byte_limit(monkeypatch, company_factory) -> None:
    company_id = company_factory("import-bytes")
    monkeypatch.setattr(settings, "import_max_bytes", 32)

    with pytest.raises(ConversationImportError, match="exceeds the configured limit"):
        import_conversations(
            company_id=company_id,
            batch_key="lote-bytes",
            source_format="csv",
            raw_payload=CSV_EXPORT,
            dry_run=True,
        )


def test_sessions_are_split_by_inactivity_window(monkeypatch, company_factory) -> None:
    company_id = company_factory("import-sessions")
    monkeypatch.setattr(settings, "import_session_gap_minutes", 60)

    csv_with_gap = (
        "conversation_id,contact_id,role,content,occurred_at\n"
        "conv-9,5511999990002,cliente,Primeira sessão,2024-03-12 09:00:00\n"
        "conv-9,5511999990002,atendente,Resposta da primeira,2024-03-12 09:01:00\n"
        "conv-9,5511999990002,cliente,Segunda sessão depois de dias,2024-03-20 15:00:00\n"
        "conv-9,5511999990002,atendente,Resposta da segunda,2024-03-20 15:01:00\n"
    )

    result = import_conversations(
        company_id=company_id,
        batch_key="lote-sessoes",
        source_format="csv",
        raw_payload=csv_with_gap,
        dry_run=False,
    )

    assert result["stats"]["conversations"] == 2
    conversations = import_repository.get_staged_conversations(
        company_id=company_id,
        batch_id=result["batch_id"],
    )
    assert {int(row["session_index"]) for row in conversations} == {0, 1}
    # As duas sessões pertencem ao mesmo contato anonimizado.
    assert len({row["contact_ref_hash"] for row in conversations}) == 1
