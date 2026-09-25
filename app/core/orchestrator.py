import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.config import is_production_like
from app.core.logging import get_logger, log_event, mask_identifier
from app.services.automation_trigger_service import (
    build_message_processed_event,
    emit_handoff_created,
)
from app.services.billing_service import consume_usage
from app.services.classification_service import classify_message
from app.services.company_config_service import get_company_config
from app.services.company_policy_service import (
    AFTER_HOURS_NOTICE_COOLDOWN,
    HANDOFF_NOTICE_COOLDOWN,
    UNSUPPORTED_CONTENT_NOTICE_COOLDOWN,
    EffectivePolicy,
    is_plain_greeting,
    is_within_business_hours,
    resolve_effective_policy,
)
from app.services.conversation_workflow_service import handle_conversation_workflow
from app.services.decision_service import MessageDecision, decide_message_action
from app.services.handoff_service import HandoffDecision, should_create_pending_question
from app.services.memory_service import update_conversation_summary
from app.services.pipeline_service import infer_pipeline_state
from app.services.reply_services import generate_business_reply
from app.services.whatsapp_delivery_policy import parse_channel_command
from app.storage import channel_delivery_repository as channel_state
from app.storage import ownership_repository
from app.storage.repository import RuntimeRepository

repository = RuntimeRepository()
logger = get_logger(__name__)

AFTER_HOURS_REPLY_SOURCE = "after_hours_notice"
HANDOFF_NOTICE_REPLY_SOURCE = "policy_handoff_notice"
UNSUPPORTED_CONTENT_REPLY_SOURCE = "unsupported_content_notice"
UNSUPPORTED_CONTENT_TYPE = "unsupported"
CHANNEL_COMMAND_REPLY_SOURCE = "channel_command"
HUMAN_OWNED_QUESTION_TYPE = "human_conversation_message"


class UnresolvedCompanyError(RuntimeError):
    """Mensagem sem empresa resolvível num runtime de produção."""


def resolve_company_runtime(company_id: int) -> tuple[dict[str, Any], EffectivePolicy]:
    """Contexto publicado e política efetiva da empresa (CONTRATOS, P01)."""
    config = get_company_config(company_id)
    return config.context, resolve_effective_policy(config)


def _no_handoff() -> HandoffDecision:
    return {
        "should_handoff": False,
        "question_type": None,
        "question_for_owner": None,
    }


def _parse_stored_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace(" ", "T", 1))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _reply_source_recent(
    contact_id: str,
    company_id: int,
    now: datetime,
    *,
    reply_source: str,
    cooldown: timedelta,
) -> bool:
    latest = repository.get_latest_message_with_reply_source(
        contact_id,
        company_id=company_id,
        reply_source=reply_source,
    )
    if not latest:
        return False
    sent_at = _parse_stored_timestamp(latest.get("created_at"))
    # Timestamp ilegível conta como recente: repetir o aviso é o erro pior.
    return sent_at is None or now - sent_at < cooldown


