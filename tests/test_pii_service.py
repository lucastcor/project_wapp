"""Anonimização de PII: detecção por valor, não por menção."""

import logging

from app.services.pii_service import (
    POLICY_HEALTHCARE_STRICT,
    POLICY_STANDARD,
    anonymize_text,
    contains_pii,
    pseudonymize_identifier,
)


def test_email_is_fully_anonymized_not_only_the_at_sign() -> None:
    sanitized, report = anonymize_text("Meu email é joao.silva@empresa.com.br, pode enviar")

    assert "joao.silva" not in sanitized
    assert "empresa.com.br" not in sanitized
    assert "[EMAIL]" in sanitized
    assert report.as_dict()["email"] == 1


def test_document_numbers_are_detected_by_value() -> None:
    sanitized, report = anonymize_text("Segue CPF 529.982.247-25 e CNPJ 11.222.333/0001-81")

    assert "529.982.247-25" not in sanitized
    assert "11.222.333/0001-81" not in sanitized
    assert report.as_dict()["cpf"] == 1
    assert report.as_dict()["cnpj"] == 1


def test_invalid_document_numbers_are_not_replaced() -> None:
    # Sequência com formato de CPF mas dígito verificador inválido.
    sanitized, report = anonymize_text("Protocolo 111.111.111-12 do pedido")

    assert "111.111.111-12" in sanitized
    assert "cpf" not in report.as_dict()


def test_the_word_cpf_alone_is_not_treated_as_pii() -> None:
    # A regra anterior barrava qualquer texto que citasse "cpf".
    sanitized, report = anonymize_text("Preciso do meu CPF para a nota?")

    assert sanitized == "Preciso do meu CPF para a nota?"
    assert report.has_pii is False


def test_phone_cep_and_card_are_anonymized() -> None:
    sanitized, report = anonymize_text(
        "Ligue (11) 98765-4321, CEP 01310-100, cartão 4111 1111 1111 1111"
    )

    assert "98765-4321" not in sanitized
    assert "01310-100" not in sanitized
    assert "4111 1111 1111 1111" not in sanitized
    counts = report.as_dict()
    assert counts["phone"] >= 1
    assert counts["cep"] == 1
    assert counts["credit_card"] == 1


def test_address_is_anonymized() -> None:
    sanitized, report = anonymize_text("Moro na Rua das Flores, nº 250, perto do mercado")

    assert "Rua das Flores" not in sanitized
    assert report.as_dict()["street_address"] == 1


def test_healthcare_policy_adds_stricter_detectors() -> None:
    text = "Meu nome é Joana Pereira Lima e nasci em 12/03/1985. Cartão SUS 123456789012345"

    standard, standard_report = anonymize_text(text, policy=POLICY_STANDARD)
    strict, strict_report = anonymize_text(text, policy=POLICY_HEALTHCARE_STRICT)

    assert "Joana Pereira Lima" in standard
    assert "12/03/1985" in standard
    assert "full_name" not in standard_report.as_dict()

    assert "Joana Pereira Lima" not in strict
    assert "12/03/1985" not in strict
    counts = strict_report.as_dict()
    assert counts["full_name"] == 1
    assert counts["birth_date"] == 1
    assert counts["health_id"] == 1


def test_report_never_contains_the_sensitive_value() -> None:
    _, report = anonymize_text("Meu email é sigilo@empresa.com e telefone 11987654321")

    serialized = str(report.as_dict())
    assert "sigilo@empresa.com" not in serialized
    assert "11987654321" not in serialized
    assert set(report.as_dict()) <= {"email", "phone", "credit_card", "cep"}


def test_pii_is_not_written_to_logs(caplog) -> None:
    with caplog.at_level(logging.DEBUG):
        anonymize_text("Meu email é vazamento@empresa.com")

    assert "vazamento@empresa.com" not in caplog.text


def test_pseudonymized_identifier_is_stable_and_tenant_scoped() -> None:
    first = pseudonymize_identifier("5511999999999", salt="company:1")
    second = pseudonymize_identifier("5511999999999", salt="company:1")
    other_tenant = pseudonymize_identifier("5511999999999", salt="company:2")

    assert first == second
    assert first != other_tenant
    assert "5511999999999" not in first


def test_contains_pii_helper() -> None:
    assert contains_pii("fale com joao@empresa.com") is True
    assert contains_pii("nenhum dado sensível aqui") is False
