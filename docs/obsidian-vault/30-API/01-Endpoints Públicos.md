---
title: Endpoints Públicos
type: api
tags: [api, público, endpoints]
updated: 2026-07-25
---

# Endpoints Públicos

Rotas expostas sem sessão administrativa, sob `app/api/routes.py` (e
`app/api/platform_routes.py` para `/platform/*`). **Nem todas são anônimas**:
`/messages/incoming` exige segredo de ingress interno em produção e
`GET /platform/events` exige sessão de plataforma válida.

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

#### Autenticação

| Runtime | Comportamento |
|---------|---------------|
| `ENVIRONMENT=production` **ou** `STRICT_RUNTIME_VALIDATION=true` | Exige `INTERNAL_INGRESS_SECRET`; chamada anônima → `401` |
| Demais ambientes | Chamada anônima permitida (conveniência de desenvolvimento) |

Regras válidas em qualquer ambiente:
- Credencial em `X-Internal-Ingress-Secret: <segredo>` ou `Authorization: Bearer <segredo>`.
- Comparação em tempo constante; segredo nunca é logado.
- Credencial **inválida** → `401`, mesmo fora de produção.
- Credencial apresentada sem segredo configurado → `401` (nunca aceita o que não pode verificar).
- Produção com `INTERNAL_INGRESS_SECRET` ausente/placeholder → `503` (fail closed).

Query params:
- `company_id` (int, opcional)
- `company_slug` (string, opcional)

Headers:
- `X-Company-Id`, `X-Company-Slug` (opcionais)
- `X-Internal-Ingress-Secret` (obrigatório em produção)
- `X-Request-ID` (opcional, devolvido)

#### Escopo de empresa

A empresa informada é resolvida contra a tabela `companies` **antes** do
processamento. Um `company_id`/`company_slug` que não existe retorna `404
Company not found.` — não há mais fallback silencioso para a empresa padrão,
que permitiria gravar conversa de um tenant no escopo de outro. Sem parâmetro
de empresa, o orquestrador segue usando a empresa padrão.

#### Limites de tamanho

| Campo | Limite | Violação |
|-------|--------|----------|
| `contact_id` | 64 | 422 |
| `contact_name` | 200 | 422 |
| `message` | 4096 | 422 |

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
- 401 — credencial de ingress ausente/inválida (ver acima).
- 404 — empresa informada não existe.
- 422 — payload inválido ou acima dos limites de tamanho.
- 429 — rate limit (se ativo).
- 503 — produção com ingress interno não configurado.

> O simulador da plataforma **não** usa esta rota. Ele chama
> `POST /admin/simulator/messages`, autenticada por sessão de plataforma e
> exigindo papel `operator`. Ver [[30-API/02-Endpoints Admin]].

## Webhook WhatsApp

Detalhe completo em [[30-API/03-Webhook WhatsApp]].

### `GET /webhooks/whatsapp`
Handshake do Meta. Query: `hub.mode=subscribe`, `hub.verify_token`, `hub.challenge`.

### `POST /webhooks/whatsapp`
Recepção real. Aceita payload bruto Meta (assinatura HMAC obrigatória) **ou**
payload normalizado interno (desabilitado por padrão; quando habilitado exige
`INTERNAL_INGRESS_SECRET`).

## Plataforma

### `GET /platform`
Shell HTML da SPA.

### `POST /platform/auth/session`
Body: `{admin_token, admin_actor}`. Emite cookie HTTP-only.

### `GET /platform/auth/session`
Inspeciona sessão atual.

### `DELETE /platform/auth/session`
Logout.

### `GET /platform/events`
Stream SSE de eventos em tempo real. **Exige sessão de plataforma válida**
(`401` sem cookie, com cookie adulterado ou expirado). O escopo de tenant vem
da sessão, nunca de query string. Ver [[20-Modules/04-Plataforma Frontend]].

Detalhe em [[30-API/04-Auth e Sessão Web]].

## Headers cross-cutting

| Header | Comportamento |
|--------|---------------|
| `X-Request-ID` | Aceito na request; sempre devolvido. Auto-gerado se ausente. |
| `X-RateLimit-Scope`, `-Limit`, `-Remaining`, `-Reset` | Quando rate limiting ativo. |
| `Retry-After` | Quando 429. |

## Próximas leituras

- [[30-API/02-Endpoints Admin]]
- [[30-API/03-Webhook WhatsApp]]
- [[10-Architecture/03-Fluxo Mensagem Recebida]]
- [[50-Security/05-Secrets e Ambiente]]
