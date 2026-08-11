

from typing import Any

import streamlit as st

from app.ui.admin_api_client import AdminApiClientError


def render_companies_page(page_context: dict[str, Any]) -> None:
    client = page_context["client"]

    st.header("Empresas")
    st.caption("Cadastro e visualização das empresas do sistema.")

    col_form, col_list = st.columns([1, 1.4])

    with col_form:
        st.subheader("Cadastrar empresa")
        with st.form("create_company_form"):
            slug = st.text_input("Slug")
            name = st.text_input("Nome")
            business_type = st.text_input("Tipo de negócio")
            active = st.checkbox("Ativa", value=True)
            submitted = st.form_submit_button("Criar empresa")

            if submitted:
                try:
                    created = client.create_company(
                        slug=slug.strip(),
                        name=name.strip(),
                        business_type=business_type.strip() or None,
                        active=active,
                    )
                    st.success(f"Empresa criada com sucesso: {created['name']} ({created['slug']})")
                    st.rerun()
                except AdminApiClientError as exc:
                    st.error(str(exc))
                except Exception as exc:
                    st.error(f"Erro inesperado ao criar empresa: {exc}")

    with col_list:
        st.subheader("Empresas cadastradas")
        try:
            companies = client.get_companies()
        except AdminApiClientError as exc:
            st.error(str(exc))
            return
        except Exception as exc:
            st.error(f"Erro inesperado ao carregar empresas: {exc}")
            return

        if not companies:
            st.info("Nenhuma empresa cadastrada ainda.")
            return

        for company in companies:
            with st.container(border=True):
                status_text = "Ativa" if company.get("active") else "Inativa"
                st.markdown(f"### {company.get('name')}  ")
                st.write(f"**Slug:** {company.get('slug')}")
                st.write(f"**Tipo de negócio:** {company.get('business_type') or 'Não informado'}")
                st.write(f"**Status:** {status_text}")
                st.caption(
                    f"Criada em: {company.get('created_at')} | Atualizada em: {company.get('updated_at') or '—'}"
                )
