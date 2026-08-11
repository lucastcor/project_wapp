---
title: Plataforma Frontend (/platform)
type: module
tags: [módulo, frontend, spa, plataforma]
updated: 2026-07-26
---

# Módulo: Plataforma Frontend `/platform`

SPA dedicada servida pelo próprio FastAPI. Sem framework — HTML + CSS + JS vanilla. Substitui o Streamlit como interface principal de produto.

## Arquivos

| Arquivo | Papel |
|---------|-------|
| `app/platform/index.html` | Shell HTML; carrega CSS + JS. |
| `app/platform/assets/styles.css` | Identidade visual; canvas; timeline. |
| `app/platform/assets/app.js` | SPA: estado, fetch, render, eventos. |
| `app/api/platform_routes.py` | Serve HTML + autenticação, identidade, equipe e SSE. |
| `app/services/platform_session_service.py` | Resolve sessão opaca de usuário ou acesso legado break-glass. |
| `app/services/identity_service.py` | Usuários, senha scrypt, memberships, convites, reset e sessões revogáveis. |
| `app/api/admin_security.py` | Aceita o cookie de sessão como credencial de `/admin/*`. |
| `app/services/realtime_service.py` | Hub SSE por processo + fan-out Redis entre réplicas. |

## Como sobe

`app/main.py`:

```python
app.include_router(platform_router)
app.mount("/platform/assets", StaticFiles(directory=str(PLATFORM_ASSETS_DIR)))
```

Browser: `GET /platform` → HTML shell → carrega `app.js` → `bootstrap()` chama
`GET /platform/auth/capabilities` e `GET /platform/auth/session`. Se a sessão
existe, abre direto; senão mostra login.

## Login

```http
POST /platform/auth/session
Content-Type: application/json

{ "email": "owner@empresa.com", "password": "..." }
```

O caminho principal autentica uma conta em `saas_users`, resolve memberships por
empresa e emite um token aleatório armazenado somente como hash no banco. O
cookie é HTTP-only e as mutações exigem o token CSRF do cookie separado. Logout
revoga a sessão imediatamente.

Cookie attrs: `HttpOnly; SameSite=Lax; Path=/; Secure` (apenas se `ENVIRONMENT=production`).

Tokens administrativos continuam disponíveis dentro de “acesso administrativo
de emergência”, isolados como mecanismo break-glass/compatibilidade.

## 10 áreas

### 1. Control Tower
Leitura. Usa `/admin/observability/summary`, `/admin/system/readiness`, `/admin/system/profile`, `/admin/system/onboarding`.

### 2. Analytics
No-show, lembretes, funil de agendamento, fila e motivos de cancelamento.

### 3. Bot Studio
Simula cliente/operador no mesmo runtime através de
`POST /admin/simulator/messages` — rota autenticada por sessão de plataforma e
exigindo papel `operator`. Ajuda a validar agenda, cancel e reschedule sem
WhatsApp real.

A empresa é resolvida no servidor e precisa existir; o simulador não consegue
empurrar conversa para um tenant arbitrário. A rota registra `audit_events`
(`action=simulate_message`) com o `contact_id` mascarado.

### 4. Flow Studio
Edita playbook conversacional. Persiste em `company_context.conversation_pipelines`. Endpoints:
- `GET /admin/company-context`
- `PUT /admin/company-context`
- `GET/POST /admin/message-examples`

Cada nó: `key`, `label`, `objective`, `trigger_terms`, `next_keys`, `preferred_cta`, `handoff_when`, `success_criteria`, `canvas_position`. Validação no backend em `company_context.py`.

### 5. Agenda Console
Bookings, fila, timeline. Detalhe em [[10-Architecture/06-Fluxo Agenda]].

### 6. Contacts Desk
Busca e atualização do perfil contextual do contato dentro do tenant.

### 7. Training Ops
Import histórica + curadoria + jobs de fine-tuning. Endpoints em `training-import`, `training-dataset/*`, `finetuning/*`.

### 8. Equipe
Lista memberships, convida por email e altera papel/estado. Só `owner`/`admin`
acessam a lista; apenas `owner` promove administradores/proprietários; o backend
impede remover o último proprietário ativo. O link aparece na UI somente quando
`IDENTITY_EXPOSE_ACTION_TOKENS=true`, destinado a desenvolvimento.

### 9. Billing
Planos, consumo, limites, Checkout e Customer Portal do Stripe.

### 10. Setup Wizard
Provisionamento guiado do contexto, horários, serviços e regras de uma empresa.

## Camada HTTP no JS

`apiRequest()` no `app.js`:
- Usa `credentials: "same-origin"` → cookie de sessão vai automaticamente.
- Em mutações, lê o cookie CSRF não HTTP-only e envia `X-CSRF-Token`.
- Normaliza erro, query params, JSON.

## Tempo real (SSE)

`GET /platform/events` entrega eventos via Server-Sent Events.

### Autenticação e escopo

- **Sessão obrigatória**: sem cookie válido → `401`. Cookie adulterado ou
  expirado → `401`.
- O escopo de empresa vem de `session["company_ids"]`, resolvido no servidor.
  **Query string nunca é considerada.**
- Cada evento carrega `company_id` no envelope. A entrega só acontece se a
  assinatura conectada está autorizada para aquela empresa.
- Sessões de identidade recebem somente empresas com membership ativo. Sessões
  break-glass globais podem receber todas as empresas e eventos sistêmicos.
- Eventos **sem** `company_id` (sistêmicos) só vão para conexões de escopo global —
  fail closed.

### Envelope

```json
{
  "type": "new_message",
  "company_id": 3,
  "data": { "contact_id": "***9999", "classification": "lead_novo", "handoff_required": false },
  "ts": 1785000000.0
}
```

Identificadores de contato vão **mascarados** (`mask_identifier`); nenhum segredo
ou PII desnecessária trafega no payload.

Eventos publicados hoje: `new_message` (por empresa), `new_booking` (por empresa),
`reminders_dispatched` (um evento por empresa, agrupado no dispatch).

### Conexão e limpeza

- Heartbeat `: heartbeat` a cada 25s; frame inicial `event: connected`.
- O bloco `finally` do gerador remove a assinatura do hub ao desconectar.
- Fila por assinatura limitada; consumidor lento é descartado em
  vez de bloquear o broadcast.
- Redis Pub/Sub replica cada publicação para hubs de outros processos sem
  duplicar a entrega no processo de origem.
- No cliente: só conecta com sessão ativa, desconecta no logout e em `401`/`403`,
  e reconecta com backoff exponencial até 60s.

## Escopo de empresa

UI carrega `GET /admin/companies` → usuário escolhe → `app.js` injeta `company_id` ou `company_slug` em queries seguintes.

## Como debugar

| Sintoma | Onde olhar |
|---------|-----------|
| Página não abre | `app/main.py` mount + `GET /platform`. |
| Login falha | `POST /platform/auth/session` + `ENABLE_ADMIN_AUTH` + tokens + `PLATFORM_SESSION_SECRET`. |
| Tela carrega vazia | Função `loadXxxData()` em `app.js` + endpoint correspondente. |
| Mudança não aparece | Confirmar persistência no banco (tabela `company_contexts`) — ver [[40-Data/05-Company Context]]. |
| Toasts em tempo real não chegam | `GET /platform/events` (401 = sessão), e escopo `ADMIN_*_COMPANY_IDS` do papel. |

## Próximas leituras

- [[30-API/04-Auth e Sessão Web]]
- [[30-API/05-RBAC e Papéis]]
- [[50-Security/02-Auth Admin]]
- `docs/platform_frontend_guide.md` (referência oficial original)
