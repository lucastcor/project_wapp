from typing import Any

import streamlit as st

from app.ui.admin_api_client import AdminApiClientError


def _render_checklist(items: list[dict[str, Any]], empty_message: str) -> None:
    if not items:
        st.info(empty_message)
        return

    for item in items:
        with st.container(border=True):
            st.write(f"**{item.get('title')}**")
            st.write(f"Status: {item.get('status')} | Severidade: {item.get('severity')}")
            st.write(str(item.get("detail") or ""))
            if item.get("recommendation"):
                st.caption(f"Próxima ação: {item['recommendation']}")


def render_system_page(page_context: dict[str, Any]) -> None:
    client = page_context["client"]
    selected_company_id = page_context.get("company_id")
    selected_company_slug = page_context.get("company_slug")

    st.header("System Readiness")
    st.caption("Visão operacional para readiness, onboarding, auditoria e migrações.")

    try:
        system_profile = client.get_system_profile()
        readiness = client.get_system_readiness()
        onboarding = client.get_onboarding_status(
            company_id=selected_company_id,
            company_slug=selected_company_slug,
        )
        migrations = client.get_schema_migrations()
        audit_events = client.get_audit_events(limit=25)
        observability = client.get_observability_summary()
    except AdminApiClientError as exc:
        st.error(str(exc))
        return
    except Exception as exc:
        st.error(f"Erro inesperado ao carregar visão sistêmica: {exc}")
        return

    col_a, col_b, col_c = st.columns(3)
    col_a.metric("Readiness geral", readiness.get("overall_status", "unknown").upper())
    col_b.metric("Pilot ready", "Sim" if readiness.get("pilot_ready") else "Não")
    col_c.metric("Scale ready", "Sim" if readiness.get("scale_ready") else "Não")

    tabs = st.tabs(
        [
            "Resumo",
            "Readiness",
            "Onboarding",
            "Auditoria",
            "Migrações",
        ]
    )

    with tabs[0]:
        st.subheader("Perfil de implantação")
        app_profile = system_profile.get("app", {})
        database_profile = system_profile.get("database", {})
        security_profile = system_profile.get("security", {})
        ai_profile = system_profile.get("ai", {})

        st.json(
            {
                "app": app_profile,
                "database": database_profile,
                "security": security_profile,
                "ai": ai_profile,
                "observability_totals": observability.get("totals", {}),
            }
        )

    with tabs[1]:
        st.subheader("Checks de readiness")
        _render_checklist(readiness.get("checks", []), "Nenhum check encontrado.")

    with tabs[2]:
        st.subheader("Checklist de onboarding")
        scope = onboarding.get("scope", {})
        st.write(
            f"Escopo: {scope.get('mode')} | Empresa: "
            f"{scope.get('company_name') or scope.get('company_slug') or 'global'}"
        )
        st.json(
            {
                "overall_status": onboarding.get("overall_status"),
                "operational_snapshot": onboarding.get("operational_snapshot"),
                "finetuning_readiness": onboarding.get("finetuning_readiness"),
                "context_status": onboarding.get("context_status"),
            }
        )
        _render_checklist(onboarding.get("checklist", []), "Nenhum item de onboarding encontrado.")

    with tabs[3]:
        st.subheader("Últimos eventos de auditoria")
        if not audit_events:
            st.info("Nenhum evento de auditoria encontrado.")
        else:
            for event in audit_events:
                with st.container(border=True):
                    st.write(
                        f"**{event.get('action')}** em `{event.get('resource_type')}` "
                        f"por `{event.get('actor_id')}`"
                    )
                    st.write(
                        f"Status: {event.get('status')} | Recurso: {event.get('resource_id') or '—'} "
                        f"| Company ID: {event.get('company_id') or '—'}"
                    )
                    st.caption(
                        f"Request: {event.get('request_id') or '—'} | Criado em: {event.get('created_at')}"
                    )
                    if event.get("metadata"):
                        st.json(event["metadata"])

    with tabs[4]:
        st.subheader("Migrações aplicadas")
        if not migrations:
            st.info("Nenhuma migração registrada.")
        else:
            for migration in migrations:
                with st.container(border=True):
                    st.write(f"**{migration.get('version')}**")
                    st.write(migration.get("name"))
                    st.caption(f"Aplicada em: {migration.get('applied_at')}")
