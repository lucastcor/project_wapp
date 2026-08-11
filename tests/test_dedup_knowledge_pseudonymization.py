"""Deduplicação por SimHash, versionamento de knowledge e pseudonimização HMAC."""

import pytest

from app.core.config import settings
from app.services import pii_service
from app.services.dataset_service import (
    DatasetItem,
    deduplicate,
    hamming_distance,
    jaccard_similarity,
    near_duplicate_key,
    simhash,
)
from app.services.knowledge_document_service import approve_document, ingest_document
from app.services.knowledge_retrieval_service import retrieve_knowledge
from app.services.pii_service import PseudonymizationKeyError, pseudonymize_identifier
from app.storage import knowledge_repository


def _item(customer_message: str, approved_reply: str, source_id: int) -> DatasetItem:
    return DatasetItem(
        split="",
        group_key=f"group-{source_id}",
        source_type="message_example",
        source_id=source_id,
        scenario_type="lead",
        customer_profile="new_customer",
        profile_name="default",
        context=[],
        customer_message=customer_message,
        approved_reply=approved_reply,
        style_notes=None,
        quality_score=9.0,
        content_hash=f"hash-{source_id}",
        near_duplicate_key=near_duplicate_key(customer_message, approved_reply),
    )


# --- SimHash ------------------------------------------------------------------------


def test_simhash_is_deterministic() -> None:
    first = simhash("Quanto custa o plano?", "Custa 100 reais por mês.")
    second = simhash("Quanto custa o plano?", "Custa 100 reais por mês.")

    assert first == second
    assert first != 0


def test_cosmetic_differences_are_near_duplicates() -> None:
    left = ("Bom dia, quanto custa o plano anual?", "O plano anual custa mil reais por ano.")
    right = ("bom dia quanto custa o plano anual", "o plano anual custa mil reais por ano")

    distance = hamming_distance(simhash(*left), simhash(*right))

    assert distance <= settings.near_duplicate_max_distance
    assert jaccard_similarity(left, right) >= 0.6


def test_semantically_distinct_pairs_are_not_near_duplicates() -> None:
    left = ("Bom dia, vocês entregam em Salvador?", "Entregamos em Salvador com frete calculado.")
    right = ("Bom dia, vocês parcelam no cartão?", "Parcelamos em ate seis vezes sem juros.")

    items = [_item(*left, 1), _item(*right, 2)]
    unique, stats = deduplicate(items)

    # Saudação igual não pode colapsar dois assuntos diferentes.
    assert len(unique) == 2
    assert stats["near_duplicates_removed"] == 0


def test_deduplication_reports_count_and_reason() -> None:
    base = ("Bom dia, quanto custa o plano anual?", "O plano anual custa mil reais por ano.")
    items = [
        _item(*base, 1),
        _item("bom dia quanto custa o plano anual", "o plano anual custa mil reais por ano", 2),
        _item(*base, 3),
    ]
    items[2] = DatasetItem(**{**items[2].__dict__, "content_hash": items[0].content_hash})

    unique, stats = deduplicate(items)

    assert len(unique) == 1
    assert stats["exact_duplicates_removed"] == 1
    assert stats["near_duplicates_removed"] == 1
    reasons = {entry["reason"] for entry in stats["removal_reasons"]}
    assert reasons == {"exact_duplicate", "near_duplicate"}
    near_entry = next(
        entry for entry in stats["removal_reasons"] if entry["reason"] == "near_duplicate"
    )
    assert near_entry["duplicate_of_source_id"] == 1
    assert near_entry["hamming_distance"] <= settings.near_duplicate_max_distance


def test_threshold_is_configurable(monkeypatch) -> None:
    left = ("Bom dia, quanto custa o plano anual?", "O plano anual custa mil reais por ano.")
    right = ("bom dia quanto custa o plano anual", "o plano anual custa mil reais por ano")
    items = [_item(*left, 1), _item(*right, 2)]

    monkeypatch.setattr(settings, "near_duplicate_max_distance", 0)
    strict_unique, _ = deduplicate(items)

    monkeypatch.setattr(settings, "near_duplicate_max_distance", 12)
    loose_unique, _ = deduplicate(items)

    assert len(loose_unique) <= len(strict_unique)


