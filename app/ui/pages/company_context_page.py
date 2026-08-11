import json
from typing import Any

import streamlit as st

from app.ui.admin_api_client import AdminApiClientError

DAY_KEYS = [
    ("monday", "Segunda"),
    ("tuesday", "Terça"),
    ("wednesday", "Quarta"),
    ("thursday", "Quinta"),
    ("friday", "Sexta"),
    ("saturday", "Sábado"),
    ("sunday", "Domingo"),
]


def _clone_context(data: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(data, ensure_ascii=False))


def _parse_multiline(value: str) -> list[str]:
    return [item.strip() for item in value.splitlines() if item.strip()]


def _save_context(client: Any, company_slug: str | None, context_data: dict[str, Any]) -> None:
    client.update_company_context(data=context_data, company_slug=company_slug)


def _render_profiles_tab(client: Any, company_slug: str | None, context_data: dict[str, Any]) -> None:
    scheduling_policy = context_data.setdefault("scheduling_policy", {})
    profiles = scheduling_policy.setdefault("profiles", [])
    existing_profile_keys = [str(profile.get("key") or "") for profile in profiles if isinstance(profile, dict)]

    col_summary, col_editor = st.columns([0.8, 1.2])
    with col_summary:
        st.subheader("Perfis configurados")
        st.caption("Use perfis para separar agenda por tipo de cliente, serviço ou regra operacional.")
        default_profile_key = scheduling_policy.get("default_profile_key")
        st.text_input("Perfil padrão", value=str(default_profile_key or ""), disabled=True)
        if not profiles:
            st.info("Nenhum perfil cadastrado ainda.")
        else:
            for profile in profiles:
                with st.container(border=True):
                    st.write(f"**{profile.get('label') or profile.get('key')}**")
                    st.caption(f"Key: {profile.get('key')} | Enabled: {'Sim' if profile.get('enabled', True) else 'Não'}")
                    st.write(f"Customer profiles: {', '.join(profile.get('customer_profiles') or []) or '—'}")
                    st.write(f"Serviços: {', '.join(profile.get('service_names') or []) or '—'}")

    with col_editor:
        st.subheader("Editar perfil")
        selected_profile_key = st.selectbox(
            "Perfil",
            options=["__novo__", *existing_profile_keys],
            format_func=lambda item: "Novo perfil" if item == "__novo__" else item,
        )
        selected_profile: dict[str, Any] = next(
            (profile for profile in profiles if str(profile.get("key") or "") == selected_profile_key),
            {},
        ) if selected_profile_key != "__novo__" else {}

        with st.form("company_context_profile_form"):
            enabled = st.checkbox("Ativo", value=bool(selected_profile.get("enabled", True)))
            key = st.text_input("Key", value=str(selected_profile.get("key") or ""))
            label = st.text_input("Label", value=str(selected_profile.get("label") or ""))
            customer_profiles_text = st.text_area(
                "Customer profiles (1 por linha)",
                value="\n".join(selected_profile.get("customer_profiles") or []),
                height=90,
            )
            service_names_text = st.text_area(
                "Serviços (1 por linha)",
                value="\n".join(selected_profile.get("service_names") or []),
                height=90,
            )
            match_keywords_text = st.text_area(
                "Palavras-chave de match (1 por linha)",
                value="\n".join(selected_profile.get("match_keywords") or []),
                height=90,
            )
            allowed_weekdays = st.multiselect(
                "Dias permitidos",
                options=[day_key for day_key, _ in DAY_KEYS],
                default=[str(item) for item in selected_profile.get("allowed_weekdays") or []],
                format_func=lambda item: dict(DAY_KEYS).get(item, item),
            )
            day_windows: dict[str, str] = {}
            for day_key, day_label in DAY_KEYS:
                day_windows[day_key] = st.text_input(
                    f"Janela {day_label}",
                    value=str((selected_profile.get("day_windows") or {}).get(day_key) or ""),
                    placeholder="09:00-12:00;14:00-18:00",
                )

            col_numbers_1, col_numbers_2 = st.columns(2)
            with col_numbers_1:
                minimum_notice_minutes = st.number_input(
                    "Antecedência mínima (min)",
                    min_value=0,
                    value=int(selected_profile.get("minimum_notice_minutes") or 0),
                    step=15,
                )
                default_duration_minutes = st.number_input(
                    "Duração padrão (min)",
                    min_value=0,
                    value=int(selected_profile.get("default_duration_minutes") or 0),
                    step=15,
                )
            with col_numbers_2:
                slot_increment_minutes = st.number_input(
                    "Incremento de slot (min)",
                    min_value=0,
                    value=int(selected_profile.get("slot_increment_minutes") or 0),
                    step=5,
                )
                make_default = st.checkbox(
                    "Usar como perfil padrão",
                    value=bool(scheduling_policy.get("default_profile_key") == selected_profile.get("key")),
                )

            submitted = st.form_submit_button("Salvar perfil", use_container_width=True)
            if submitted:
                if not key.strip():
                    st.error("A key do perfil é obrigatória.")
                else:
                    new_profile = {
                        "enabled": enabled,
                        "key": key.strip(),
                        "label": label.strip() or key.strip(),
                        "customer_profiles": _parse_multiline(customer_profiles_text),
                        "service_names": _parse_multiline(service_names_text),
                        "match_keywords": _parse_multiline(match_keywords_text),
                        "allowed_weekdays": allowed_weekdays,
                        "day_windows": {day_key: value.strip() for day_key, value in day_windows.items() if value.strip()},
                    }
                    if minimum_notice_minutes > 0:
                        new_profile["minimum_notice_minutes"] = int(minimum_notice_minutes)
                    if default_duration_minutes > 0:
                        new_profile["default_duration_minutes"] = int(default_duration_minutes)
                    if slot_increment_minutes > 0:
                        new_profile["slot_increment_minutes"] = int(slot_increment_minutes)

                    updated_profiles = [profile for profile in profiles if str(profile.get("key") or "") != selected_profile_key]
                    updated_profiles.append(new_profile)
                    scheduling_policy["profiles"] = sorted(updated_profiles, key=lambda item: str(item.get("key") or ""))
                    if make_default:
                        scheduling_policy["default_profile_key"] = new_profile["key"]
                    elif scheduling_policy.get("default_profile_key") == selected_profile_key:
                        scheduling_policy["default_profile_key"] = ""
                    try:
                        _save_context(client, company_slug, context_data)
                        st.success("Perfil salvo com sucesso.")
                        st.rerun()
                    except AdminApiClientError as exc:
                        st.error(str(exc))
                    except Exception as exc:
                        st.error(f"Erro inesperado ao salvar perfil: {exc}")

        if selected_profile_key != "__novo__" and st.button(
            "Remover perfil selecionado", use_container_width=True
        ):
                scheduling_policy["profiles"] = [
                    profile for profile in profiles if str(profile.get("key") or "") != selected_profile_key
                ]
                if scheduling_policy.get("default_profile_key") == selected_profile_key:
                    scheduling_policy["default_profile_key"] = ""
                try:
                    _save_context(client, company_slug, context_data)
                    st.success("Perfil removido com sucesso.")
                    st.rerun()
                except AdminApiClientError as exc:
                    st.error(str(exc))
                except Exception as exc:
                    st.error(f"Erro inesperado ao remover perfil: {exc}")


