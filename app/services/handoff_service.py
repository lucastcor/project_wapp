

from typing import Any, TypedDict

from app.core.config import settings
from app.llm.factory import get_llm_provider
from app.llm.providers.openai_provider import OpenAIProvider
from app.services.company_policy_service import DEFAULT_AUTONOMY_POLICY, AutonomyPolicy

VALID_RESOLUTION_TYPES = {
    "final_answer",
    "needs_customer_clarification",
    "case_specific_exception",
    "global_rule",
}

VALID_KNOWLEDGE_ACTIONS = {
    "none",
    "store_as_knowledge",
    "promote_to_company_context",
}


class HandoffDecision(TypedDict):
    should_handoff: bool
    question_type: str | None
    question_for_owner: str | None


def _fallback_customer_facing_text(owner_answer: str) -> str:
    text = owner_answer.strip()

    replacements = [
        ("fala pra ele que", ""),
        ("fala para ele que", ""),
        ("fala pra ela que", ""),
        ("fala para ela que", ""),
        ("diz pra ele que", ""),
        ("diz para ele que", ""),
        ("diz pra ela que", ""),
        ("diz para ela que", ""),
        ("pergunta pra ele", ""),
        ("pergunta para ele", ""),
        ("pergunta pra ela", ""),
        ("pergunta para ela", ""),
    ]

    lowered = text.lower()
    for source, _ in replacements:
        if lowered.startswith(source):
            text = text[len(source):].strip(" .:-")
            break

    if not text:
        text = owner_answer.strip()

    if text and text[0].islower():
        text = text[0].upper() + text[1:]

    return text


def _handoff_result(
    should_handoff: bool,
    question_type: str | None = None,
    question_for_owner: str | None = None,
) -> HandoffDecision:
    return {
        "should_handoff": should_handoff,
        "question_type": question_type,
        "question_for_owner": question_for_owner,
    }


def should_create_pending_question(
    classification: str,
    confidence: float,
    message: str,
    company_context: dict[str, Any] | None = None,
    autonomy: AutonomyPolicy | None = None,
) -> HandoffDecision:
    """Decide se a mensagem vira pendência para a equipe.

    `autonomy` vem da política publicada da empresa (limiar, handoff em baixa
    confiança, intenções com humano obrigatório e com resposta automática).
    """
    normalized = message.lower()
    policy = autonomy or DEFAULT_AUTONOMY_POLICY

    # Spam é descartado por restrição fixa; não gera trabalho para a equipe.
    if classification == "spam":
        return _handoff_result(False)

    low_confidence = policy.is_low_confidence(classification, confidence)
    if low_confidence and policy.handoff_on_low_confidence:
        return _handoff_result(
            True,
            "classification_uncertain",
            (
                "A mensagem do cliente não pôde ser classificada com confiança suficiente. "
                "Como a empresa deve tratar esse tipo de solicitação?"
            ),
        )

    if any(term in normalized for term in ["desconto", "abaixar", "melhor valor", "negociar valor"]):
        return _handoff_result(
            True,
            "discount_request",
            "O cliente pediu desconto. A empresa autoriza algum desconto ou condição especial nesse caso?",
        )

    if any(term in normalized for term in ["tem disponível", "tem disponivel", "estoque", "pronta entrega"]):
        return _handoff_result(
            True,
            "inventory_or_availability",
            (
                "O cliente perguntou sobre disponibilidade ou estoque. "
                "Qual informação correta deve ser passada para esse item ou situação?"
            ),
        )

    if any(
        term in normalized
        for term in ["sábado", "sabado", "domingo", "feriado", "fora do horário", "fora do horario"]
    ):
        return _handoff_result(
            True,
            "schedule_exception",
            (
                "O cliente perguntou sobre exceção de horário ou atendimento fora da rotina padrão. "
                "Como a empresa deseja responder nesses casos?"
            ),
        )

    if any(
        term in normalized
        for term in ["troca", "reembolso", "devolução", "devolucao", "cancelamento", "estorno"]
    ):
        return _handoff_result(
            True,
            "policy_unknown",
            (
                "O cliente perguntou sobre política comercial ou exceção operacional. "
                "Qual regra correta deve ser aplicada neste caso?"
            ),
        )

    if any(term in normalized for term in ["sob medida", "personalizado", "encomenda", "customizado"]):
        return _handoff_result(
            True,
            "custom_case",
            (
                "O cliente trouxe um caso personalizado ou sob medida. "
                "Como a empresa quer conduzir esse tipo de solicitação?"
            ),
        )

    if any(term in normalized for term in ["aluguel", "locação", "locacao", "comprar ou alugar"]):
        return _handoff_result(
            True,
            "rental_or_sale_decision",
            (
                "O cliente trouxe uma dúvida sobre aluguel, venda ou condição de locação. "
                "Qual orientação comercial correta deve ser dada nesse caso?"
            ),
        )

    if classification in policy.handoff_intents:
        return _handoff_result(
            True,
            "handoff_required",
            (
                f"A política da empresa exige atendimento humano para mensagens de {classification}. "
                "Assuma a conversa ou oriente a resposta ao cliente."
            ),
        )

    # Intenção com confiança suficiente, mas que a empresa não liberou para
    # resposta automática: sem pendência, a mensagem ficaria sem resposta e
    # sem ninguém sabendo dela.
    if (
        not low_confidence
        and classification not in policy.auto_reply_intents
        and classification not in {"agendamento", "workflow"}
    ):
        return _handoff_result(
            True,
            "auto_reply_not_allowed",
            (
                f"A política da empresa não autoriza resposta automática para mensagens de {classification}. "
                "Como a equipe deve responder?"
            ),
        )

    return _handoff_result(False)