# --- knowledge: publicação explícita -------------------------------------------------


def test_identical_reupload_is_a_true_noop(company_factory) -> None:
    company_id = company_factory("kb-noop")

    first = ingest_document(
        company_id=company_id,
        title="Política",
        content="Cancelamento com 24 horas de antecedência não gera custo.",
        document_type="business_rule",
        external_ref="politica",
        approve=True,
    )
    second = ingest_document(
        company_id=company_id,
        title="Política",
        content="Cancelamento com 24 horas de antecedência não gera custo.",
        document_type="business_rule",
        external_ref="politica",
        approve=True,
    )

    assert second["created_version"] is False
    assert second["version"] == first["version"]
    # E, principalmente, o status não foi rebaixado para draft.
    document = knowledge_repository.get_document(
        company_id=company_id,
        document_id=first["document_id"],
    )
    assert document["status"] == "approved"


def test_new_draft_version_does_not_take_the_published_one_offline(company_factory) -> None:
    company_id = company_factory("kb-draft")

    published = ingest_document(
        company_id=company_id,
        title="Horário de atendimento",
        content="Atendemos de segunda a sexta das 9h às 18h.",
        document_type="schedule_rule",
        external_ref="horario",
        approve=True,
    )
    assert retrieve_knowledge(
        company_id=company_id,
        query="qual o horário de atendimento?",
    ).has_relevant_source

    # Nova versão em edição, ainda não aprovada.
    draft = ingest_document(
        company_id=company_id,
        title="Horário de atendimento",
        content="Atendemos de segunda a sabado das 8h às 20h.",
        document_type="schedule_rule",
        external_ref="horario",
        approve=False,
    )
    assert draft["created_version"] is True
    assert draft["version"] == published["version"] + 1

    result = retrieve_knowledge(company_id=company_id, query="qual o horário de atendimento?")
    content = " ".join(chunk.content for chunk in result.chunks)

    # A produção continua servindo a versão publicada.
    assert result.has_relevant_source
    assert "9h às 18h" in content
    assert "8h às 20h" not in content

    # Só a aprovação explícita troca o que está no ar.
    approve_document(
        company_id=company_id,
        document_id=draft["document_id"],
        approved_by="revisor",
    )
    after = retrieve_knowledge(company_id=company_id, query="qual o horário de atendimento?")
    after_content = " ".join(chunk.content for chunk in after.chunks)
    assert "8h às 20h" in after_content
    assert "9h às 18h" not in after_content


def test_published_version_can_be_rolled_back(company_factory) -> None:
    company_id = company_factory("kb-rollback")

    first = ingest_document(
        company_id=company_id,
        title="Tabela",
        content="O pacote básico custa duzentos reais.",
        document_type="price",
        external_ref="tabela",
        approve=True,
    )
    second = ingest_document(
        company_id=company_id,
        title="Tabela",
        content="O pacote básico custa trezentos reais.",
        document_type="price",
        external_ref="tabela",
        approve=True,
    )
    assert "trezentos" in " ".join(
        chunk.content
        for chunk in retrieve_knowledge(company_id=company_id, query="pacote básico").chunks
    )

    assert knowledge_repository.rollback_published_version(
        company_id=company_id,
        document_id=first["document_id"],
        version_id=first["version_id"],
    )

    restored = " ".join(
        chunk.content
        for chunk in retrieve_knowledge(company_id=company_id, query="pacote básico").chunks
    )
    assert "duzentos" in restored
    assert "trezentos" not in restored
    assert second["version"] > first["version"]


def test_metadata_change_does_not_change_status(company_factory) -> None:
    """Mudar metadado não rebaixa o documento nem republica nada.

    A asserção anterior verificava que o título do documento mudava com o
    rascunho. Isso era o próprio defeito: o documento espelha a versão
    **publicada**, e um rascunho não publicado não pode mexer nela. Aqui o
    status continua aprovado e o metadado publicado continua o antigo; a
    troca só acontece na aprovação (coberta em `test_phase_zero_blockers`).
    """
    company_id = company_factory("kb-metadata")

    stored = ingest_document(
        company_id=company_id,
        title="Documento",
        content="Conteúdo estável do documento.",
        document_type="general_faq",
        external_ref="doc",
        approve=True,
    )
    draft = ingest_document(
        company_id=company_id,
        title="Documento renomeado",
        content="Conteúdo estável do documento.",
        document_type="general_faq",
        external_ref="doc",
        priority=5,
        approve=False,
    )

    document = knowledge_repository.get_document(
        company_id=company_id,
        document_id=stored["document_id"],
    )
    assert document["status"] == "approved"
    assert document["title"] == "Documento"
    assert int(document["priority"]) == 0

    draft_version = knowledge_repository.get_version(
        company_id=company_id,
        version_id=int(draft["version_id"]),
    )
    assert draft_version["title"] == "Documento renomeado"
    assert int(draft_version["priority"]) == 5


