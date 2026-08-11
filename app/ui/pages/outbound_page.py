

from typing import Any

import streamlit as st

from app.ui.admin_api_client import AdminApiClientError


def render_outbound_page(page_context: dict[str, Any]) -> None:
    client = page_context["client"]
    selected_company_id = page_context.get("company_id")

    st.header("Outbound Messages")
    st.caption("Visualização e disparo das mensagens outbound pendentes.")

    channel = st.selectbox(
        "Canal de dispatch",
        options=["whatsapp", "generic", "instagram"],
        index=0,
    )

    col_actions, col_info = st.columns([0.8, 1.2])

    with col_actions:
        if st.button("Disparar todas as pendentes", use_container_width=True):
            try:
                results = client.dispatch_pending_outbound_messages(channel=channel)
                st.success(f"Dispatch concluído. Total processado: {len(results)}")
                st.rerun()
            except AdminApiClientError as exc:
                st.error(str(exc))
            except Exception as exc:
                st.error(f"Erro inesperado ao disparar pendentes: {exc}")

    with col_info:
        if selected_company_id is not None:
            st.info(f"Exibindo outbound messages da empresa {selected_company_id}.")
        else:
            st.info("Exibindo outbound messages de todos os escopos disponíveis.")

    try:
        outbound_messages = client.get_outbound_messages()
    except AdminApiClientError as exc:
        st.error(str(exc))
        return
    except Exception as exc:
        st.error(f"Erro inesperado ao carregar outbound messages: {exc}")
        return

    if selected_company_id is not None:
        outbound_messages = [
            message
            for message in outbound_messages
            if message.get("company_id") == selected_company_id
        ]

    if not outbound_messages:
        st.info("Nenhuma outbound message encontrada para o escopo atual.")
        return

    for outbound_message in outbound_messages:
        outbound_message_id = outbound_message["id"]
        with st.container(border=True):
            st.markdown(f"### Outbound #{outbound_message_id}")
            st.write(f"**Contato:** {outbound_message.get('contact_id')}")
            st.write(f"**Status:** {outbound_message.get('status')}")
            st.write(f"**Origem:** {outbound_message.get('source')}")
            st.write(f"**Mensagem:** {outbound_message.get('message')}")
            st.caption(
                f"Criada em: {outbound_message.get('created_at')} | Enviada em: {outbound_message.get('sent_at') or '—'} | Company ID: {outbound_message.get('company_id') or '—'}"
            )

            if outbound_message.get("status") == "sent":
                st.success("Mensagem já enviada.")
                continue

            if st.button(
                f"Disparar outbound #{outbound_message_id}",
                key=f"dispatch_outbound_{outbound_message_id}",
                use_container_width=True,
            ):
                try:
                    result = client.dispatch_outbound_message(
                        outbound_message_id=outbound_message_id,
                        channel=channel,
                    )
                    st.success(
                        f"Outbound #{outbound_message_id} enviado com status {result.get('delivery_status')}."
                    )
                    st.rerun()
                except AdminApiClientError as exc:
                    st.error(str(exc))
                except Exception as exc:
                    st.error(f"Erro inesperado ao disparar outbound: {exc}")
