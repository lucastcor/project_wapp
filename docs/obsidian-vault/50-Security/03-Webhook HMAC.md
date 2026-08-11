---
title: Webhook HMAC
type: security
tags: [segurança, webhook, hmac, whatsapp]
updated: 2026-07-25
---

# Validação HMAC do Webhook WhatsApp

## Por que

Meta envia eventos para `POST /webhooks/whatsapp`. Sem validação, qualquer um poderia forjar mensagens. A Meta provê `X-Hub-Signature-256` justamente pra que o servidor valide a origem.

## Implementação

`app/api/routes.py:_validate_meta_signature`:

```python
def _validate_meta_signature(raw_body, header):
    secret = settings.whatsapp_app_secret.strip()
    if not secret:
        raise HTTPException(503)
    if not header.startswith("sha256="):
        raise HTTPException(403)
    expected = hmac.new(secret.encode(), raw_body, sha256).hexdigest()
    received = header.removeprefix("sha256=").strip()
    if not hmac.compare_digest(expected, received):
        raise HTTPException(403)
```

Pontos importantes:
- Usa `request.body()` **bruto** (antes de `request.json()`) — qualquer reserialização quebraria a validação.
- `hmac.compare_digest` para evitar timing attack.
- 503 quando `WHATSAPP_APP_SECRET` ausente.
- 403 quando header ausente ou inválido.
- A autenticação roda **antes** de qualquer parsing pesado, persistência ou
  orquestração: o handler classifica o formato do payload, autentica o modo
  correspondente e só então normaliza e processa.
- Vale para **todo** payload com `object=whatsapp_business_account`, inclusive
  eventos de status que serão ignorados. Um POST Meta sem assinatura válida
  recebe 403 mesmo que a mensagem não fosse processada.

## Modo normalizado interno

Quando o payload chega no formato **normalizado interno** (com `event`,
`from_number`, `text` no top-level), não há assinatura Meta para validar. Por isso
esse modo tem autenticação própria:

1. **Desabilitado por padrão** (`ENABLE_NORMALIZED_WHATSAPP_INGRESS=false`) →
   `403 Normalized webhook ingress is disabled.`
2. Quando habilitado, exige `INTERNAL_INGRESS_SECRET` em
   `X-Internal-Ingress-Secret` ou `Authorization: Bearer`, comparado em tempo
   constante.
3. Habilitado **sem** segredo real (ausente ou `change-me*`) → `503`. Falha
   fechado em qualquer ambiente, e o readiness marca `normalized_ingress_security`
   como blocker.

Implementação em `app/services/ingress_auth_service.py` (política) e
`app/api/ingress_security.py` (mapeamento HTTP).

> Implicação operacional: o tráfego do Meta sempre cai no modo `meta` e exige
> assinatura. O modo normalizado é opt-in explícito para fontes confiáveis dentro
> da rede, e portador do segredo interno.

## O que nunca vai para o log

O evento `api.ingress.auth.denied` registra apenas `ingress_surface`,
`denial_reason` e `status_code`. Segredo apresentado, segredo configurado e
header `Authorization` **nunca** são logados.

## Idempotência

Independente de validação, cada `provider_message_id` cai no fluxo de `inbound_message_receipts`:
- Primeira chegada: `started`.
- Sucesso: `completed`.
- Falha: `failed` com `error_message`.
- Reentrega Meta: `200` com `processed:false reason:duplicate_provider_message_id`.

Detalhe em [[40-Data/02-Schema do Banco]] (tabela `inbound_message_receipts`).

## Handshake (verify)

`GET /webhooks/whatsapp` valida `hub.verify_token` contra `WHATSAPP_WEBHOOK_VERIFY_TOKEN`. Sem comparação timing-safe — **risco menor**, pois é só handshake único de configuração e o token não dá acesso a nada se vazar (atacante precisaria também controlar o callback URL na Meta). Tracking em [[80-Improvements/03-Melhorias de Segurança]].

## Logs gerados

| Evento | Quando |
|--------|--------|
| `api.whatsapp.webhook.received` | Recepção válida |
| `api.whatsapp.webhook.verified` | Handshake OK |
| `api.whatsapp.webhook.ignored` | Payload não-mensagem |
| `api.whatsapp.webhook.duplicate_ignored` | Reentrega |
| `api.whatsapp.webhook.completed` | Processamento OK |
| `api.whatsapp.auto_reply.dispatch_failed` | Falha do auto-reply |

## Recomendações

1. Definir `WHATSAPP_APP_SECRET` igual ao **App Secret** do app no Meta Developer Portal (não confundir com Access Token).
2. Definir `WHATSAPP_WEBHOOK_VERIFY_TOKEN` longo e aleatório.
3. Servir API atrás de HTTPS (Meta exige).
4. Monitorar `inbound_message_receipts.status=failed`.

## Próximas leituras

- [[30-API/03-Webhook WhatsApp]]
- [[60-Operations/07-Provider WhatsApp Real]]
