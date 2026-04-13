from typing import Any

import streamlit as st

from app.ui.admin_api_client import AdminApiClientError


def render_contacts_page(page_context: dict[str, Any]) -> None:
    client = page_context["client"]
    selected_company_id = page_context.get("company_id")

    st.header("Contacts")
    st.caption("Base operacional de contatos, com busca rápida e atualização de perfil do cliente.")

    search_query = st.text_input("Buscar por nome ou número")

    try:
        if search_query.strip():
            contacts = client.search_contacts(
                query=search_query.strip(),
                company_id=selected_company_id,
                limit=50,
            )
        else:
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
            st.write(f"Perfil do cliente: {contact.get('customer_profile') or '—'}")
            if contact.get("conversation_summary"):
                st.caption(str(contact["conversation_summary"]))
            st.caption(
                f"Última classificação: {contact.get('last_classification') or '—'} | "
                f"Última interação: {contact.get('last_interaction_at') or '—'}"
            )

            with st.expander("Editar contato"):
                with st.form(f"contact_edit_{contact.get('contact_id')}"):
                    updated_name = st.text_input("Nome", value=str(contact.get("contact_name") or ""))
                    updated_profile = st.text_input(
                        "Customer profile",
                        value=str(contact.get("customer_profile") or ""),
                        placeholder="new_customer, returning_customer, vip...",
                    )
                    updated_notes = st.text_area(
                        "Notas",
                        value=str(contact.get("notes") or ""),
                        height=100,
                    )
                    submitted = st.form_submit_button("Salvar contato", use_container_width=True)
                    if submitted:
                        try:
                            client.update_contact(
                                contact_id=str(contact["contact_id"]),
                                contact_name=updated_name.strip() or None,
                                customer_profile=updated_profile.strip() or None,
                                notes=updated_notes.strip() or None,
                                company_id=selected_company_id,
                            )
                            st.success("Contato atualizado com sucesso.")
                            st.rerun()
                        except AdminApiClientError as exc:
                            st.error(str(exc))
                        except Exception as exc:
                            st.error(f"Erro inesperado ao atualizar contato: {exc}")
