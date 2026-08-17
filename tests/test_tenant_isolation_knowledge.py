"""Testes negativos de isolamento entre empresas.

Cada teste cobre um vazamento concreto que a arquitetura precisa impedir.
"""

import pytest

from app.core.tenancy import TenantScopeError
from app.services import company_config_service
from app.services.company_config_service import CompanyConfigError, get_company_config
from app.services.knowledge_document_service import ingest_document
from app.services.knowledge_retrieval_service import retrieve_knowledge
from app.services.style_service import select_style_examples
from app.storage.repository import RuntimeRepository

repository = RuntimeRepository()


def test_contact_scoped_knowledge_does_not_cross_companies(company_factory) -> None:
    company_a = company_factory("kb-a")
    company_b = company_factory("kb-b")
    shared_contact_id = "5511900000001"

    repository.save_contact(shared_contact_id, "Cliente", company_id=company_a)
    repository.save_contact(shared_contact_id, "Cliente", company_id=company_b)

    repository.create_knowledge_entry(
        title="Regra privada A",
        content="Desconto exclusivo da empresa A",
        knowledge_type="discount_policy",
        contact_id=shared_contact_id,
        company_id=company_a,
    )

    entries_for_b = repository.get_relevant_knowledge_entries(
        contact_id=shared_contact_id,
        company_id=company_b,
    )

    assert entries_for_b == []

    entries_for_a = repository.get_relevant_knowledge_entries(
        contact_id=shared_contact_id,
        company_id=company_a,
    )
    assert [entry["title"] for entry in entries_for_a] == ["Regra privada A"]


def test_global_knowledge_stays_inside_its_company(company_factory) -> None:
    company_a = company_factory("kb-global-a")
    company_b = company_factory("kb-global-b")

    repository.create_knowledge_entry(
        title="Política global A",
        content="Política válida apenas para A",
        knowledge_type="business_rule",
        applies_globally=True,
        company_id=company_a,
    )

    assert repository.get_relevant_knowledge_entries(company_id=company_b) == []
    assert len(repository.get_relevant_knowledge_entries(company_id=company_a)) == 1


def test_knowledge_retrieval_requires_company_id() -> None:
    with pytest.raises(TenantScopeError):
        repository.get_relevant_knowledge_entries(contact_id="5511900000002", company_id=None)

    with pytest.raises(TenantScopeError):
        retrieve_knowledge(company_id=None, query="qualquer coisa")


def test_rag_search_never_returns_other_tenant_chunks(company_factory) -> None:
    company_a = company_factory("rag-a")
    company_b = company_factory("rag-b")

    ingest_document(
        company_id=company_a,
        title="Tabela de preços",
        content="O corte masculino custa 60 reais e a barba custa 40 reais.",
        document_type="price",
        external_ref="precos",
        approve=True,
    )
    ingest_document(
        company_id=company_b,
        title="Tabela de preços",
        content="A consultoria tributária custa 900 reais por hora.",
        document_type="price",
        external_ref="precos",
        approve=True,
    )

    result_a = retrieve_knowledge(company_id=company_a, query="quanto custa o corte")
    assert result_a.has_relevant_source
    assert all(chunk.company_id if hasattr(chunk, "company_id") else True for chunk in result_a.chunks)
    assert "consultoria tributária" not in " ".join(chunk.content for chunk in result_a.chunks)

    result_b = retrieve_knowledge(company_id=company_b, query="quanto custa o corte")
    assert "corte masculino" not in " ".join(chunk.content for chunk in result_b.chunks)


def test_style_examples_are_scoped_by_company(company_factory) -> None:
    company_a = company_factory("style-a")
    company_b = company_factory("style-b")

    repository.create_message_example(
        company_id=company_a,
        profile_name="default",
        scenario_type="lead",
        customer_profile="new_customer",
        customer_message="Oi",
        approved_reply="Resposta da empresa A",
    )

    selection_b = select_style_examples(company_id=company_b, scenario_type="lead")
    assert selection_b.examples == []

    selection_a = select_style_examples(company_id=company_a, scenario_type="lead")
    assert selection_a.examples[0]["approved_reply"] == "Resposta da empresa A"


def test_missing_company_context_in_production_does_not_use_fictitious_identity(
    monkeypatch,
    company_factory,
) -> None:
    company_id = company_factory("no-context")
    company_config_service.invalidate_company_config()
    monkeypatch.setattr(company_config_service, "is_production_like", lambda: True)

    with pytest.raises(CompanyConfigError, match="no published company context"):
        get_company_config(company_id, use_cache=False)


def test_missing_company_context_outside_production_is_marked_as_template(
    company_factory,
) -> None:
    company_id = company_factory("template-context")
    company_config_service.invalidate_company_config()

    config = get_company_config(company_id, use_cache=False)

    assert config.source == "global_template"
    assert config.version is None
