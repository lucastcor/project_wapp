from app.services.classification_service import classify_message
from app.services.decision_service import decide_message_action
from app.storage.repository import RuntimeRepository
from app.services.reply_services import generate_business_reply
from app.services.memory_service import update_conversation_summary
from app.services.pipeline_service import infer_pipeline_state
from app.services.handoff_service import should_create_pending_question
from app.core.company_context import get_company_context

repository = RuntimeRepository()


def _process_contact_message(
    contact_id: str,
    contact_name: str | None,
    message: str,
    company_id: int | None = None,
    company_slug: str | None = None,
) -> dict[str, str | float | bool | int | None]:
    resolved_company_id = repository.resolve_company_id(
        company_id=company_id,
        company_slug=company_slug,
    )
    if resolved_company_id is None:
        resolved_company_id = repository.ensure_default_company()
    repository.save_contact(contact_id, contact_name, company_id=resolved_company_id)
    contact = repository.get_contact(contact_id)
    previous_summary = contact["conversation_summary"] if contact else None
    previous_classification = contact["last_classification"] if contact else None
    latest_customer_clarification = repository.get_latest_customer_clarification(contact_id)

    classification_result = classify_message(message)
    classification = classification_result["classification"]
    confidence = float(classification_result["confidence"])
    reason = classification_result["reason"]

    company_context = get_company_context()
    handoff_decision = should_create_pending_question(
        classification=classification,
        confidence=confidence,
        message=message,
        company_context=company_context,
    )

    decision = decide_message_action(classification, confidence)

    reply = ""
    reply_source = "none"

    if latest_customer_clarification:
        reply = latest_customer_clarification["customer_followup_question"]
        reply_source = "owner_clarification"
        decision = {
            "auto_reply": True,
            "action": "request_customer_clarification",
            "priority": "high",
        }
        repository.mark_clarification_consumed(latest_customer_clarification["id"])
    elif decision["auto_reply"]:
        reply_result = generate_business_reply(
            classification=classification,
            message=message,
            contact_name=contact_name,
            conversation_summary=previous_summary,
            last_classification=previous_classification,
            pipeline_stage=contact["pipeline_stage"] if contact else None,
            contact_id=contact_id,
        )
        reply = reply_result["reply"]
        reply_source = reply_result["source"]

    repository.save_message(
        contact_id=contact_id,
        company_id=resolved_company_id,
        message=message,
        classification=classification,
        reply=reply,
        classification_reason=reason,
        classification_confidence=confidence,
        auto_reply=decision["auto_reply"],
        reply_source=reply_source,
        action=decision["action"],
        priority=decision["priority"],
    )

    pending_question_id: int | None = None
    if handoff_decision["should_handoff"] and not latest_customer_clarification:
        pending_question_id = repository.create_pending_question(
            contact_id=contact_id,
            company_id=resolved_company_id,
            customer_message=message,
            question_type=handoff_decision["question_type"],
            question_for_owner=handoff_decision["question_for_owner"],
        )

    new_summary = update_conversation_summary(
        previous_summary=previous_summary,
        latest_message=message,
        latest_classification=classification,
        latest_reply=reply,
    )

    pipeline_state = infer_pipeline_state(
        conversation_summary=new_summary,
        latest_message=message,
        latest_classification=classification,
    )

    repository.update_contact_memory(
        contact_id=contact_id,
        last_classification=classification,
        conversation_summary=new_summary,
        pipeline_stage=pipeline_state["pipeline_stage"],
        next_action=pipeline_state["next_action"],
        lead_temperature=pipeline_state["lead_temperature"],
    )

    return {
        "contact_id": contact_id,
        "company_id": resolved_company_id,
        "company_slug": company_slug,
        "classification": classification,
        "classification_reason": reason,
        "classification_confidence": confidence,
        "reply": reply,
        "source": reply_source,
        "auto_reply": decision["auto_reply"],
        "action": decision["action"],
        "priority": decision["priority"],
        "pipeline_stage": pipeline_state["pipeline_stage"],
        "next_action": pipeline_state["next_action"],
        "lead_temperature": pipeline_state["lead_temperature"],
        "handoff_required": handoff_decision["should_handoff"] and not latest_customer_clarification,
        "pending_question_id": pending_question_id,
        "question_type": handoff_decision["question_type"],
        "customer_followup_question": latest_customer_clarification["customer_followup_question"] if latest_customer_clarification else None,
    }


def process_incoming_message(
    contact_id: str,
    contact_name: str | None,
    message: str,
    company_id: int | None = None,
    company_slug: str | None = None,
) -> dict[str, str | float | bool | int | None]:
    return _process_contact_message(
        contact_id=contact_id,
        contact_name=contact_name,
        message=message,
        company_id=company_id,
        company_slug=company_slug,
    )


def process_whatsapp_webhook(
    from_number: str,
    from_name: str | None,
    text: str,
    company_id: int | None = None,
    company_slug: str | None = None,
) -> dict[str, str | float | bool | int | None]:
    return _process_contact_message(
        contact_id=from_number,
        contact_name=from_name,
        message=text,
        company_id=company_id,
        company_slug=company_slug,
    )