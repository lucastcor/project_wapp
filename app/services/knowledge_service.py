from typing import Any

VALID_KNOWLEDGE_TYPES = {
    "discount_policy",
    "business_rule",
    "product_info",
    "availability_rule",
    "schedule_rule",
    "rental_rule",
    "custom_case_guidance",
    "general_faq",
}


def should_store_resolution_as_knowledge(resolution: dict[str, Any]) -> bool:
    knowledge_action = resolution.get("knowledge_action") or "none"
    applies_globally = bool(resolution.get("applies_globally", False))
    resolution_type = resolution.get("resolution_type")

    if knowledge_action == "none":
        return False

    if resolution_type == "needs_customer_clarification":
        return False

    if knowledge_action == "promote_to_company_context":
        return True

    return bool(knowledge_action == "store_as_knowledge" and (applies_globally or resolution_type == "global_rule"))


def build_knowledge_entry_from_resolution(
    pending_question: dict[str, Any],
    resolution: dict[str, Any],
) -> dict[str, Any]:
    question_type = pending_question.get("question_type") or "general_faq"
    customer_message = pending_question.get("customer_message") or ""
    owner_answer = resolution["owner_answer"]

    knowledge_type_map = {
        "discount_request": "discount_policy",
        "inventory_or_availability": "availability_rule",
        "schedule_exception": "schedule_rule",
        "policy_unknown": "business_rule",
        "custom_case": "custom_case_guidance",
        "rental_or_sale_decision": "rental_rule",
        "classification_uncertain": "general_faq",
    }

    knowledge_type = knowledge_type_map.get(question_type, "general_faq")
    if knowledge_type not in VALID_KNOWLEDGE_TYPES:
        knowledge_type = "general_faq"

    title_map = {
        "discount_request": "Política de desconto",
        "inventory_or_availability": "Regra de disponibilidade ou estoque",
        "schedule_exception": "Regra de horário ou atendimento excepcional",
        "policy_unknown": "Regra comercial complementar",
        "custom_case": "Orientação para caso personalizado",
        "rental_or_sale_decision": "Regra de aluguel ou venda",
        "classification_uncertain": "Orientação geral de atendimento",
    }

    title = title_map.get(question_type, "Conhecimento complementar da empresa")

    content = (
        f"Pergunta original do cliente: {customer_message}\n"
        f"Orientação do responsável: {owner_answer}"
    )

    company_id = pending_question.get("company_id")

    return {
        "title": title,
        "content": content,
        "knowledge_type": knowledge_type,
        "source_question_id": pending_question.get("id"),
        "contact_id": pending_question.get("contact_id"),
        "company_id": company_id,
        "applies_globally": bool(resolution.get("applies_globally", False)),
        "status": "active",
    }