# --- pseudonimização ------------------------------------------------------------------


def test_pseudonym_uses_hmac_with_the_configured_secret(monkeypatch) -> None:
    monkeypatch.setattr(settings, "pii_pseudonymization_key", "chave-a")
    with_key_a = pseudonymize_identifier("5511999999999", company_id=1)

    monkeypatch.setattr(settings, "pii_pseudonymization_key", "chave-b")
    with_key_b = pseudonymize_identifier("5511999999999", company_id=1)

    # Sem o segredo, o mesmo telefone produziria o mesmo hash: é isso que
    # tornava o pseudônimo reversível por enumeração.
    assert with_key_a != with_key_b
    assert "5511999999999" not in with_key_a


def test_pseudonym_is_stable_within_a_tenant_and_differs_across_tenants(monkeypatch) -> None:
    monkeypatch.setattr(settings, "pii_pseudonymization_key", "chave-estavel")

    first = pseudonymize_identifier("5511999999999", company_id=7)
    again = pseudonymize_identifier("5511999999999", company_id=7)
    other_tenant = pseudonymize_identifier("5511999999999", company_id=8)

    assert first == again
    assert first != other_tenant


def test_key_version_is_part_of_the_pseudonym(monkeypatch) -> None:
    monkeypatch.setattr(settings, "pii_pseudonymization_key", "chave-versionada")
    monkeypatch.setattr(settings, "pii_pseudonymization_key_version", "v1")
    v1 = pseudonymize_identifier("5511999999999", company_id=1)

    monkeypatch.setattr(settings, "pii_pseudonymization_key_version", "v2")
    v2 = pseudonymize_identifier("5511999999999", company_id=1)

    assert v1.startswith("anon_v1_")
    assert v2.startswith("anon_v2_")
    assert v1 != v2


def test_missing_key_is_fatal_in_production(monkeypatch) -> None:
    monkeypatch.setattr(settings, "pii_pseudonymization_key", None)
    monkeypatch.setattr(pii_service, "is_production_like", lambda: True)

    with pytest.raises(PseudonymizationKeyError, match="required in production"):
        pseudonymize_identifier("5511999999999", company_id=1)


def test_placeholder_key_is_rejected_in_production(monkeypatch) -> None:
    monkeypatch.setattr(settings, "pii_pseudonymization_key", "change-me-key")
    monkeypatch.setattr(pii_service, "is_production_like", lambda: True)

    with pytest.raises(PseudonymizationKeyError):
        pseudonymize_identifier("5511999999999", company_id=1)


def test_development_fallback_is_flagged(monkeypatch, caplog) -> None:
    import logging

    monkeypatch.setattr(settings, "pii_pseudonymization_key", None)
    monkeypatch.setattr(pii_service, "is_production_like", lambda: False)

    with caplog.at_level(logging.WARNING):
        pseudonym = pseudonymize_identifier("5511999999999", company_id=1)

    assert "insecure" in pseudonym
    assert "pii.pseudonymization.insecure_fallback" in caplog.text


def test_key_is_never_logged(monkeypatch, caplog) -> None:
    import logging

    monkeypatch.setattr(settings, "pii_pseudonymization_key", "segredo-super-sensivel")

    with caplog.at_level(logging.DEBUG):
        pseudonymize_identifier("5511999999999", company_id=1)

    assert "segredo-super-sensivel" not in caplog.text


def test_pseudonymize_requires_a_scope() -> None:
    with pytest.raises(ValueError, match="requires company_id or salt"):
        pseudonymize_identifier("5511999999999")
