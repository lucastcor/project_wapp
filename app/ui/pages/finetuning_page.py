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
                jobs = client.get_finetuning_jobs(limit=20, company_id=selected_company_id)
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
        # O job só nasce de uma dataset_version congelada; a curadoria completa
        # vive em /platform (Data Governance).
        frozen_versions: list[dict] = []
        if selected_company_id is not None:
            try:
                frozen_versions = [
                    version
                    for version in client.get_dataset_versions(company_id=selected_company_id)
                    if version.get("status") == "frozen"
                ]
            except Exception:
                frozen_versions = []

        selected_version_id = None
        if frozen_versions:
            selected_version_id = st.selectbox(
                "Dataset congelado",
                options=[int(version["id"]) for version in frozen_versions],
                format_func=lambda version_id: f"v{version_id}",
            )
        else:
            st.caption("Nenhuma dataset_version congelada disponível.")

        if st.button("Criar novo job", use_container_width=True):
            if selected_company_id is None:
                st.error("Selecione uma empresa para criar o job de fine-tuning.")
                return
            if selected_version_id is None:
                st.error("Congele uma dataset_version antes de criar o job.")
                return
            try:
                result = client.create_finetuning_job(
                    company_id=selected_company_id,
                    dataset_version_id=int(selected_version_id),
                )
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

    st.divider()
    st.subheader("Importador de Base Histórica / CRM")
    st.caption(
        "Importe CSV ou JSON com conversas históricas para atualizar customer_profile e gerar message_examples para curadoria."
    )

    uploaded_file = st.file_uploader(
        "Arquivo CSV ou JSON",
        type=["csv", "json"],
        accept_multiple_files=False,
    )

    with st.form("training_import_form"):
        import_format = st.selectbox("Formato", options=["csv", "json"], index=0)
        delimiter = st.text_input("Delimitador CSV", value=",")
        default_profile_name = st.text_input("Profile padrão", value="default")
        default_scenario_type = st.text_input("Scenario padrão", value="general")
        default_quality_score = st.number_input(
            "Quality score padrão",
            min_value=0.0,
            max_value=10.0,
            value=7.0,
            step=0.5,
        )
        col_import_1, col_import_2 = st.columns(2)
        with col_import_1:
            dry_run = st.checkbox("Pré-visualizar sem gravar", value=True)
            update_contacts = st.checkbox("Atualizar contatos", value=True)
        with col_import_2:
            create_examples = st.checkbox("Criar message_examples", value=True)
            auto_promote = st.checkbox("Promover exemplos elegíveis para treino", value=False)

        submitted_import = st.form_submit_button("Processar importação", use_container_width=True)
        if submitted_import:
            if uploaded_file is None:
                st.error("Selecione um arquivo CSV ou JSON para importar.")
            else:
                try:
                    raw_text = uploaded_file.getvalue().decode("utf-8")
                    result = client.import_training_records(
                        fmt=import_format,
                        raw_text=raw_text,
                        delimiter=delimiter,
                        dry_run=dry_run,
                        update_contacts=update_contacts,
                        create_message_examples=create_examples,
                        auto_promote_eligible_examples=auto_promote,
                        default_profile_name=default_profile_name.strip() or "default",
                        default_scenario_type=default_scenario_type.strip() or "general",
                        default_quality_score=float(default_quality_score),
                        company_id=selected_company_id,
                    )
                    st.session_state["training_import_result"] = result
                except AdminApiClientError as exc:
                    st.error(str(exc))
                except Exception as exc:
                    st.error(f"Erro inesperado ao importar base histórica: {exc}")

    import_result = st.session_state.get("training_import_result")
    if import_result:
        st.write(
            f"Registros lidos: {import_result.get('parsed_records')} | "
            f"Contatos: {import_result.get('contacts_to_update')} | "
            f"Examples: {import_result.get('message_examples_to_create')} | "
            f"Elegíveis: {import_result.get('eligible_examples')}"
        )
        if import_result.get("errors"):
            st.warning(f"Linhas com problema: {len(import_result.get('errors') or [])}")
            st.json(import_result.get("errors"))
        if import_result.get("preview"):
            st.subheader("Preview")
            st.dataframe(import_result.get("preview"), use_container_width=True)