def _render_internal_ops_tab(client: Any, company_slug: str | None, context_data: dict[str, Any]) -> None:
    internal_operation = context_data.setdefault("internal_operation", {})
    st.subheader("Operação interna")
    st.caption("Defina aqui quais números podem conversar com o bot para operar agenda em nome dos clientes.")

    with st.form("company_context_internal_ops_form"):
        operator_contact_ids_text = st.text_area(
            "Operator contact IDs (1 por linha)",
            value="\n".join(internal_operation.get("operator_contact_ids") or []),
            height=140,
        )
        saved = st.form_submit_button("Salvar operação interna", use_container_width=True)
        if saved:
            internal_operation["operator_contact_ids"] = _parse_multiline(operator_contact_ids_text)
            try:
                _save_context(client, company_slug, context_data)
                st.success("Operação interna atualizada com sucesso.")
                st.rerun()
            except AdminApiClientError as exc:
                st.error(str(exc))
            except Exception as exc:
                st.error(f"Erro inesperado ao salvar operação interna: {exc}")


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

    structured_context = _clone_context(context_data)
    raw_context = _clone_context(context_data)
    tabs = st.tabs(["Perfis de Agenda", "Operação Interna", "JSON Bruto"])

    with tabs[0]:
        _render_profiles_tab(client, company_slug, structured_context)

    with tabs[1]:
        _render_internal_ops_tab(client, company_slug, structured_context)

    with tabs[2]:
        pretty_json = json.dumps(raw_context, ensure_ascii=False, indent=2)
        edited_json = st.text_area(
            "JSON do contexto",
            value=pretty_json,
            height=500,
        )
        col_preview, col_actions = st.columns([1.2, 0.8])
        with col_preview:
            st.subheader("Pré-visualização")
            st.json(raw_context)
        with col_actions:
            st.subheader("Ações")
            if st.button("Salvar contexto bruto", use_container_width=True):
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
