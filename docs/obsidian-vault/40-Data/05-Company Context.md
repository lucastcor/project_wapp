---
title: Company Context
type: data
tags: [dados, contexto, persistência]
updated: 2026-07-25
---

# Company Context

Cada empresa tem um documento com **identidade, regras, agenda e playbooks**.
Validação obrigatória em `app/core/company_context.py`.

## Onde o contexto mora

O **store de runtime é o banco compartilhado** (tabela `company_contexts`). API e
worker leem a mesma linha, cada escrita é transacional e versionada, e o backup
lógico do banco inclui a configuração ativa.

Os arquivos JSON sob `app/data` são **material de seed**, não store de runtime:

| Caminho | Papel |
|---------|-------|
| `app/data/company_context.json` | Seed do contexto global (chave de storage `__global__`) |
| `app/data/company_contexts/{slug}.json` | Seed por empresa (`{slug}` normalizado: lowercase, hifens) |

### Ordem de resolução

`get_company_context(company_slug=...)` avalia, nesta ordem, `slug` e depois o global:

1. Linha do banco para a chave.
2. Arquivo de seed da chave — **apenas se seeds estiverem habilitados**.

Seeds são desabilitados quando `ENVIRONMENT=production` **ou**
`STRICT_RUNTIME_VALIDATION=true` (`seed_contexts_enabled()`). Ou seja: o runtime
de produção nunca cai silenciosamente em arquivo local mutável. Sem linha no
banco e sem fallback permitido, a leitura levanta `CompanyContextError` e a
readiness reporta blocker.

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

## Persistência

Tabela `company_contexts` (migrações `024`–`026`):

| Coluna | Observação |
|--------|-----------|
| `company_id` | Id interno resolvido a partir do slug; `NULL` no contexto global |
| `company_slug` | Chave de storage única (`__global__` para o contexto compartilhado) |
| `context_json` | Payload validado |
| `schema_version` | `COMPANY_CONTEXT_SCHEMA_VERSION` (hoje `1`) |
| `revision` | Contador incremental para concorrência otimista |
| `created_at` / `updated_at` | Timestamps |
| `updated_by` | Ator admin quando disponível; `seed:<arquivo>` na importação |

### Garantias de escrita

- **Validação antes de persistir** — payload inválido é rejeitado antes de qualquer
  escrita, então a última versão válida nunca é substituída por uma quebrada.
- **Transação** — leitura da revisão, insert/update e commit acontecem na mesma
  conexão; falha implica rollback.
- **Sem lost update** — o `UPDATE` carrega o predicado `WHERE company_slug = ? AND
  revision = ?` com a revisão lida. Um escritor concorrente que já avançou a
  revisão faz o `UPDATE` afetar 0 linhas → conflito.
- **Concorrência otimista opcional** — `expected_revision` no payload da API;
  divergência retorna `409`.
- **Sem cache** — nenhuma camada de cache foi introduzida, então API e worker não
  podem divergir.

## Como editar

1. **Pela plataforma** (Flow Studio para pipelines, formulários para o resto):
   `PUT /admin/company-context` (superadmin). Aceita `expected_revision` opcional.
2. **Pelo Streamlit**: `app/ui/pages/company_context_page.py`.
3. **Leitura com metadados**: `GET /admin/company-context/record` devolve
   `revision`, `schema_version`, `source`, `updated_at`, `updated_by` e `data` —
   use para obter a `revision` antes de um update concorrente.

Respostas de `PUT /admin/company-context`:

| Situação | Código |
|----------|--------|
| Sucesso | `200` com o contexto salvo |
| Payload inválido | `400` (nada é gravado) |
| `expected_revision` desatualizada | `409` (nada é gravado) |
| Papel abaixo de superadmin | `403` |

## Bootstrap / importação dos seeds

A migração `026_company_context_seed_import` importa os arquivos de seed dentro da
própria transação de migração. Ela é **idempotente**: uma chave que já tem linha é
pulada, então um contexto mais novo no banco nunca é sobrescrito por um seed
antigo. Seeds inválidos são ignorados sem quebrar a migração.

Para rodar a importação fora da migração:

```bash
python -m app.tools.bootstrap_company_contexts --dry-run   # mostra o plano
python -m app.tools.bootstrap_company_contexts             # importa
```

Saída: `imported`, `skipped_existing`, `skipped_invalid`.

## Como o backend lê

`get_company_context(company_slug=...)` devolve o payload validado.
`get_company_context_record(...)` devolve payload + `revision`, `schema_version`,
`source` (`database` ou `seed_file`), `company_id`, `updated_at`, `updated_by`.
`get_company_context_status(...)` devolve o mesmo estado sem levantar exceção,
para readiness e onboarding.

## Onde o contexto é usado

- **Reply prompt**: `openai_provider.generate_business_reply` carrega catálogo, regras, identidade, guidelines.
- **Workflow conversacional**: `enabled_modules.scheduling`, `business_hours.timezone`, `internal_operation.operator_contact_ids`.
- **Calendar service**: `scheduling_policy.profiles` segmenta janelas.
- **Readiness**: `get_company_context_status` aparece em `/admin/system/readiness`
  e `/admin/system/onboarding`. O check `company_context_persistence` é blocker em
  produção quando o contexto global ainda vem de arquivo de seed.
- **Backup**: `company_contexts` está em `BACKUP_TABLES`
  (`app/tools/export_runtime_backup.py`), então o export lógico carrega a
  configuração ativa.
- **Auditoria**: cada update grava `audit_events` com `revision` e `schema_version`
  nos metadados (sem o payload).

## Próximas leituras

- [[20-Modules/01-Core]] (linha company_context)
- [[10-Architecture/06-Fluxo Agenda]]
- [[20-Modules/04-Plataforma Frontend]] (Flow Studio)
- [[40-Data/02-Schema do Banco]] (tabela `company_contexts`)
- [[40-Data/03-Migrações]]
- [[40-Data/06-Backup e Limpeza]]
