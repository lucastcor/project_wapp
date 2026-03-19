from typing import Any

import streamlit as st

from app.ui.admin_api_client import AdminApiClientError



def render_finetuning_page(page_context: dict[str, Any]) -> None:
    client = page_context["client"]
    selected_company_id = page_context.get("company_id")
    selected_company_slug = page_context.get("company_slug")

    st.header("Fine-tuning")
    if selected_company_id is not None:
        st.caption(
            f"Monitoramento e preparação de dataset para a empresa {selected_company_slug or selected_company_id}."
        )
    else:
        st.caption("Monitoramento e preparação de dataset no escopo global/padrão.")

    col_review, col_jobs = st.columns([1, 1])

    with col_review:
        st.subheader("Review do dataset")
        if st.button("Carregar review do dataset", use_container_width=True):
            try:
                review_payload = client.get_training_dataset_review(company_id=selected_company_id)
                st.session_state["finetuning_review_payload"] = review_payload
            except AdminApiClientError as exc:
                st.error(str(exc))
            except Exception as exc:
                st.error(f"Erro inesperado ao carregar review do dataset: {exc}")

        review_payload = st.session_state.get("finetuning_review_payload")
        if review_payload:
            dataset_text = review_payload.get("dataset")
            st.text_area(
                "Review do dataset",
                value=str(dataset_text),
                height=300,
            )

    with col_jobs:
        st.subheader("Jobs de fine-tuning")
        if st.button("Atualizar lista de jobs", use_container_width=True):
            try:
                jobs = client.get_finetuning_jobs(limit=20)
                st.session_state["finetuning_jobs_payload"] = jobs
            except AdminApiClientError as exc:
                st.error(str(exc))
            except Exception as exc:
                st.error(f"Erro inesperado ao carregar jobs: {exc}")

        jobs_payload = st.session_state.get("finetuning_jobs_payload", [])
        if jobs_payload:
            for job in jobs_payload:
                with st.container(border=True):
                    st.write(f"**Job ID:** {job.get('job_id')}")
                    st.write(f"**Status:** {job.get('status')}")
                    st.write(f"**Base model:** {job.get('base_model')}")
                    st.write(f"**Fine-tuned model:** {job.get('fine_tuned_model') or '—'}")
                    st.caption(
                        f"Criado em: {job.get('created_at')} | Finalizado em: {job.get('finished_at') or '—'}"
                    )
        else:
            st.info("Nenhum job carregado ainda.")

    st.divider()
    st.subheader("Ações")

    col_create_job, col_train_jsonl, col_validation_jsonl = st.columns([1, 1, 1])

    with col_create_job:
        if st.button("Criar novo job", use_container_width=True):
            try:
                result = client.create_finetuning_job()
                st.success(f"Job criado com sucesso: {result.get('job_id')}")
                st.json(result)
            except AdminApiClientError as exc:
                st.error(str(exc))
            except Exception as exc:
                st.error(f"Erro inesperado ao criar job: {exc}")

    with col_train_jsonl:
        if st.button("Ver train JSONL", use_container_width=True):
            try:
                train_payload = client.get_finetuning_train_jsonl(company_id=selected_company_id)
                st.session_state["finetuning_train_payload"] = train_payload
            except AdminApiClientError as exc:
                st.error(str(exc))
            except Exception as exc:
                st.error(f"Erro inesperado ao carregar train JSONL: {exc}")

    with col_validation_jsonl:
        if st.button("Ver validation JSONL", use_container_width=True):
            try:
                validation_payload = client.get_finetuning_validation_jsonl(company_id=selected_company_id)
                st.session_state["finetuning_validation_payload"] = validation_payload
            except AdminApiClientError as exc:
                st.error(str(exc))
            except Exception as exc:
                st.error(f"Erro inesperado ao carregar validation JSONL: {exc}")

    train_payload = st.session_state.get("finetuning_train_payload")
    if train_payload:
        st.text_area(
            "Train JSONL",
            value=str(train_payload.get("dataset")),
            height=220,
        )

    validation_payload = st.session_state.get("finetuning_validation_payload")
    if validation_payload:
        st.text_area(
            "Validation JSONL",
            value=str(validation_payload.get("dataset")),
            height=220,
        )
