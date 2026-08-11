import logging
import time

from app.core.logging import get_logger, log_event, mask_identifier
from app.services.automation_trigger_service import build_message_processed_event
from app.services.classification_service import classify_message
from app.services.company_config_service import get_company_config
from app.services.conversation_workflow_service import handle_conversation_workflow
from app.services.decision_service import decide_message_action
from app.services.handoff_service import should_create_pending_question
from app.services.memory_service import update_conversation_summary
from app.services.pipeline_service import infer_pipeline_state
from app.services.reply_services import generate_business_reply
from app.storage.repository import RuntimeRepository

repository = RuntimeRepository()
logger = get_logger(__name__)


def _process_contact_message(
    contact_id: str,
    contact_name: str | None,
    message: str,
    company_id: int | None = None,
    company_slug: str | None = None,
) -> dict[str, str | float | bool | int | None]:
    start_time = time.perf_counter()
    masked_contact_id = mask_identifier(contact_id)

    log_event(
        logger,
        logging.INFO,
        "conversation.processing.started",
        contact_id=masked_contact_id,
        company_id=company_id,
        company_slug=company_slug,
        has_contact_name=bool(contact_name),
    )

    resolved_company_id = repository.resolve_company_id(
        company_id=company_id,
        company_slug=company_slug,
    )
    if resolved_company_id is None:
        resolved_company_id = repository.ensure_default_company()

    company_config = get_company_config(resolved_company_id)
    resolved_company_slug = company_config.company_slug or company_slug

    repository.save_contact(contact_id, contact_name, company_id=resolved_company_id)
    contact = repository.get_contact(contact_id, company_id=resolved_company_id)
    previous_summary = contact["conversation_summary"] if contact else None
    previous_classification = contact["last_classification"] if contact else None
    latest_customer_clarification = repository.get_latest_customer_clarification(
        contact_id,
        company_id=resolved_company_id,
    )
    company_context = company_config.context
    customer_profile = contact.get("customer_profile") if contact else None

    workflow_result = handle_conversation_workflow(
        contact_id=contact_id,
        contact_name=contact_name,
        message=message,
        company_id=resolved_company_id,
        company_context=company_context,
    )

    retrieval_trace: dict[str, object] = {}

    if workflow_result.handled:
        classification = str(workflow_result.classification or "workflow")
        confidence = 0.98
        reason = f"Fluxo conversacional tratado internamente: {workflow_result.workflow_type or 'workflow'}."
        decision = {
            "auto_reply": workflow_result.auto_reply,
            "action": workflow_result.action,
            "priority": workflow_result.priority,
        }
        reply = workflow_result.reply
        reply_source = workflow_result.source
        handoff_decision = {
            "should_handoff": False,
            "question_type": None,
        }
        pending_question_id = None

        log_event(
            logger,
            logging.INFO,
            "conversation.workflow.handled",
            contact_id=masked_contact_id,
            company_id=resolved_company_id,
            company_slug=resolved_company_slug,
            workflow_type=workflow_result.workflow_type,
            action=workflow_result.action,
        )
    else:
        classification_result = classify_message(message)
        classification = classification_result["classification"]
        confidence = float(classification_result["confidence"])
        reason = classification_result["reason"]

        log_event(
            logger,
            logging.INFO,
            "conversation.classification.completed",
            contact_id=masked_contact_id,
            company_id=resolved_company_id,
            company_slug=resolved_company_slug,
            classification=classification,
            classification_confidence=confidence,
            classification_reason=reason,
        )

        handoff_decision = should_create_pending_question(
            classification=classification,
            confidence=confidence,
            message=message,
            company_context=company_context,
        )

        decision = decide_message_action(classification, confidence)
        log_event(
            logger,
            logging.INFO,
            "conversation.decision.completed",
            contact_id=masked_contact_id,
            company_id=resolved_company_id,
            action=decision["action"],
            auto_reply=decision["auto_reply"],
            priority=decision["priority"],
            handoff_required=handoff_decision["should_handoff"],
        )

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
            repository.mark_clarification_consumed(
                latest_customer_clarification["id"],
                company_id=resolved_company_id,
            )
        elif decision["auto_reply"]:
            reply_result = generate_business_reply(
                classification=classification,
                message=message,
                contact_name=contact_name,
                conversation_summary=previous_summary,
                last_classification=previous_classification,
                pipeline_stage=contact.get("pipeline_stage") if contact else None,
                contact_id=contact_id,
                company_id=resolved_company_id,
                company_slug=resolved_company_slug,
                customer_profile=customer_profile,
            )
            reply = reply_result["reply"]
            reply_source = reply_result["source"]
            retrieval_trace = reply_result.get("retrieval") or {}
            log_event(
                logger,
                logging.INFO,
                "conversation.reply.generated",
                contact_id=masked_contact_id,
                company_id=resolved_company_id,
                reply_source=reply_source,
                reply_length=len(reply),
                knowledge_source_ids=[
                    source.get("chunk_id") for source in retrieval_trace.get("sources", [])
                ],
                style_example_ids=retrieval_trace.get("style_example_ids"),
                has_relevant_source=retrieval_trace.get("has_relevant_source"),
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
            log_event(
                logger,
                logging.WARNING,
                "conversation.handoff.created",
                contact_id=masked_contact_id,
                company_id=resolved_company_id,
                pending_question_id=pending_question_id,
                question_type=handoff_decision["question_type"],
            )

    handoff_required = bool(handoff_decision["should_handoff"]) and not latest_customer_clarification

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
        retrieval=retrieval_trace,
        # Outbox transacional: o fato para a automação é gravado no mesmo
        # commit da mensagem. A automação é pós-orquestração — a resposta
        # síncrona acima continua sendo dona do atendimento.
        automation_event_factory=lambda new_message_id: build_message_processed_event(
            company_id=resolved_company_id,
            message_id=new_message_id,
            contact_id=contact_id,
            classification=classification,
            confidence=confidence,
            action=str(decision["action"]),
            pipeline_stage=contact.get("pipeline_stage") if contact else None,
            customer_profile=customer_profile,
            handoff_required=handoff_required,
            reply_source=reply_source,
            has_relevant_source=bool(retrieval_trace.get("has_relevant_source")),
            auto_reply_sent=bool(decision["auto_reply"]) and bool(reply),
        ),
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
        company_id=resolved_company_id,
    )

    duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
    log_event(
        logger,
        logging.INFO,
        "conversation.processing.completed",
        contact_id=masked_contact_id,
        company_id=resolved_company_id,
        company_slug=resolved_company_slug,
        classification=classification,
        auto_reply=decision["auto_reply"],
        action=decision["action"],
        pipeline_stage=pipeline_state["pipeline_stage"],
        lead_temperature=pipeline_state["lead_temperature"],
        pending_question_id=pending_question_id,
        duration_ms=duration_ms,
    )

    return {
        "contact_id": contact_id,
        "company_id": resolved_company_id,
        "company_slug": resolved_company_slug,
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