def _process_contact_message(
    contact_id: str,
    contact_name: str | None,
    message: str,
    company_id: int | None = None,
    company_slug: str | None = None,
    message_ref: str | None = None,
    content_type: str = "text",
) -> dict[str, Any]:
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
        if is_production_like():
            # Gravar na empresa default misturaria conversa e memória de um
            # tenant desconhecido com outra identidade.
            raise UnresolvedCompanyError(
                "Inbound message without a resolvable company; refusing to use the default company."
            )
        resolved_company_id = repository.ensure_default_company()

    resolved_company = repository.get_company(resolved_company_id)
    resolved_company_slug = (
        company_slug
        or (resolved_company.get("slug") if resolved_company else None)
    )

    repository.save_contact(contact_id, contact_name, company_id=resolved_company_id)
    contact = repository.get_contact(contact_id, company_id=resolved_company_id)
    previous_summary = contact["conversation_summary"] if contact else None
    previous_classification = contact["last_classification"] if contact else None
    latest_customer_clarification = repository.get_latest_customer_clarification(
        contact_id,
        company_id=resolved_company_id,
    )
    company_context, policy = resolve_company_runtime(resolved_company_id)
    now = datetime.now(UTC)
    within_business_hours = is_within_business_hours(company_context, now)
    after_hours_suspended = (
        policy.conversation.after_hours_mode == "notice_only" and not within_business_hours
    )

    pending_question_id: int | None = None
    decision: MessageDecision
    handoff_decision: HandoffDecision
    consumed_clarification: dict[str, Any] | None = None

    channel_command = (
        parse_channel_command(message)
        if content_type == "text" and str(message_ref or "").startswith("whatsapp:")
        else None
    )
    if channel_command == "opt_in" and not channel_state.is_opted_out(
        company_id=resolved_company_id, contact_id=contact_id
    ):
        # "voltar" sem descadastro prévio é conversa normal, não comando.
        channel_command = None

    human_owned = channel_command is None and ownership_repository.is_human_owned(
        company_id=resolved_company_id,
        contact_id=contact_id,
    )

    if channel_command is not None:
        # SAIR/VOLTAR (W06): registro explícito do cliente, sem classificador,
        # sem workflow e válido também fora do horário. A confirmação é
        # resposta à mensagem dele, não contato proativo.
        classification = "indefinido"
        confidence = 1.0
        if channel_command == "opt_out":
            channel_state.set_opt_out(
                company_id=resolved_company_id,
                contact_id=contact_id,
                source="whatsapp_keyword",
            )
            reply = policy.render(policy.conversation.opt_out_confirmation_message)
            reason = "Cliente pediu para não receber mensagens proativas."
        else:
            channel_state.clear_opt_out(company_id=resolved_company_id, contact_id=contact_id)
            reply = policy.render(policy.conversation.opt_in_confirmation_message)
            reason = "Cliente voltou a aceitar mensagens proativas."
        reply_source = CHANNEL_COMMAND_REPLY_SOURCE
        decision = {
            "auto_reply": True,
            "action": channel_command,
            "priority": "low",
        }
        handoff_decision = _no_handoff()
    elif human_owned:
        # Posse humana (P06, H01): a mensagem é persistida e publicada, mas o bot
        # não responde, não conduz fluxo nem chama modelo. Uma única pendência
        # aberta avisa a equipe, sem duplicar a cada nova mensagem.
        classification = "indefinido"
        confidence = 0.0
        reason = "Conversa em atendimento humano; bot suspenso até a devolução."
        reply = ""
        reply_source = "none"
        decision = {
            "auto_reply": False,
            "action": "human_owned",
            "priority": "medium",
        }
        already_open = repository.has_open_pending_question(
            contact_id,
            company_id=resolved_company_id,
            question_type=HUMAN_OWNED_QUESTION_TYPE,
        )
        handoff_decision = (
            _no_handoff()
            if already_open
            else {
                "should_handoff": True,
                "question_type": HUMAN_OWNED_QUESTION_TYPE,
                "question_for_owner": (
                    "O cliente escreveu durante o atendimento humano. Responda pela conversa "
                    "ou devolva o atendimento ao bot."
                ),
            }
        )
    elif after_hours_suspended:
        # Política da empresa: fora do horário o bot não conduz atendimento.
        # A mensagem é persistida; o aviso sai uma vez por janela e abre uma
        # única pendência para a equipe.
        classification = "indefinido"
        confidence = 0.0
        reason = "Fora do horário de atendimento; respostas automáticas suspensas pela política da empresa."
        already_notified = _reply_source_recent(
            contact_id,
            resolved_company_id,
            now,
            reply_source=AFTER_HOURS_REPLY_SOURCE,
            cooldown=AFTER_HOURS_NOTICE_COOLDOWN,
        )
        reply = "" if already_notified else policy.render(policy.conversation.after_hours_message)
        reply_source = AFTER_HOURS_REPLY_SOURCE if reply else "none"
        decision = {
            "auto_reply": bool(reply),
            "action": "after_hours",
            "priority": "medium",
        }
        handoff_decision = (
            _no_handoff()
            if already_notified
            else {
                "should_handoff": True,
                "question_type": "after_hours_contact",
                "question_for_owner": (
                    "O cliente escreveu fora do horário de atendimento e recebeu o aviso automático. "
                    "Retome a conversa no próximo período."
                ),
            }
        )
        log_event(
            logger,
            logging.INFO,
            "conversation.after_hours.suspended",
            contact_id=masked_contact_id,
            company_id=resolved_company_id,
            notice_sent=bool(reply),
        )
    elif content_type == UNSUPPORTED_CONTENT_TYPE:
        # Áudio, imagem e outros tipos sem texto: sem transcrição no piloto.
        # Orientação controlada, sem modelo, sem tocar em fluxo ativo e sem
        # repetir a cada mídia enviada em sequência (W07).
        classification = "indefinido"
        confidence = 0.0
        reason = "Conteúdo sem texto não suportado no piloto."
        already_oriented = _reply_source_recent(
            contact_id,
            resolved_company_id,
            now,
            reply_source=UNSUPPORTED_CONTENT_REPLY_SOURCE,
            cooldown=UNSUPPORTED_CONTENT_NOTICE_COOLDOWN,
        )
        reply = "" if already_oriented else policy.render(policy.conversation.unsupported_content_message)
        reply_source = UNSUPPORTED_CONTENT_REPLY_SOURCE if reply else "none"
        decision = {
            "auto_reply": bool(reply),
            "action": "unsupported_content",
            "priority": "low",
        }
        handoff_decision = _no_handoff()
    else:
        workflow_result = handle_conversation_workflow(
            contact_id=contact_id,
            contact_name=contact_name,
            message=message,
            company_id=resolved_company_id,
            company_context=company_context,
            policy=policy,
            message_ref=message_ref,
            content_type=content_type,
        )

        if workflow_result.handled:
            classification = str(workflow_result.classification or "workflow")
            confidence = 0.98
            reason = f"Fluxo conversacional tratado internamente: {workflow_result.workflow_type or 'workflow'}."
            decision = {
                "auto_reply": bool(workflow_result.auto_reply),
                "action": str(workflow_result.action or "workflow"),
                "priority": str(workflow_result.priority or "medium"),
            }
            reply = workflow_result.reply
            reply_source = workflow_result.source
            workflow_question_type = getattr(workflow_result, "handoff_question_type", None)
            workflow_question = getattr(workflow_result, "handoff_question", None)
            handoff_decision = (
                {
                    "should_handoff": True,
                    "question_type": str(workflow_question_type),
                    "question_for_owner": str(workflow_question),
                }
                if workflow_question_type and workflow_question
                else _no_handoff()
            )
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
        elif (
            policy.conversation.greeting_enabled
            and not latest_customer_clarification
            and is_plain_greeting(message)
        ):
            # Saudação sem pedido e sem fluxo ativo: resposta configurada,
            # determinística e sem chamada de modelo; não abre pendência.
            classification = "indefinido"
            confidence = 1.0
            reason = "Saudação sem pedido e sem fluxo ativo."
            decision = {
                "auto_reply": True,
                "action": "greeting",
                "priority": "low",
            }
            reply = policy.render(policy.conversation.greeting_message)
            reply_source = "policy_greeting"
            handoff_decision = _no_handoff()
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
                autonomy=policy.autonomy,
            )

            decision = decide_message_action(classification, confidence, policy.autonomy)
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
                consumed_clarification = latest_customer_clarification
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
                )
                reply = reply_result["reply"]
                reply_source = reply_result["source"]
                log_event(
                    logger,
                    logging.INFO,
                    "conversation.reply.generated",
                    contact_id=masked_contact_id,
                    company_id=resolved_company_id,
                    reply_source=reply_source,
                    reply_length=len(reply),
                )
            elif decision["action"] != "ignore":
                if handoff_decision["should_handoff"]:
                    # Silêncio só por política explícita: sem aviso, o cliente
                    # não sabe que alguém vai responder.
                    # Mensagens seguidas do mesmo contato ("ok", "obrigado")
                    # não repetem o aviso a cada vez.
                    if policy.conversation.handoff_notice_enabled and not _reply_source_recent(
                        contact_id,
                        resolved_company_id,
                        now,
                        reply_source=HANDOFF_NOTICE_REPLY_SOURCE,
                        cooldown=HANDOFF_NOTICE_COOLDOWN,
                    ):
                        reply = policy.render(policy.conversation.handoff_notice_message)
                        if not within_business_hours:
                            reply = f"{reply} {policy.render(policy.conversation.after_hours_message)}"
                        reply_source = HANDOFF_NOTICE_REPLY_SOURCE
                        decision = {
                            "auto_reply": True,
                            "action": decision["action"],
                            "priority": decision["priority"],
                        }
                elif policy.autonomy.is_low_confidence(classification, confidence):
                    reply = policy.render(policy.conversation.clarification_message)
                    reply_source = "policy_clarification"
                    decision = {
                        "auto_reply": True,
                        "action": "request_customer_clarification",
                        "priority": decision["priority"],
                    }

    handoff_created = bool(handoff_decision["should_handoff"] and not consumed_clarification)
    if handoff_created:
        question_type = handoff_decision["question_type"]
        question_for_owner = handoff_decision["question_for_owner"]
        if question_type is None or question_for_owner is None:
            raise RuntimeError("Handoff decision is missing its required question metadata.")
        pending_question_id = repository.create_pending_question(
            contact_id=contact_id,
            company_id=resolved_company_id,
            customer_message=message,
            question_type=question_type,
            question_for_owner=question_for_owner,
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
        # `trigger.handoffCreated` está na paleta do editor: sem esta
        # emissão, um fluxo publicado com esse gatilho nunca dispararia, e
        # o produto anunciaria uma capacidade que não existe.
        #
        # Fora do outbox transacional de propósito: o handoff já foi
        # gravado e comitado acima. `emit_event` é idempotente por
        # `event_key`, então uma reentrega não cria uma segunda execução.
        # Sob posse humana (P06) nenhuma automação reage à mensagem: um fluxo
        # publicado poderia chamar modelo ou enfileirar mensagem ao cliente.
        if not human_owned:
            emit_handoff_created(
                company_id=resolved_company_id,
                handoff_id=pending_question_id,
                contact_id=contact_id,
                question_type=question_type,
            )

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
        # Outbox transacional: o fato para a automação entra no mesmo commit
        # da mensagem, então não existe janela em que a conversa foi salva e
        # o evento se perdeu. A automação é pós-orquestração — a resposta
        # síncrona acima continua sendo dona do atendimento.
        automation_event_factory=None if human_owned else lambda new_message_id: build_message_processed_event(
            company_id=resolved_company_id,
            message_id=new_message_id,
            contact_id=contact_id,
            classification=classification,
            confidence=confidence or 0.0,
            action=str(decision["action"]),
            pipeline_stage=contact.get("pipeline_stage") if contact else None,
            customer_profile=(contact or {}).get("customer_profile"),
            handoff_required=handoff_created,
            reply_source=str(reply_source or ""),
            has_relevant_source=bool(reply_source and reply_source != "fallback"),
            auto_reply_sent=bool(decision["auto_reply"] and reply),
        ),
    )
    try:
        consume_usage(
            company_id=resolved_company_id,
            metric="messages",
        )
        if handoff_created:
            consume_usage(
                company_id=resolved_company_id,
                metric="handoffs",
            )
    except Exception as exc:  # Billing telemetry is non-blocking for inbound safety.
        # Inbound conversations and safety handoffs are deliberately fail-open;
        # billing telemetry must never make the customer channel unavailable.
        log_event(
            logger,
            logging.WARNING,
            "billing.usage.soft_meter_failed",
            company_id=resolved_company_id,
            error_type=type(exc).__name__,
        )

    if human_owned:
        # Posse humana (P06, A2 da revisão): resumo e estágio vêm do que já está
        # gravado. Nenhum modelo recebe o texto do cliente e a memória do contato
        # não é sobrescrita com "indefinido" enquanto a pessoa atende.
        stored_contact = contact or {}
        pipeline_state = {
            "pipeline_stage": stored_contact.get("pipeline_stage"),
            "next_action": stored_contact.get("next_action"),
            "lead_temperature": stored_contact.get("lead_temperature"),
        }
    else:
        # A saudação pura é um caminho de política deliberadamente barato e
        # determinístico. Enriquecer memória/pipeline com o modelo aqui faria
        # duas inferências apesar de a resposta não precisar de nenhuma.
        allow_enrichment_model = decision["action"] != "greeting"
        new_summary = update_conversation_summary(
            previous_summary=previous_summary,
            latest_message=message,
            latest_classification=classification,
            latest_reply=reply,
            allow_model=allow_enrichment_model,
        )

        if decision["action"] == "greeting":
            # Saudação não encerra nem requalifica o lead. Preserve o estágio
            # existente; no primeiro contato, apenas mantenha-o como novo lead.
            pipeline_state = {
                "pipeline_stage": (contact or {}).get("pipeline_stage") or "novo_lead",
                "next_action": (contact or {}).get("next_action")
                or "Entender a necessidade do contato e coletar o contexto inicial.",
                "lead_temperature": (contact or {}).get("lead_temperature") or "cold",
            }
        else:
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
        "handoff_required": handoff_created,
        "pending_question_id": pending_question_id,
        "question_type": handoff_decision["question_type"],
        "customer_followup_question": (
            consumed_clarification["customer_followup_question"] if consumed_clarification else None
        ),
    }


def process_incoming_message(
    contact_id: str,
    contact_name: str | None,
    message: str,
    company_id: int | None = None,
    company_slug: str | None = None,
) -> dict[str, Any]:
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
    message_ref: str | None = None,
    content_type: str = "text",
) -> dict[str, Any]:
    return _process_contact_message(
        contact_id=from_number,
        contact_name=from_name,
        message=text,
        company_id=company_id,
        company_slug=company_slug,
        message_ref=message_ref,
        content_type=content_type,
    )
