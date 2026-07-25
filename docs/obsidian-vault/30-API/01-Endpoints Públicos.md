---
title: Endpoints Públicos
type: api
tags: [api, público, endpoints]
updated: 2026-04-28
---

# Endpoints Públicos

Sem autenticação obrigatória. Todos sob `app/api/routes.py` (e `app/api/platform_routes.py` para `/platform/*`).

## Health

### `GET /health`
- Retorna `{status:"ok"}`.
- Liveness probe.

### `GET /health/ready`
- Probe de banco + readiness blockers.
- 200 se ok; 503 se degradado.
- Resposta:
```json
{
  "status": "ok",
  "database_ok": true,
  "overall_status": "ready",
  "pilot_ready": true,
  "scale_ready": false,
  "blocker_count": 0,
  "blockers": []
}
```

## Conversação

### `POST /messages/incoming`

Entrada genérica.

Query params:
- `company_id` (int, opcional)
- `company_slug` (string, opcional)

Headers:
- `X-Company-Id`, `X-Company-Slug` (opcionais)
- `X-Request-ID` (opcional, devolvido)

Body:
```json
{
  "contact_id": "5511999999999",
  "contact_name": "Cliente Teste",
  "message": "Quero entender o serviço."
}
```

Response (`IncomingMessageResponse`):
```json
{
  "contact_id": "5511999999999",
  "company_id": 1,
  "company_slug": "empresa-teste",
  "classification": "lead_novo",
  "classification_reason": "Interesse comercial claro.",
  "classification_confidence": 0.91,
  "reply": "...",
  "source": "openai",
  "auto_reply": true,
  "action": "auto_reply",
  "priority": "high",
  "pipeline_stage": "qualificando",
  "next_action": "...",
  "lead_temperature": "warm",
  "handoff_required": false,
  "pending_question_id": null,
  "question_type": null,
  "customer_followup_question": null
}
```

Erros:
- 400 — `message` em branco.
- 422 — payload inválido.
- 429 — rate limit (se ativo).

## Webhook WhatsApp

Detalhe completo em [[30-API/03-Webhook WhatsApp]].

### `GET /webhooks/whatsapp`
Handshake do Meta. Query: `hub.mode=subscribe`, `hub.verify_token`, `hub.challenge`.

### `POST /webhooks/whatsapp`
Recepção real. Aceita payload normalizado interno **ou** payload bruto Meta.

## Plataforma

### `GET /platform`
Shell HTML da SPA.

### `POST /platform/auth/session`
Body: `{admin_token, admin_actor}`. Emite cookie HTTP-only.

### `GET /platform/auth/session`
Inspeciona sessão atual.

### `DELETE /platform/auth/session`
Logout.

Detalhe em [[30-API/04-Auth e Sessão Web]].

## Headers cross-cutting

| Header | Comportamento |
|--------|---------------|
| `X-Request-ID` | Aceito na request; sempre devolvido. Auto-gerado se ausente. |
| `X-RateLimit-Scope`, `-Limit`, `-Remaining`, `-Reset` | Quando rate limiting ativo. |
| `Retry-After` | Quando 429. |

## Próximas leituras

- [[30-API/02-Endpoints Admin]]
- [[10-Architecture/03-Fluxo Mensagem Recebida]]
