
from pathlib import Path
import os
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st


from app.ui.admin_api_client import AdminApiClient
from app.ui.pages.calendar_page import render_calendar_page
from app.ui.pages.companies_page import render_companies_page
from app.ui.pages.company_context_page import render_company_context_page
from app.ui.pages.contacts_page import render_contacts_page
from app.ui.pages.finetuning_page import render_finetuning_page
from app.ui.pages.knowledge_page import render_knowledge_page
from app.ui.pages.message_examples_page import render_message_examples_page
from app.ui.pages.outbound_page import render_outbound_page
from app.ui.pages.pending_questions_page import render_pending_questions_page
from app.ui.pages.system_page import render_system_page



st.set_page_config(
    page_title="Project WAPP Admin",
    page_icon="💼",
    layout="wide",
)

st.title("Project WAPP Admin")
st.caption("Painel administrativo para operação multiempresa, readiness e onboarding.")


with st.sidebar:
    st.header("Configuração")
    from app.core.config import settings

    default_api_base_url = settings.admin_app_api_base_url
    api_base_url = st.text_input("Base URL da API", value=default_api_base_url).strip()
    default_admin_actor = os.getenv("ADMIN_ACTOR", "streamlit-admin")
    default_admin_token = os.getenv("ADMIN_API_TOKEN", "")
    admin_actor = st.text_input("Admin actor", value=default_admin_actor).strip()
    admin_token = st.text_input("Admin token", value=default_admin_token, type="password").strip()
    client = AdminApiClient(
        base_url=api_base_url,
        admin_token=admin_token or None,
        admin_actor=admin_actor or None,
    )

    st.divider()
    st.subheader("Empresa")

    company_options: list[tuple[str, str | None, int | None]] = [("Global / padrão", None, None)]
    companies_error: str | None = None
    try:
        companies = client.get_companies()
        for company in companies:
            label = f"{company['name']} ({company['slug']})"
            company_options.append((label, company.get("slug"), company.get("id")))
    except Exception as exc:
        companies_error = str(exc)

    selected_label = st.selectbox(
        "Escopo da empresa",
        options=[label for label, _, _ in company_options],
        index=0,
    )

    selected_company_slug = None
    selected_company_id = None
    for label, slug, company_id in company_options:
        if label == selected_label:
            selected_company_slug = slug
            selected_company_id = company_id
            break

    if companies_error:
        st.error(f"Não foi possível carregar empresas: {companies_error}")

    st.divider()
    selected_page = st.radio(
        "Navegação",
        options=[
            "System Readiness",
            "Empresas",
            "Contacts",
            "Calendar",
            "Company Context",
            "Knowledge Base",
            "Message Examples",
            "Fine-tuning",
            "Pending Questions",
            "Outbound Messages",
        ],
    )

page_context = {
    "client": client,
    "company_slug": selected_company_slug,
    "company_id": selected_company_id,
}

if selected_page == "System Readiness":
    render_system_page(page_context)
elif selected_page == "Empresas":
    render_companies_page(page_context)
elif selected_page == "Contacts":
    render_contacts_page(page_context)
elif selected_page == "Calendar":
    render_calendar_page(page_context)
elif selected_page == "Company Context":
    render_company_context_page(page_context)
elif selected_page == "Knowledge Base":
    render_knowledge_page(page_context)
elif selected_page == "Message Examples":
    render_message_examples_page(page_context)
elif selected_page == "Fine-tuning":
    render_finetuning_page(page_context)
elif selected_page == "Pending Questions":
    render_pending_questions_page(page_context)
elif selected_page == "Outbound Messages":
    render_outbound_page(page_context)
