---
title: API Pública
type: module
tags: [módulo, api, público]
updated: 2026-04-28
---

# Módulo: API Pública

`app/api/routes.py` — superfície que **não** exige token administrativo.

## Endpoints

| Método | Path | Função |
|--------|------|--------|
| GET | `/health` | Healthcheck simples. Retorna `{status:"ok"}`. |
| GET | `/health/ready` | Readiness com probe de banco + readiness checks. 503 se degradado. |
| POST | `/messages/incoming` | Entrada genérica (não-Meta). Resposta = contrato `IncomingMessageResponse`. |
| GET | `/webhooks/whatsapp` | Handshake do Meta. Retorna `hub.challenge` se token confere. |
| POST | `/webhooks/whatsapp` | Webhook real do Meta com HMAC + idempotência. |
| GET | `/platform` | Shell HTML da SPA. |
| POST | `/platform/auth/session` | Login → cookie HTTP-only. |
| GET | `/platform/auth/session` | Inspeciona sessão atual. |
| DELETE | `/platform/auth/session` | Logout. |

`/platform*` está em `app/api/platform_routes.py` (separado).

Detalhe completo em [[30-API/01-Endpoints Públicos]] e [[30-API/03-Webhook WhatsApp]].

## Pydantic schemas usados

`app/api/schemas.py`:
- `IncomingMessageRequest` / `IncomingMessageResponse`
- `WhatsAppWebhookRequest` / `WhatsAppWebhookResponse`
- `PlatformSessionLoginPayload` / `PlatformSessionResponse`
- `HealthResponse`

## Resolução de tenant

`_resolve_request_company()` (`routes.py:33-53`) ordena:

1. `company_id` query param (int).
2. `X-Company-Id` header.
3. `company_slug` query param.
4. `X-Company-Slug` header.

Se nada resolver, `orchestrator.py:_process_contact_message` chama `ensure_default_company()`.

## Webhook idempotência

Tabela `inbound_message_receipts`. Estados:
- `started` → primeiro processamento.
- `completed` → sucesso.
- `failed` → erro persistido com `error_message`.

Se chega outro POST com mesmo `provider_message_id`, retorna `200` com `processed:false, reason:duplicate_provider_message_id, duplicate_status:<estado>`.

## Validação HMAC

`_validate_meta_signature(raw_body, header)`:

```python
expected = hmac.new(secret, raw_body, sha256).hexdigest()
hmac.compare_digest(expected, header.removeprefix("sha256="))
```

Erros possíveis:
- 503: `WHATSAPP_APP_SECRET` não configurado.
- 403: header ausente ou inválido.

## Auto-reply outbound

Em `_dispatch_whatsapp_auto_reply()`: se `auto_reply=true` e `reply` não vazia, cria outbound + dispatch imediato. Falha de provider não derruba a request — registra `outbound_message.status=failed`.

## Realtime broadcast

`broadcast_event("new_message", payload)` é chamado em `incoming_message`. Hoje é um broadcast simples em memória — ver [[80-Improvements/01-Limites Conhecidos]] para evolução.

## Próximas leituras

- [[20-Modules/03-API Admin]]
- [[30-API/03-Webhook WhatsApp]]
- [[10-Architecture/03-Fluxo Mensagem Recebida]]
