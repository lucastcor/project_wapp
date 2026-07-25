---
title: Diagrama de Componentes
type: architecture
tags: [arquitetura, componentes]
updated: 2026-04-28
---

# Diagrama de Componentes

## Visão de alto nível

```mermaid
flowchart TB
  classDef ext fill:#fff3cd,stroke:#d39e00
  classDef api fill:#d1ecf1,stroke:#0c5460
  classDef core fill:#d4edda,stroke:#155724
  classDef svc fill:#fce4ec,stroke:#c2185b
  classDef store fill:#e2d4f0,stroke:#5e3c99
  classDef worker fill:#f8d7da,stroke:#721c24

  WP[WhatsApp Cloud API]:::ext
  OPENAI[OpenAI API]:::ext
  GCAL[Google Calendar API]:::ext
  CALDAV[CalDAV / iCloud]:::ext
  USER[Operador / Plataforma Web]:::ext

  subgraph FastAPI
    PUB[routes.py]:::api
    ADM[admin_routes.py]:::api
    PLT[platform_routes.py]:::api
    SEC[admin_security.py]:::api
  end

  subgraph CoreLayer
    ORQ[orchestrator.py]:::core
    CCX[company_context.py]:::core
    CFG[config.py]:::core
    LOG[logging.py]:::core
  end

  subgraph ServicesLayer
    REPS[reply_services]:::svc
    CLS[classification]:::svc
    DEC[decision]:::svc
    HND[handoff]:::svc
    KNW[knowledge]:::svc
    MEM[memory]:::svc
    PIP[pipeline]:::svc
    STY[style]:::svc
    OUT[outbound_dispatch]:::svc
    CAL[calendar]:::svc
    CWF[conversation_workflow]:::svc
    AQS[appointment_queue]:::svc
    REM[reminder]:::svc
    TRX[training_export]:::svc
    TRI[training_import]:::svc
    FIN[finetuning]:::svc
    AAS[admin_auth]:::svc
    PSS[platform_session]:::svc
    SYS[system_readiness]:::svc
    GRD[runtime_guard]:::svc
    RTL[rate_limit]:::svc
    BIL[billing]:::svc
    ANL[analytics]:::svc
    REA[realtime]:::svc
    ONB[onboarding]:::svc
    WPP[whatsapp_provider]:::svc
  end

  subgraph LLM
    OAI[openai_provider]:::svc
    MOK[mock_provider]:::svc
  end

  subgraph Storage
    REPO[(repository.py)]:::store
    DB[(Postgres/SQLite)]:::store
    CTX[(JSON contexts)]:::store
  end

  subgraph WorkerLayer
    WRK[reminder_worker.py]:::worker
  end

  WP -->|POST /webhooks/whatsapp| PUB
  USER -->|/messages/incoming| PUB
  USER -->|/admin/*| ADM
  USER -->|/platform| PLT
  ADM --> SEC
  PUB --> ORQ
  ORQ --> CWF
  ORQ --> CLS
  ORQ --> DEC
  ORQ --> REPS
  ORQ --> MEM
  ORQ --> PIP
  ORQ --> HND
  REPS --> OAI
  CLS --> OAI
  CLS --> MOK
  CWF --> CAL
  CWF --> AQS
  ADM --> CAL
  ADM --> CWF
  ADM --> AQS
  ADM --> REM
  ADM --> TRX
  ADM --> TRI
  ADM --> FIN
  ADM --> OUT
  ADM --> SYS
  ADM --> ANL
  ADM --> BIL
  ADM --> ONB
  OUT --> WPP
  WPP --> WP
  CAL --> GCAL
  CAL --> CALDAV
  PLT --> PSS
  SEC --> AAS
  SEC --> PSS
  ServicesLayer --> REPO
  CoreLayer --> REPO
  REPO --> DB
  CCX --> CTX
  OAI --> OPENAI
  WRK --> REM
  WRK --> AQS
  WRK --> OUT
```

## Acoplamentos importantes

| Origem | Destino | Tipo | Observação |
|--------|---------|------|------------|
| `orchestrator.py` | `conversation_workflow_service.py` | função pura | Tenta primeiro tratar agenda/cancelamento/operador. |
| `reply_services.py` | `openai_provider.py` | classe | Tem fallback determinístico se faltar `OPENAI_API_KEY`. |
| `admin_routes.py` | `repository.py` | direto | Centraliza acesso a 20+ recursos administrativos. |
| `platform_routes.py` | `platform_session_service.py` | função | Emite/lê cookie HTTP-only assinado. |
| `admin_security.py` | `admin_auth_service.py` + `platform_session_service.py` | função | Aceita token via header **ou** cookie de sessão. |
| `reminder_worker.py` | `reminder_service.py` + `appointment_queue_service.py` | função | Loop com `REMINDER_WORKER_POLL_SECONDS`. |
| `outbound_dispatch_service.py` | `whatsapp_provider.py` | classe | Por canal `whatsapp`, fallback genérico. |

## Pontos de extensão

- **Novo provider LLM**: implementar interface compatível em `app/llm/providers/` e injetar via `CLASSIFICATION_PROVIDER` ou refatorar `reply_services.py`.
- **Novo provider de canal**: criar service equivalente a `whatsapp_provider.py` e plugar em `outbound_dispatch_service.py` (Instagram é o próximo declarado).
- **Novo provider de calendário**: branch em `calendar_service.py` (atualmente `mock`, `google_calendar`, `caldav`).
- **Novo papel admin**: estender `ADMIN_ROLE_HIERARCHY` em `admin_auth_service.py` e ajustar `build_require_admin_role`.

## Próximas leituras

- [[10-Architecture/01-Arquitetura em Camadas]]
- [[20-Modules/05-Services]]
- [[20-Modules/06-LLM Providers]]
