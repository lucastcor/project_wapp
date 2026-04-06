from typing import Any

import streamlit as st

from app.ui.admin_api_client import AdminApiClientError


def render_contacts_page(page_context: dict[str, Any]) -> None:
    client = page_context["client"]
    selected_company_id = page_context.get("company_id")

    st.header("Contacts")
    st.caption("Base operacional de contatos acompanhados pelo runtime.")

    try:
        contacts = client.get_contacts()
    except AdminApiClientError as exc:
        st.error(str(exc))
        return
    except Exception as exc:
        st.error(f"Erro inesperado ao carregar contatos: {exc}")
        return

    if selected_company_id is not None:
        contacts = [contact for contact in contacts if contact.get("company_id") == selected_company_id]

    if not contacts:
        st.info("Nenhum contato encontrado para o escopo atual.")
        return

    for contact in contacts:
        with st.container(border=True):
            st.write(f"**{contact.get('contact_name') or contact.get('contact_id')}**")
            st.write(f"Contato: {contact.get('contact_id')}")
            st.write(f"Company ID: {contact.get('company_id') or '—'}")
            st.write(f"Pipeline: {contact.get('pipeline_stage') or '—'}")
            st.write(f"Próxima ação: {contact.get('next_action') or '—'}")
            st.write(f"Temperatura: {contact.get('lead_temperature') or '—'}")
            if contact.get("conversation_summary"):
                st.caption(str(contact["conversation_summary"]))
            st.caption(
                f"Última classificação: {contact.get('last_classification') or '—'} | "
                f"Última interação: {contact.get('last_interaction_at') or '—'}"
            )
