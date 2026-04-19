from uuid import uuid4

from app.storage.repository import RuntimeRepository


def test_repository_company_scoped_queries_do_not_leak_between_tenants() -> None:
    repository = RuntimeRepository()
    suffix = uuid4().hex

    company_a = repository.create_company(
        slug=f"tenant-a-{suffix}",
        name=f"Tenant A {suffix}",
    )
    company_b = repository.create_company(
        slug=f"tenant-b-{suffix}",
        name=f"Tenant B {suffix}",
    )

    contact_a = f"contact-{suffix}-a"
    contact_b = f"contact-{suffix}-b"

    repository.save_contact(contact_a, "Alice", company_id=company_a)
    repository.save_contact(contact_b, "Bruno", company_id=company_b)

    repository.save_message(
        contact_id=contact_a,
        message="Mensagem A",
        classification="faq",
        reply="Resposta A",
        company_id=company_a,
    )
    repository.save_message(
        contact_id=contact_b,
        message="Mensagem B",
        classification="faq",
        reply="Resposta B",
        company_id=company_b,
    )

    pending_question_a = repository.create_pending_question(
        contact_id=contact_a,
        customer_message="Pergunta A",
        question_type="pricing",
        question_for_owner="Resolver A",
        company_id=company_a,
    )
    pending_question_b = repository.create_pending_question(
        contact_id=contact_b,
        customer_message="Pergunta B",
        question_type="pricing",
        question_for_owner="Resolver B",
        company_id=company_b,
    )

    knowledge_entry_a = repository.create_knowledge_entry(
        title="KB A",
        content="Conteúdo A",
        knowledge_type="policy",
        contact_id=contact_a,
        company_id=company_a,
    )
    knowledge_entry_b = repository.create_knowledge_entry(
        title="KB B",
        content="Conteúdo B",
        knowledge_type="policy",
        contact_id=contact_b,
        company_id=company_b,
    )

    message_example_a = repository.create_message_example(
        profile_name="premium",
        scenario_type="lead",
        customer_message="Oi A",
        approved_reply="Olá A",
        used_for_training=True,
        company_id=company_a,
    )
    message_example_b = repository.create_message_example(
        profile_name="standard",
        scenario_type="lead",
        customer_message="Oi B",
        approved_reply="Olá B",
        used_for_training=True,
        company_id=company_b,
    )

    outbound_message_a = repository.create_outbound_message(
        contact_id=contact_a,
        message="Outbound A",
        source="handoff",
        company_id=company_a,
    )
    outbound_message_b = repository.create_outbound_message(
        contact_id=contact_b,
        message="Outbound B",
        source="handoff",
        company_id=company_b,
    )

    integration_a = repository.create_calendar_integration(
        provider="mock",
        company_id=company_a,
        config={"label": "Agenda A"},
    )
    integration_b = repository.create_calendar_integration(
        provider="mock",
        company_id=company_b,
        config={"label": "Agenda B"},
    )

    booking_a = repository.create_appointment_booking(
        provider="mock",
        title="Consulta A",
        starts_at="2030-05-01T13:00:00+00:00",
        ends_at="2030-05-01T14:00:00+00:00",
        timezone="America/Sao_Paulo",
        company_id=company_a,
        contact_id=contact_a,
        integration_id=integration_a,
    )
    booking_b = repository.create_appointment_booking(
        provider="mock",
        title="Consulta B",
        starts_at="2030-05-02T13:00:00+00:00",
        ends_at="2030-05-02T14:00:00+00:00",
        timezone="America/Sao_Paulo",
        company_id=company_b,
        contact_id=contact_b,
        integration_id=integration_b,
    )

    assert repository.get_contact(contact_a, company_id=company_a)["company_id"] == company_a
    assert repository.get_contact(contact_a, company_id=company_b) is None
    assert repository.get_contact(contact_b, company_id=company_a) is None

    assert {item["company_id"] for item in repository.get_messages(company_id=company_a)} == {company_a}
    assert {item["company_id"] for item in repository.get_messages(company_id=company_b)} == {company_b}

    assert repository.get_pending_question(pending_question_a, company_id=company_a)["company_id"] == company_a
    assert repository.get_pending_question(pending_question_a, company_id=company_b) is None
    assert repository.get_pending_question(pending_question_b, company_id=company_a) is None
    assert {item["company_id"] for item in repository.get_pending_questions(company_id=company_a)} == {company_a}
    assert {item["company_id"] for item in repository.get_pending_questions(company_id=company_b)} == {company_b}

    assert repository.get_knowledge_entry(knowledge_entry_a, company_id=company_a)["company_id"] == company_a
    assert repository.get_knowledge_entry(knowledge_entry_a, company_id=company_b) is None
    assert repository.get_knowledge_entry(knowledge_entry_b, company_id=company_a) is None
    assert {item["company_id"] for item in repository.get_knowledge_entries(company_id=company_a)} == {company_a}
    assert {item["company_id"] for item in repository.get_knowledge_entries(company_id=company_b)} == {company_b}

    assert repository.get_message_example(message_example_a, company_id=company_a)["company_id"] == company_a
    assert repository.get_message_example(message_example_a, company_id=company_b) is None
    assert repository.get_message_example(message_example_b, company_id=company_a) is None
    assert {item["company_id"] for item in repository.get_message_examples(company_id=company_a)} == {company_a}
    assert {item["company_id"] for item in repository.get_message_examples(company_id=company_b)} == {company_b}
    assert {item["company_id"] for item in repository.get_exportable_message_examples(company_id=company_a)} == {company_a}
    assert {item["company_id"] for item in repository.get_exportable_message_examples(company_id=company_b)} == {company_b}

    assert repository.get_outbound_message(outbound_message_a, company_id=company_a)["company_id"] == company_a
    assert repository.get_outbound_message(outbound_message_a, company_id=company_b) is None
    assert repository.get_outbound_message(outbound_message_b, company_id=company_a) is None
    assert {item["company_id"] for item in repository.get_outbound_messages(company_id=company_a)} == {company_a}
    assert {item["company_id"] for item in repository.get_outbound_messages(company_id=company_b)} == {company_b}

    assert repository.get_calendar_integration(integration_a, company_id=company_a)["company_id"] == company_a
    assert repository.get_calendar_integration(integration_a, company_id=company_b) is None
    assert repository.get_active_calendar_integration(company_id=company_a)["company_id"] == company_a
    assert repository.get_active_calendar_integration(company_id=company_b)["company_id"] == company_b

    assert repository.get_appointment_booking(booking_a, company_id=company_a)["company_id"] == company_a
    assert repository.get_appointment_booking(booking_a, company_id=company_b) is None
    assert repository.get_appointment_booking(booking_b, company_id=company_a) is None
    assert {item["company_id"] for item in repository.get_appointment_bookings(company_id=company_a)} == {company_a}
    assert {item["company_id"] for item in repository.get_appointment_bookings(company_id=company_b)} == {company_b}


