
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st


from app.ui.admin_api_client import AdminApiClient
from app.ui.pages.companies_page import render_companies_page
from app.ui.pages.company_context_page import render_company_context_page
from app.ui.pages.finetuning_page import render_finetuning_page
from app.ui.pages.outbound_page import render_outbound_page
from app.ui.pages.pending_questions_page import render_pending_questions_page



st.set_page_config(
    page_title="Project WAPP Admin",
    page_icon="💼",
    layout="wide",
)

st.title("Project WAPP Admin")
st.caption("Painel administrativo mínimo para operação multiempresa.")


with st.sidebar:
    st.header("Configuração")
    default_api_base_url = "http://127.0.0.1:8000"
    api_base_url = st.text_input("Base URL da API", value=default_api_base_url).strip()
    client = AdminApiClient(base_url=api_base_url)

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
            "Empresas",
            "Company Context",
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

if selected_page == "Empresas":
    render_companies_page(page_context)
elif selected_page == "Company Context":
    render_company_context_page(page_context)
elif selected_page == "Fine-tuning":
    render_finetuning_page(page_context)
elif selected_page == "Pending Questions":
    render_pending_questions_page(page_context)
elif selected_page == "Outbound Messages":
    render_outbound_page(page_context)