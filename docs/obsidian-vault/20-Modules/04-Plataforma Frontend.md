---
title: Plataforma Frontend (/platform)
type: module
tags: [módulo, frontend, spa, plataforma]
updated: 2026-04-28
---

# Módulo: Plataforma Frontend `/platform`

SPA dedicada servida pelo próprio FastAPI. Sem framework — HTML + CSS + JS vanilla. Substitui o Streamlit como interface principal de produto.

## Arquivos

| Arquivo | Papel |
|---------|-------|
| `app/platform/index.html` | Shell HTML; carrega CSS + JS. |
| `app/platform/assets/styles.css` | Identidade visual; canvas; timeline. |
| `app/platform/assets/app.js` | SPA: estado, fetch, render, eventos. |
| `app/api/platform_routes.py` | Serve HTML + login/logout/sessão. |
| `app/services/platform_session_service.py` | Emite/valida cookie HTTP-only assinado. |
| `app/api/admin_security.py` | Aceita o cookie de sessão como credencial de `/admin/*`. |

## Como sobe

`app/main.py`:

```python
app.include_router(platform_router)
app.mount("/platform/assets", StaticFiles(directory=str(PLATFORM_ASSETS_DIR)))
```

Browser: `GET /platform` → HTML shell → carrega `app.js` → `bootstrap()` chama `GET /platform/auth/session`. Se sessão existe, abre direto; senão mostra login.

## Login

```http
POST /platform/auth/session
Content-Type: application/json

{ "admin_token": "...", "admin_actor": "platform-admin" }
```

`platform_session_service.authenticate_platform_login`:
- Se `ENABLE_ADMIN_AUTH=false`, emite sessão `superadmin` para dev local.
- Senão, resolve papel via `resolve_admin_role_from_token`.
- Emite cookie HMAC-SHA256 (TTL `PLATFORM_SESSION_TTL_SECONDS`, default 28 800s = 8h).

Cookie attrs: `HttpOnly; SameSite=Lax; Path=/; Secure` (apenas se `ENVIRONMENT=production`).

## 5 áreas

### 1. Control Tower
Leitura. Usa `/admin/observability/summary`, `/admin/system/readiness`, `/admin/system/profile`, `/admin/system/onboarding`.

### 2. Bot Studio
Simula cliente/operador chamando `POST /messages/incoming` no mesmo runtime. Ajuda a validar agenda, cancel, reschedule sem WhatsApp real.

### 3. Flow Studio
Edita playbook conversacional. Persiste em `company_context.conversation_pipelines`. Endpoints:
- `GET /admin/company-context`
- `PUT /admin/company-context`
- `GET/POST /admin/message-examples`

Cada nó: `key`, `label`, `objective`, `trigger_terms`, `next_keys`, `preferred_cta`, `handoff_when`, `success_criteria`, `canvas_position`. Validação no backend em `company_context.py`.

### 4. Agenda Console
Bookings, fila, timeline. Detalhe em [[10-Architecture/06-Fluxo Agenda]].

### 5. Training Ops
Import histórica + curadoria + jobs de fine-tuning. Endpoints em `training-import`, `training-dataset/*`, `finetuning/*`.

## Camada HTTP no JS

`apiRequest()` no `app.js`:
- Usa `credentials: "same-origin"` → cookie de sessão vai automaticamente.
- Normaliza erro, query params, JSON.

## Escopo de empresa

UI carrega `GET /admin/companies` → usuário escolhe → `app.js` injeta `company_id` ou `company_slug` em queries seguintes.

## Como debugar

| Sintoma | Onde olhar |
|---------|-----------|
| Página não abre | `app/main.py` mount + `GET /platform`. |
| Login falha | `POST /platform/auth/session` + `ENABLE_ADMIN_AUTH` + tokens + `PLATFORM_SESSION_SECRET`. |
| Tela carrega vazia | Função `loadXxxData()` em `app.js` + endpoint correspondente. |
| Mudança não aparece | Confirmar persistência: JSON (`company_context`) ou banco. |

## Próximas leituras

- [[30-API/04-Auth e Sessão Web]]
- [[50-Security/02-Auth Admin]]
- `docs/platform_frontend_guide.md` (referência oficial original)
