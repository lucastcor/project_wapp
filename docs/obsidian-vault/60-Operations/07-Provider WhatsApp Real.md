---
title: Provider WhatsApp Real
type: operations
tags: [operação, whatsapp, meta, provider]
updated: 2026-04-28
---

# Provider WhatsApp Real (Cloud API)

Implementação em `app/services/whatsapp_provider.py`. Documentação Meta: [WhatsApp Cloud API](https://developers.facebook.com/docs/whatsapp/cloud-api).

## Setup no Meta

1. Criar app no Meta Developer Portal.
2. Adicionar produto "WhatsApp".
3. Pegar:
   - **Phone Number ID** → `WHATSAPP_PHONE_NUMBER_ID`.
   - **Access Token** (System User token, longo prazo) → `WHATSAPP_API_TOKEN`.
   - **App Secret** (em Settings > Basic) → `WHATSAPP_APP_SECRET`.
4. Configurar webhook:
   - Callback URL: `https://SEU-DOMINIO/webhooks/whatsapp`.
   - Verify Token: igual a `WHATSAPP_WEBHOOK_VERIFY_TOKEN`.
   - Subscribe a "messages".

## Setup no Project WAPP

```env
WHATSAPP_API_BASE_URL=https://graph.facebook.com/v22.0
WHATSAPP_API_TOKEN=EAA...
WHATSAPP_PHONE_NUMBER_ID=123456789012345
WHATSAPP_WEBHOOK_VERIFY_TOKEN=<token longo aleatório>
WHATSAPP_APP_SECRET=<app secret real>
OUTBOUND_PROVIDER=whatsapp
OUTBOUND_DEFAULT_CHANNEL=whatsapp
```

## Validação

1. **Handshake**: ao salvar webhook no Meta, ele faz `GET /webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=...&hub.challenge=...`.
2. **Mensagem teste**: enviar do seu WhatsApp para o número configurado. Esperar:
   - `inbound_message_receipts` com `status=completed`.
   - Resposta automática chegando como `outbound_messages` `status=sent`.
3. **HMAC**: olhar logs por `api.whatsapp.webhook.received payload_mode=meta` (válido).

## Outbound real

Implementação `WhatsAppProvider.send_message`:

```python
POST {base_url}/{phone_number_id}/messages
Authorization: Bearer {token}
Content-Type: application/json
{
  "messaging_product":"whatsapp",
  "to":"<contact_id>",
  "type":"text",
  "text": {"body":"<message>"}
}
```

Timeout 20s. Erros mapeados para `WhatsAppProviderError`.

## Observabilidade

- `outbound_messages.provider_response` armazena resposta crua do Meta.
- `outbound_messages.provider_message_id` armazena `wamid.*`.
- `inbound_message_receipts` para idempotência.
- Logs `api.whatsapp.webhook.*` e `api.whatsapp.auto_reply.dispatch_failed`.

## Limites Meta

- Tier inicial: **1 000 conversas únicas / 24h**.
- Após verificação de qualidade: 10k, 100k, ilimitado.
- Templates necessários para iniciar conversa fora da janela 24h (não implementado ainda no projeto — só responde dentro da janela).

## Hardening pendente

- **Retry com backoff exponencial** em falhas transientes.
- **Métricas dedicadas** (latência, error rate por código Meta).
- **Suporte a templates** para outbound proativo.
- **Suporte a media** (imagens, áudios).
- **Webhook signing rotation**.

Tracking em [[80-Improvements/01-Limites Conhecidos]] e [[80-Improvements/02-Issues Identificados]].

## Erros comuns

| Sintoma | Causa | Resolver |
|---------|-------|---------|
| 403 no webhook | HMAC inválido | Conferir `WHATSAPP_APP_SECRET` igual ao app no Meta. |
| 503 no webhook | Sem `WHATSAPP_APP_SECRET` | Setar env. |
| Outbound falha 401 | Token expirado | Gerar System User Token novo. |
| Outbound falha 131xxx | Rate / template | Conferir limite e necessidade de template. |
| Webhook não chega | Domínio sem HTTPS / token errado no Meta | Reconfigurar callback. |

## Próximas leituras

- [[30-API/03-Webhook WhatsApp]]
- [[50-Security/03-Webhook HMAC]]
- [[60-Operations/06-Runbook Produção]]