def test_same_contact_id_can_exist_with_independent_profiles_per_tenant() -> None:
    repository = RuntimeRepository()
    suffix = uuid4().hex

    company_a = repository.create_company(
        slug=f"same-contact-a-{suffix}",
        name=f"Same Contact A {suffix}",
    )
    company_b = repository.create_company(
        slug=f"same-contact-b-{suffix}",
        name=f"Same Contact B {suffix}",
    )

    shared_contact_id = f"55119999{suffix[:8]}"

    contact_name_a = f"Alice Tenant A {suffix[:6]}"
    contact_name_b = f"Bruno Tenant B {suffix[:6]}"
    profile_a = f"Perfil A {suffix[:6]}"
    profile_b = f"Perfil B {suffix[:6]}"

    repository.save_contact(
        shared_contact_id,
        contact_name_a,
        company_id=company_a,
        customer_profile=profile_a,
        notes="Observacao A",
    )
    repository.save_contact(
        shared_contact_id,
        contact_name_b,
        company_id=company_b,
        customer_profile=profile_b,
        notes="Observacao B",
    )

    repository.update_contact_memory(
        contact_id=shared_contact_id,
        company_id=company_a,
        last_classification="lead_novo",
        conversation_summary="Resumo exclusivo da empresa A.",
        pipeline_stage="qualificando",
        next_action="Enviar proposta A.",
        lead_temperature="warm",
    )
    repository.update_contact_memory(
        contact_id=shared_contact_id,
        company_id=company_b,
        last_classification="suporte",
        conversation_summary="Resumo exclusivo da empresa B.",
        pipeline_stage="encerrado",
        next_action="Acionar suporte B.",
        lead_temperature="cold",
    )

    repository.save_message(
        contact_id=shared_contact_id,
        company_id=company_a,
        message="Mensagem empresa A",
        classification="lead_novo",
        reply="Resposta empresa A",
    )
    repository.save_message(
        contact_id=shared_contact_id,
        company_id=company_b,
        message="Mensagem empresa B",
        classification="suporte",
        reply="Resposta empresa B",
    )

    contact_a = repository.get_contact(shared_contact_id, company_id=company_a)
    contact_b = repository.get_contact(shared_contact_id, company_id=company_b)

    assert contact_a is not None
    assert contact_b is not None
    assert contact_a["company_id"] == company_a
    assert contact_b["company_id"] == company_b
    assert contact_a["contact_name"] == contact_name_a
    assert contact_b["contact_name"] == contact_name_b
    assert contact_a["customer_profile"] == profile_a
    assert contact_b["customer_profile"] == profile_b
    assert contact_a["conversation_summary"] == "Resumo exclusivo da empresa A."
    assert contact_b["conversation_summary"] == "Resumo exclusivo da empresa B."
    assert contact_a["pipeline_stage"] == "qualificando"
    assert contact_b["pipeline_stage"] == "encerrado"

    contacts_a = repository.get_contacts(company_id=company_a)
    contacts_b = repository.get_contacts(company_id=company_b)
    matching_contacts_a = [item for item in contacts_a if item["contact_id"] == shared_contact_id]
    matching_contacts_b = [item for item in contacts_b if item["contact_id"] == shared_contact_id]
    assert len(matching_contacts_a) == 1
    assert len(matching_contacts_b) == 1
    assert matching_contacts_a[0]["contact_name"] == contact_name_a
    assert matching_contacts_b[0]["contact_name"] == contact_name_b

    search_a = repository.search_contacts(query=contact_name_a.lower(), company_id=company_a)
    search_b = repository.search_contacts(query=contact_name_b.lower(), company_id=company_b)
    assert [item["company_id"] for item in search_a] == [company_a]
    assert [item["company_id"] for item in search_b] == [company_b]
    assert all(item["contact_name"] == contact_name_a for item in search_a)
    assert all(item["contact_name"] == contact_name_b for item in search_b)

    messages_a = repository.get_messages(company_id=company_a)
    messages_b = repository.get_messages(company_id=company_b)
    assert {item["message"] for item in messages_a if item["contact_id"] == shared_contact_id} == {"Mensagem empresa A"}
    assert {item["message"] for item in messages_b if item["contact_id"] == shared_contact_id} == {"Mensagem empresa B"}
