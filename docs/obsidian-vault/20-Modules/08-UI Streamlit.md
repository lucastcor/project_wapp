---
title: UI Streamlit (legado / apoio)
type: module
tags: [módulo, ui, streamlit, legado]
updated: 2026-04-28
---

# Módulo: UI Streamlit

`app/admin_app.py` + `app/ui/`.

Status: **mantido como apoio/contingência**. A interface principal de produto agora é `/platform` (ver [[20-Modules/04-Plataforma Frontend]]).

## Arquivos

| Arquivo | Papel |
|---------|-------|
| `app/admin_app.py` | Entry point Streamlit. |
| `app/ui/admin_api_client.py` | Cliente HTTP para falar com `/admin/*`. |
| `app/ui/pages/calendar_page.py` | Agenda, integrações, fila. |
| `app/ui/pages/companies_page.py` | Listagem/cadastro de empresas. |
| `app/ui/pages/company_context_page.py` | Edição estruturada do `company_context`. |
| `app/ui/pages/contacts_page.py` | Busca e ajuste de contatos. |
| `app/ui/pages/finetuning_page.py` | Dataset, importação, jobs. |
| `app/ui/pages/knowledge_page.py` | Knowledge base read. |
| `app/ui/pages/message_examples_page.py` | Curadoria. |
| `app/ui/pages/outbound_page.py` | Inspeção/dispatch. |
| `app/ui/pages/pending_questions_page.py` | Resolver handoffs. |
| `app/ui/pages/system_page.py` | Readiness, profile, auditoria. |

## Como subir

Local:

```bash
streamlit run app/admin_app.py
```

Docker compose: serviço `admin` em `compose.yaml:163-187`. Sobe Streamlit em `0.0.0.0:8501`.

## Como autentica

Passa `ADMIN_API_TOKEN` (via `ADMIN_APP_API_BASE_URL`) e `X-Admin-Actor` (default `streamlit-admin`). Sem RBAC granular — usa o token do superadmin.

## Quando usar

| Situação | Ferramenta |
|----------|-----------|
| Operação primária | `/platform` |
| Acessar coisas que ainda não estão na nova UI | Streamlit |
| Ambiente local sem frontend assets | Streamlit |
| Fallback durante incidente do `/platform` | Streamlit |

## Limitações

- Sem login refinado por papel.
- Re-render de página completa a cada interação (Streamlit pattern).
- Não suporta canvas visual de Flow Studio.

## Próximas leituras

- [[20-Modules/04-Plataforma Frontend]]
- [[60-Operations/01-Deploy Docker Compose]]
