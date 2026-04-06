from typing import Any

import streamlit as st

from app.ui.admin_api_client import AdminApiClientError


def render_message_examples_page(page_context: dict[str, Any]) -> None:
    client = page_context["client"]
    selected_company_id = page_context.get("company_id")
    selected_company_slug = page_context.get("company_slug")

    st.header("Message Examples")
    st.caption("Curadoria de exemplos aprovados para estilo de resposta e treino.")

    col_form, col_list = st.columns([1, 1.4])

    with col_form:
        st.subheader("Cadastrar exemplo")
        if selected_company_id is not None:
            st.info(f"Novo exemplo será associado à empresa {selected_company_slug or selected_company_id}.")

        with st.form("create_message_example_form"):
            profile_name = st.text_input("Perfil de atendimento")
            scenario_type = st.text_input("Tipo de cenário")
            customer_message = st.text_area("Mensagem do cliente", height=140)
            approved_reply = st.text_area("Resposta aprovada", height=140)
            style_notes = st.text_area("Observações de estilo", height=100)
            quality_score = st.slider("Quality score", min_value=0.0, max_value=1.0, value=0.8, step=0.05)
            used_for_training = st.checkbox("Usar para treinamento", value=True)
            active = st.checkbox("Ativo", value=True)
            submitted = st.form_submit_button("Salvar exemplo")

            if submitted:
                try:
                    created = client.create_message_example(
                        company_id=selected_company_id,
                        profile_name=profile_name.strip(),
                        scenario_type=scenario_type.strip(),
                        customer_message=customer_message.strip(),
                        approved_reply=approved_reply.strip(),
                        style_notes=style_notes.strip() or None,
                        quality_score=quality_score,
                        used_for_training=used_for_training,
                        active=active,
                    )
                    st.success(f"Exemplo #{created['id']} criado com sucesso.")
                    st.rerun()
                except AdminApiClientError as exc:
                    st.error(str(exc))
                except Exception as exc:
                    st.error(f"Erro inesperado ao criar example: {exc}")

    with col_list:
        st.subheader("Exemplos cadastrados")
        try:
            examples = client.get_message_examples(company_id=selected_company_id)
        except AdminApiClientError as exc:
            st.error(str(exc))
            return
        except Exception as exc:
            st.error(f"Erro inesperado ao carregar examples: {exc}")
            return

        if not examples:
            st.info("Nenhum message example encontrado para o escopo atual.")
            return

        for example in examples:
            with st.container(border=True):
                st.write(f"**{example.get('profile_name')}** | {example.get('scenario_type')}")
                st.write(f"Quality score: {example.get('quality_score')}")
                st.write(f"Treino: {'Sim' if example.get('used_for_training') else 'Não'} | Ativo: {'Sim' if example.get('active') else 'Não'}")
                st.write(f"Mensagem: {example.get('customer_message')}")
                st.write(f"Resposta aprovada: {example.get('approved_reply')}")
                if example.get("style_notes"):
                    st.caption(str(example["style_notes"]))
                st.caption(
                    f"Company ID: {example.get('company_id') or '—'} | Criado em: {example.get('created_at')}"
                )