def build_customer_reply_from_owner_resolution(
    owner_answer: str,
    resolution_type: str | None,
    company_context: dict[str, Any] | None = None,
) -> str:
    fallback_text = _fallback_customer_facing_text(owner_answer)

    if settings.classification_provider not in {"openai", "ollama"}:
        return fallback_text

    context = company_context or {}
    company_name = context.get("company_name", "a empresa")
    brand_tone = context.get("brand_tone", {}) if isinstance(context, dict) else {}

    try:
        provider = get_llm_provider(openai_factory=OpenAIProvider)
        response = provider.generate_text((
                "Você é um assistente de atendimento que reescreve instruções internas do responsável da empresa "
                "como mensagem final para o cliente. "
                "Responda em português do Brasil. "
                "Mantenha a intenção da instrução, mas remova marcas internas como 'fala pra ele', 'diz pra ela', "
                "'pergunta para o cliente', ou outras expressões operacionais. "
                "A mensagem final deve soar natural, profissional, cordial e pronta para envio ao cliente. "
                "Não mencione que houve consulta interna. "
                "Se a instrução pedir dados adicionais, transforme isso em pergunta clara e educada ao cliente. "
                "Se a instrução já for uma resposta final, transforme em resposta objetiva e natural.\n\n"
                f"Empresa: {company_name}\n"
                f"Tom da marca: {brand_tone}\n"
                f"Tipo de resolução: {resolution_type or 'nao_informado'}\n"
                f"Instrução interna do responsável: {owner_answer}\n\n"
                "Retorne apenas a mensagem final que deve ser enviada ao cliente."
            ),
        )
        rewritten_text = (response or "").strip()
        return rewritten_text or fallback_text
    except Exception:
        return fallback_text


def normalize_owner_resolution(payload: dict[str, Any]) -> dict[str, Any]:
    owner_answer = str(payload.get("owner_answer", "")).strip()
    resolution_type = payload.get("resolution_type")
    customer_followup_question = payload.get("customer_followup_question")
    applies_globally = bool(payload.get("applies_globally", False))
    knowledge_action = payload.get("knowledge_action") or "none"
    company_context = payload.get("company_context")

    if not owner_answer:
        raise ValueError("owner_answer must be a non-empty string.")

    if resolution_type is not None and resolution_type not in VALID_RESOLUTION_TYPES:
        raise ValueError(
            "resolution_type must be one of: "
            f"{', '.join(sorted(VALID_RESOLUTION_TYPES))}."
        )

    if knowledge_action not in VALID_KNOWLEDGE_ACTIONS:
        raise ValueError(
            "knowledge_action must be one of: "
            f"{', '.join(sorted(VALID_KNOWLEDGE_ACTIONS))}."
        )

    if resolution_type == "needs_customer_clarification":
        followup_source = str(customer_followup_question or owner_answer).strip()
        if not followup_source:
            raise ValueError(
                "customer_followup_question or owner_answer must be provided when resolution_type is needs_customer_clarification."
            )
        customer_followup_question = build_customer_reply_from_owner_resolution(
            owner_answer=followup_source,
            resolution_type=resolution_type,
            company_context=company_context,
        )

    if resolution_type == "final_answer":
        owner_answer = build_customer_reply_from_owner_resolution(
            owner_answer=owner_answer,
            resolution_type=resolution_type,
            company_context=company_context,
        )

    return {
        "owner_answer": owner_answer,
        "resolution_type": resolution_type,
        "customer_followup_question": customer_followup_question,
        "applies_globally": applies_globally,
        "knowledge_action": knowledge_action,
    }
