

from typing import Any

import json
import streamlit as st

from app.ui.admin_api_client import AdminApiClientError



def render_company_context_page(page_context: dict[str, Any]) -> None:
    client = page_context["client"]
    company_slug = page_context.get("company_slug")

    st.header("Company Context")
    if company_slug:
        st.caption(f"Editando contexto da empresa: {company_slug}")
    else:
        st.caption("Editando contexto global/padrão.")

    try:
        context_data = client.get_company_context(company_slug=company_slug)
    except AdminApiClientError as exc:
        st.error(str(exc))
        return
    except Exception as exc:
        st.error(f"Erro inesperado ao carregar company context: {exc}")
        return

    pretty_json = json.dumps(context_data, ensure_ascii=False, indent=2)

    st.subheader("Editor de contexto")
    edited_json = st.text_area(
        "JSON do contexto",
        value=pretty_json,
        height=500,
    )

    col_preview, col_actions = st.columns([1.2, 0.8])

    with col_preview:
        st.subheader("Pré-visualização")
        st.json(context_data)

    with col_actions:
        st.subheader("Ações")
        if st.button("Salvar contexto", use_container_width=True):
            try:
                parsed_data = json.loads(edited_json)
                client.update_company_context(
                    data=parsed_data,
                    company_slug=company_slug,
                )
                st.success("Company context salvo com sucesso.")
                st.rerun()
            except json.JSONDecodeError as exc:
                st.error(f"JSON inválido: {exc}")
            except AdminApiClientError as exc:
                st.error(str(exc))
            except Exception as exc:
                st.error(f"Erro inesperado ao salvar company context: {exc}")