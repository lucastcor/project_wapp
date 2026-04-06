from typing import Any

import streamlit as st

from app.ui.admin_api_client import AdminApiClientError


def render_knowledge_page(page_context: dict[str, Any]) -> None:
    client = page_context["client"]
    selected_company_id = page_context.get("company_id")

    st.header("Knowledge Base")
    st.caption("Entradas de conhecimento operacional geradas ou curadas para o atendimento.")

    try:
        knowledge_entries = client.get_knowledge_entries(company_id=selected_company_id)
    except AdminApiClientError as exc:
        st.error(str(exc))
        return
    except Exception as exc:
        st.error(f"Erro inesperado ao carregar conhecimento: {exc}")
        return

    if not knowledge_entries:
        st.info("Nenhuma knowledge entry encontrada para o escopo atual.")
        return

    for entry in knowledge_entries:
        with st.container(border=True):
            st.write(f"**{entry.get('title')}**")
            st.write(f"Tipo: {entry.get('knowledge_type')}")
            st.write(f"Status: {entry.get('status')}")
            st.write(f"Company ID: {entry.get('company_id') or '—'}")
            st.write(f"Aplica globalmente: {'Sim' if entry.get('applies_globally') else 'Não'}")
            st.write(str(entry.get("content") or ""))
            st.caption(
                f"Origem da pending question: {entry.get('source_question_id') or '—'} | "
                f"Contato: {entry.get('contact_id') or '—'} | Criado em: {entry.get('created_at')}"
            )
