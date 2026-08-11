

from typing import Any

import streamlit as st

from app.ui.admin_api_client import AdminApiClientError

RESOLUTION_OPTIONS = {
    "Responder ao cliente": "final_answer",
    "Pedir clarificação ao cliente": "needs_customer_clarification",
    "Salvar como regra global": "global_rule",
    "Exceção específica do caso": "case_specific_exception",
}

KNOWLEDGE_OPTIONS = {
    "Nenhuma ação": None,
    "Salvar como conhecimento": "store_as_knowledge",
}



def render_pending_questions_page(page_context: dict[str, Any]) -> None:
    client = page_context["client"]
    selected_company_id = page_context.get("company_id")

    st.header("Pending Questions")
    st.caption("Fila de dúvidas escaladas para responsável humano.")

    try:
        pending_questions = client.get_pending_questions()
    except AdminApiClientError as exc:
        st.error(str(exc))
        return
    except Exception as exc:
        st.error(f"Erro inesperado ao carregar pending questions: {exc}")
        return

    if selected_company_id is not None:
        pending_questions = [
            question
            for question in pending_questions
            if question.get("company_id") == selected_company_id
        ]

    if not pending_questions:
        st.info("Nenhuma pending question encontrada para o escopo atual.")
        return

    for question in pending_questions:
        question_id = question["id"]
        with st.container(border=True):
            st.markdown(f"### Pending Question #{question_id}")
            st.write(f"**Contato:** {question.get('contact_id')}")
            st.write(f"**Status:** {question.get('status')}")
            st.write(f"**Tipo:** {question.get('question_type')}")
            st.write(f"**Mensagem do cliente:** {question.get('customer_message')}")
            st.write(f"**Pergunta para o responsável:** {question.get('question_for_owner')}")
            st.caption(
                f"Criada em: {question.get('created_at')} | Company ID: {question.get('company_id') or '—'}"
            )

            if question.get("status") != "pending":
                st.success("Esta pending question já foi resolvida.")
                continue

            with st.form(f"pending_question_answer_form_{question_id}"):
                owner_answer = st.text_area(
                    "Resposta do responsável",
                    key=f"owner_answer_{question_id}",
                    height=150,
                )
                resolution_label = st.selectbox(
                    "Tipo de resolução",
                    options=list(RESOLUTION_OPTIONS.keys()),
                    key=f"resolution_type_{question_id}",
                )
                customer_followup_question = st.text_input(
                    "Pergunta de clarificação ao cliente (opcional)",
                    key=f"customer_followup_question_{question_id}",
                )
                applies_globally = st.checkbox(
                    "Aplicar globalmente",
                    value=False,
                    key=f"applies_globally_{question_id}",
                )
                knowledge_label = st.selectbox(
                    "Ação de conhecimento",
                    options=list(KNOWLEDGE_OPTIONS.keys()),
                    key=f"knowledge_action_{question_id}",
                )

                submitted = st.form_submit_button("Enviar resposta")
                if submitted:
                    try:
                        result = client.answer_pending_question(
                            question_id=question_id,
                            owner_answer=owner_answer.strip(),
                            resolution_type=RESOLUTION_OPTIONS[resolution_label],
                            customer_followup_question=customer_followup_question.strip() or None,
                            applies_globally=applies_globally,
                            knowledge_action=KNOWLEDGE_OPTIONS[knowledge_label],
                        )
                        st.success(
                            f"Pending question #{question_id} respondida com sucesso."
                        )
                        outbound_message_id = result.get("outbound_message_id")
                        if outbound_message_id:
                            st.info(f"Outbound gerado: #{outbound_message_id}")
                        st.rerun()
                    except AdminApiClientError as exc:
                        st.error(str(exc))
                    except Exception as exc:
                        st.error(f"Erro inesperado ao responder pending question: {exc}")
