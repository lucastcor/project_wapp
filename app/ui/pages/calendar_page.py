import json
from datetime import date, datetime, timedelta
from typing import Any

import streamlit as st

from app.ui.admin_api_client import AdminApiClientError


def render_calendar_page(page_context: dict[str, Any]) -> None:
    client = page_context["client"]
    selected_company_id = page_context.get("company_id")
    selected_company_slug = page_context.get("company_slug")

    st.header("Calendar")
    st.caption("Operação de agenda, disponibilidade e integrações de calendário.")

    try:
        providers = client.get_calendar_providers()
        profile = client.get_calendar_profile(company_id=selected_company_id)
        integrations = client.get_calendar_integrations(company_id=selected_company_id)
        bookings = client.get_calendar_bookings(company_id=selected_company_id)
        reminders = client.get_customer_reminders(company_id=selected_company_id)
    except AdminApiClientError as exc:
        st.error(str(exc))
        return
    except Exception as exc:
        st.error(f"Erro inesperado ao carregar agenda: {exc}")
        return

    readiness = profile.get("provider_readiness", {})
    scheduling_profiles = profile.get("scheduling_profiles") or []
    profile_options = [""] + [str(item.get("key") or "") for item in scheduling_profiles if item.get("key")]
    st.info(
        f"Provider ativo: {profile.get('integration', {}).get('provider')} | "
        f"Fonte: {profile.get('integration', {}).get('source')} | "
        f"Ready: {'Sim' if readiness.get('ready') else 'Não'}"
    )

    tabs = st.tabs(["Resumo", "Disponibilidade", "Reservas", "Lembretes", "Integrações"])

    with tabs[0]:
        st.subheader("Perfil de agenda")
        st.json(profile)

    with tabs[1]:
        st.subheader("Consultar disponibilidade")
        with st.form("calendar_availability_form"):
            start_date = st.date_input("Data inicial", value=date.today())
            end_date = st.date_input("Data final", value=date.today() + timedelta(days=3))
            duration_minutes = st.number_input("Duração (min)", min_value=15, max_value=240, value=60, step=15)
            limit = st.number_input("Limite de slots", min_value=1, max_value=50, value=12, step=1)
            scheduling_profile_key = st.selectbox(
                "Scheduling profile",
                options=profile_options,
                format_func=lambda item: item or "Automático",
            )
            customer_profile = st.text_input("Customer profile", placeholder="new_customer, returning_customer...")
            service_name = st.text_input("Serviço")
            submitted = st.form_submit_button("Buscar slots")

            if submitted:
                try:
                    availability = client.query_calendar_availability(
                        company_id=selected_company_id,
                        start_date=start_date.isoformat(),
                        end_date=end_date.isoformat(),
                        duration_minutes=int(duration_minutes),
                        limit=int(limit),
                        scheduling_profile_key=scheduling_profile_key or None,
                        customer_profile=customer_profile.strip() or None,
                        service_name=service_name.strip() or None,
                    )
                    st.session_state["calendar_availability_payload"] = availability
                except AdminApiClientError as exc:
                    st.error(str(exc))
                except Exception as exc:
                    st.error(f"Erro inesperado ao consultar disponibilidade: {exc}")

        availability = st.session_state.get("calendar_availability_payload")
        if availability:
            slots = availability.get("slots", [])
            if not slots:
                st.warning("Nenhum slot disponível encontrado.")
            for slot in slots:
                with st.container(border=True):
                    st.write(f"**{slot.get('starts_at')}** até {slot.get('ends_at')}")
                    st.caption(
                        f"Provider: {slot.get('provider')} | Duração: {slot.get('duration_minutes')} min | TZ: {slot.get('timezone')}"
                    )

    with tabs[2]:
        st.subheader("Criar reserva")
        default_start = datetime.now().replace(second=0, microsecond=0) + timedelta(days=1)
        default_end = default_start + timedelta(hours=1)
        with st.form("calendar_booking_form"):
            contact_id = st.text_input("Contact ID")
            title = st.text_input("Título", value="Atendimento comercial")
            starts_at = st.text_input("Início ISO8601", value=default_start.isoformat())
            ends_at = st.text_input("Fim ISO8601", value=default_end.isoformat())
            timezone = st.text_input("Timezone", value="America/Sao_Paulo")
            scheduling_profile_key_booking = st.selectbox(
                "Scheduling profile da reserva",
                options=profile_options,
                format_func=lambda item: item or "Automático",
                key="calendar_booking_scheduling_profile",
            )
            customer_profile_booking = st.text_input(
                "Customer profile da reserva",
                key="calendar_booking_customer_profile",
            )
            description = st.text_area("Descrição", height=120)
            submitted_booking = st.form_submit_button("Criar reserva")

            if submitted_booking:
                try:
                    created = client.create_calendar_booking(
                        company_id=selected_company_id,
                        contact_id=contact_id.strip() or None,
                        title=title.strip(),
                        starts_at=starts_at.strip(),
                        ends_at=ends_at.strip(),
                        timezone=timezone.strip() or None,
                        description=description.strip() or None,
                        scheduling_profile_key=scheduling_profile_key_booking or None,
                        customer_profile=customer_profile_booking.strip() or None,
                    )
                    st.success(f"Reserva #{created['id']} criada com sucesso.")
                    st.rerun()
                except AdminApiClientError as exc:
                    st.error(str(exc))
                except Exception as exc:
                    st.error(f"Erro inesperado ao criar reserva: {exc}")

        st.subheader("Reservas existentes")
        if not bookings:
            st.info("Nenhuma reserva encontrada para o escopo atual.")
        else:
            for booking in bookings:
                with st.container(border=True):
                    st.write(f"**{booking.get('title')}**")
                    st.write(f"Início: {booking.get('starts_at')} | Fim: {booking.get('ends_at')}")
                    st.write(f"Provider: {booking.get('provider')} | Status: {booking.get('status')}")
                    st.write(
                        f"Scheduling profile: {booking.get('scheduling_profile_key') or '—'} | "
                        f"Customer profile: {booking.get('customer_profile') or '—'}"
                    )
                    st.caption(
                        f"Contato: {booking.get('contact_id') or '—'} | Company ID: {booking.get('company_id') or '—'} | TZ: {booking.get('timezone')}"
                    )
                    with (
                        st.expander("Operar reserva"),
                        st.form(f"calendar_booking_ops_{booking.get('id')}"),
                    ):
                            new_starts_at = st.text_input("Novo início ISO8601", value=str(booking.get("starts_at") or ""))
                            new_ends_at = st.text_input("Novo fim ISO8601", value=str(booking.get("ends_at") or ""))
                            reschedule_reason = st.text_input("Observação", value=str(booking.get("description") or ""))
                            col_ops_1, col_ops_2 = st.columns(2)
                            with col_ops_1:
                                submitted_reschedule = st.form_submit_button("Reagendar")
                            with col_ops_2:
                                submitted_cancel = st.form_submit_button("Cancelar")
                            if submitted_reschedule:
                                try:
                                    client.reschedule_calendar_booking(
                                        booking_id=int(booking["id"]),
                                        starts_at=new_starts_at.strip(),
                                        ends_at=new_ends_at.strip(),
                                        timezone=str(booking.get("timezone") or "America/Sao_Paulo"),
                                        description=reschedule_reason.strip() or None,
                                        scheduling_profile_key=booking.get("scheduling_profile_key"),
                                        customer_profile=booking.get("customer_profile"),
                                    )
                                    st.success("Reserva reagendada com sucesso.")
                                    st.rerun()
                                except AdminApiClientError as exc:
                                    st.error(str(exc))
                                except Exception as exc:
                                    st.error(f"Erro inesperado ao reagendar reserva: {exc}")
                            if submitted_cancel:
                                try:
                                    client.cancel_calendar_booking(
                                        booking_id=int(booking["id"]),
                                        trigger_queue_recovery=True,
                                        reason="Cancelado via painel admin",
                                    )
                                    st.success("Reserva cancelada com sucesso.")
                                    st.rerun()
                                except AdminApiClientError as exc:
                                    st.error(str(exc))
                                except Exception as exc:
                                    st.error(f"Erro inesperado ao cancelar reserva: {exc}")

    with tabs[3]:
        st.subheader("Lembretes")
        col_actions, col_list = st.columns([0.9, 1.1])

        with col_actions:
            with st.form("internal_reminder_form"):
                owner_contact_id = st.text_input("Contato do dono/funcionário")
                message_text = st.text_area(
                    "Texto natural do lembrete",
                    height=120,
                    placeholder="Me lembra amanhã às 9h de ligar para o cliente João sobre a proposta.",
                )
                reminder_timezone = st.text_input("Timezone", value="America/Sao_Paulo")
                submitted_internal = st.form_submit_button("Criar lembrete interno")
                if submitted_internal:
                    try:
                        created = client.create_internal_reminder(
                            owner_contact_id=owner_contact_id.strip(),
                            message_text=message_text.strip(),
                            company_id=selected_company_id,
                            timezone=reminder_timezone.strip() or None,
                        )
                        st.success(f"Lembrete interno #{created['id']} criado.")
                        st.rerun()
                    except AdminApiClientError as exc:
                        st.error(str(exc))
                    except Exception as exc:
                        st.error(f"Erro inesperado ao criar lembrete interno: {exc}")

            st.divider()
            audience = st.selectbox(
                "Audiência para processamento",
                options=["customer", "internal"],
                index=0,
            )
            dispatch_immediately = st.checkbox("Disparar imediatamente ao materializar", value=False)
            channel = st.selectbox("Canal do lembrete", options=["whatsapp", "generic", "instagram"], index=0)
            if st.button("Processar lembretes vencidos", use_container_width=True):
                try:
                    results = client.dispatch_due_reminders(
                        dispatch_immediately=dispatch_immediately,
                        channel=channel,
                        audience=audience,
                    )
                    st.success(f"Lembretes processados: {len(results)}")
                    st.rerun()
                except AdminApiClientError as exc:
                    st.error(str(exc))
                except Exception as exc:
                    st.error(f"Erro inesperado ao processar lembretes: {exc}")

        with col_list:
            if not reminders:
                st.info("Nenhum lembrete encontrado para o escopo atual.")
            else:
                for reminder in reminders:
                    with st.container(border=True):
                        st.write(f"**{reminder.get('reminder_text')}**")
                        st.write(
                            f"Quando: {reminder.get('remind_at')} | Status: {reminder.get('status')} | "
                            f"Audiência: {reminder.get('audience') or '—'} | Origem: {reminder.get('source') or '—'}"
                        )
                        st.caption(
                            f"Contato: {reminder.get('contact_id')} | Outbound: {reminder.get('outbound_message_id') or '—'} | "
                            f"Booking: {reminder.get('appointment_booking_id') or '—'} | TZ: {reminder.get('timezone')}"
                        )
                        if reminder.get("reminder_summary"):
                            st.caption(str(reminder["reminder_summary"]))

    with tabs[4]:
        col_form, col_list = st.columns([1, 1.3])
        with col_form:
            st.subheader("Nova integração")
            provider_options = {item["label"]: item["provider"] for item in providers}
            selected_label = st.selectbox("Provider", options=list(provider_options.keys()))
            external_calendar_id = st.text_input("External calendar ID")
            config_text = st.text_area(
                "Config JSON",
                value=json.dumps({}, ensure_ascii=False, indent=2),
                height=180,
            )
            active = st.checkbox("Ativa", value=True)
            if selected_company_id is not None:
                st.caption(f"Integração será criada para {selected_company_slug or selected_company_id}.")
            if st.button("Criar integração", use_container_width=True):
                try:
                    created = client.create_calendar_integration(
                        company_id=selected_company_id,
                        provider=provider_options[selected_label],
                        external_calendar_id=external_calendar_id.strip() or None,
                        config=json.loads(config_text),
                        active=active,
                    )
                    st.success(f"Integração #{created['id']} criada com sucesso.")
                    st.rerun()
                except json.JSONDecodeError as exc:
                    st.error(f"JSON inválido: {exc}")
                except AdminApiClientError as exc:
                    st.error(str(exc))
                except Exception as exc:
                    st.error(f"Erro inesperado ao criar integração: {exc}")

        with col_list:
            st.subheader("Integrações cadastradas")
            if not integrations:
                st.info("Nenhuma integração cadastrada para o escopo atual.")
            else:
                for integration in integrations:
                    with st.container(border=True):
                        st.write(f"**{integration.get('provider')}**")
                        st.write(
                            f"Ativa: {'Sim' if integration.get('active') else 'Não'} | "
                            f"Company ID: {integration.get('company_id') or '—'}"
                        )
                        st.write(f"External calendar ID: {integration.get('external_calendar_id') or '—'}")
                        if integration.get("config"):
                            st.json(integration["config"])
