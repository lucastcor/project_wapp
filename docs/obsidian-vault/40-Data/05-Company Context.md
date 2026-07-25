---
title: Company Context (JSON)
type: data
tags: [dados, contexto, json]
updated: 2026-04-28
---

# Company Context (contrato JSON)

Cada empresa tem um JSON com **identidade, regras, agenda e playbooks**. Validação obrigatória em `app/core/company_context.py`.

## Localização

- Global / fallback: `app/data/company_context.json`.
- Por empresa: `app/data/company_contexts/{slug}.json` — onde `{slug}` é o slug normalizado (lowercase, hifens, sem acento).

## Chaves obrigatórias

```json
{
  "company_name": "...",
  "brand_tone": { ... },
  "enabled_modules": { ... },
  "business_identity": { ... },
  "contact_channels": { ... },
  "business_hours": { "timezone": "America/Sao_Paulo", ... },
  "response_policy": { ... },
  "commercial_policy": { ... },
  "catalog": { "items": [ ... ] },
  "customer_intake": { ... },
  "business_rules": [ "..." ],
  "llm_guidelines": { ... }
}
```

Validação em `_validate_company_context`:
- `company_name` non-empty string.
- `business_identity`, `catalog`, `enabled_modules` precisam ser objects.
- `catalog.items` precisa ser lista.
- `business_rules` precisa ser lista.

## Chaves opcionais validadas

### `scheduling_policy`

```json
{
  "scheduling_policy": {
    "default_profile_key": "novos",
    "profiles": [
      {
        "key": "novos",
        "label": "Clientes novos",
        "allowed_weekdays": ["monday","wednesday"],
        "day_windows": { "monday": "09:00-12:00" },
        "allowed_windows": "13:00-18:00",
        "slot_increment_minutes": 30,
        "default_duration_minutes": 60,
        "minimum_notice_minutes": 180,
        "service_names": ["Consultoria"],
        "customer_profiles": ["new_customer"],
        "match_keywords": ["primeira"]
      }
    ]
  }
}
```

Regras: `key` único e non-empty.

### `internal_operation`

```json
{
  "internal_operation": {
    "operator_contact_ids": ["5511999990000", "5511999990001"]
  }
}
```

Quando um desses contatos manda mensagem, o sistema entende como **comando interno** (operador agenda em nome de cliente).

### `conversation_pipelines` (canvas Flow Studio)

```json
{
  "conversation_pipelines": [
    {
      "key": "lead_inicial",
      "label": "Lead novo",
      "objective": "Qualificar interesse",
      "trigger_terms": ["preço","quanto","como funciona"],
      "success_criteria": "Solicitou orçamento",
      "handoff_when": "Pediu desconto",
      "preferred_cta": "Agendar consulta",
      "next_keys": ["proposta"],
      "canvas_position": { "x": 120, "y": 80 }
    }
  ]
}
```

Validações: `key` único, `next_keys` precisa apontar para keys existentes, `canvas_position.x|y` numéricos.

## Como editar

1. **Pela plataforma** (Flow Studio para pipelines, formulários para resto): `PUT /admin/company-context` (superadmin).
2. **Pelo Streamlit**: `app/ui/pages/company_context_page.py`.
3. **Manual**: editar JSON e validar com `get_company_context_status(company_slug=...)` antes de subir.

## Como o backend lê

`get_company_context(company_slug=...)`:
1. Resolve path com `_normalize_company_slug`.
2. Se não existe, fallback para o global.
3. Lê JSON, valida, retorna dict.

`get_company_context_status(...)` retorna o estado de validação para readiness.

## Onde o contexto é usado

- **Reply prompt**: `openai_provider.generate_business_reply` carrega catálogo, regras, identidade, guidelines.
- **Workflow conversacional**: `enabled_modules.scheduling`, `business_hours.timezone`, `internal_operation.operator_contact_ids`.
- **Calendar service**: `scheduling_policy.profiles` segmenta janelas.
- **Readiness**: `get_company_context_status` aparece em `/admin/system/readiness` e `/admin/system/onboarding`.

## Próximas leituras

- [[20-Modules/01-Core]] (linha company_context)
- [[10-Architecture/06-Fluxo Agenda]]
- [[20-Modules/04-Plataforma Frontend]] (Flow Studio)
