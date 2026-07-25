---
title: Schema do Banco
type: data
tags: [dados, schema, ddl]
updated: 2026-04-28
---

# Schema do Banco

DDL completo em `app/storage/db.py`. Lista das tabelas atuais (após migração 023).

## Tabelas

### `companies`
Tenants do sistema.

| Coluna | Tipo | Notas |
|--------|------|-------|
| `id` | INT PK | autoincrement |
| `slug` | TEXT UNIQUE | normalizado |
| `name` | TEXT | display |
| `business_type` | TEXT | livre |
| `active` | INT | 0/1 |
| `created_at` | TIMESTAMP | |
| `updated_at` | TIMESTAMP | |

### `contacts`
Contato global. Compartilhado entre tenants — perfil específico vai em `contact_company_profiles`.

| Coluna | Tipo |
|--------|------|
| `contact_id` | TEXT PK |
| `contact_name` | TEXT |
| `last_classification` | TEXT |
| `last_interaction_at` | TIMESTAMP |
| `conversation_summary` | TEXT |
| `pipeline_stage` | TEXT |
| `next_action` | TEXT |
| `lead_temperature` | TEXT |
| `customer_profile` | TEXT |
| `notes` | TEXT |
| `updated_at` | TIMESTAMP |
| `company_id` | INT (legacy / fallback) |

### `contact_company_profiles` *(migração 022)*
Perfil tenant-scoped do contato.

| Coluna | Tipo |
|--------|------|
| `contact_id` | TEXT |
| `company_id` | INT |
| `contact_name` | TEXT |
| `last_classification` | TEXT |
| `last_interaction_at` | TIMESTAMP |
| `conversation_summary` | TEXT |
| `pipeline_stage` | TEXT |
| `next_action` | TEXT |
| `lead_temperature` | TEXT |
| `customer_profile` | TEXT |
| `notes` | TEXT |
| `updated_at` | TIMESTAMP |
| **PK composta** | `(contact_id, company_id)` |

### `messages`

| Coluna | Tipo |
|--------|------|
| `id` | BIGSERIAL PK |
| `contact_id` | TEXT FK → contacts |
| `company_id` | INT |
| `message` | TEXT |
| `classification` | TEXT |
| `reply` | TEXT |
| `classification_reason` | TEXT |
| `classification_confidence` | DOUBLE |
| `auto_reply` | INT |
| `reply_source` | TEXT |
| `action` | TEXT |
| `priority` | TEXT |
| `created_at` | TIMESTAMPTZ |

### `pending_questions`

| Coluna | Tipo |
|--------|------|
| `id` | BIGSERIAL PK |
| `contact_id` | TEXT FK |
| `company_id` | INT |
| `customer_message` | TEXT |
| `question_type` | TEXT |
| `question_for_owner` | TEXT |
| `status` | TEXT default `pending` |
| `owner_answer` | TEXT |
| `resolution_type` | TEXT |
| `customer_followup_question` | TEXT |
| `clarification_consumed` | INT |
| `applies_globally` | INT |
| `knowledge_action` | TEXT |
| `created_at` | TIMESTAMPTZ |
| `resolved_at` | TIMESTAMPTZ |

### `knowledge_entries`

| Coluna | Tipo |
|--------|------|
| `id` | BIGSERIAL PK |
| `source_question_id` | BIGINT FK |
| `contact_id` | TEXT |
| `company_id` | INT |
| `title` | TEXT |
| `content` | TEXT |
| `knowledge_type` | TEXT |
| `applies_globally` | INT |
| `status` | TEXT default `active` |
| `created_at` | TIMESTAMPTZ |

### `message_examples`

| Coluna | Tipo |
|--------|------|
| `id` | BIGSERIAL PK |
| `company_id` | INT |
| `profile_name` | TEXT |
| `scenario_type` | TEXT |
| `customer_message` | TEXT |
| `approved_reply` | TEXT |
| `style_notes` | TEXT |
| `quality_score` | DOUBLE |
| `used_for_training` | INT |
| `active` | INT |
| `created_at` | TIMESTAMPTZ |

### `outbound_messages`

| Coluna | Tipo |
|--------|------|
| `id` | BIGSERIAL PK |
| `contact_id` | TEXT FK |
| `company_id` | INT |
| `pending_question_id` | BIGINT FK |
| `message` | TEXT |
| `source` | TEXT |
| `status` | TEXT (`pending|sent|failed|canceled`) |
| `delivery_status` | TEXT |
| `provider_message_id` | TEXT |
| `provider_response` | TEXT |
| `created_at` | TIMESTAMPTZ |
| `sent_at` | TIMESTAMPTZ |

### `finetuning_jobs`

| Coluna | Tipo |
|--------|------|
| `id` | BIGSERIAL PK |
| `job_id` | TEXT UNIQUE |
| `company_id` | INT |
| `status` | TEXT |
| `base_model` | TEXT |
| `suffix` | TEXT |
| `training_file_id` | TEXT |
| `validation_file_id` | TEXT |
| `fine_tuned_model` | TEXT |
| `provider_error_code` | TEXT |
| `provider_error_message` | TEXT |
| `created_at` | TIMESTAMPTZ |
| `finished_at` | TIMESTAMPTZ |

### `audit_events`

| Coluna | Tipo |
|--------|------|
| `id` | BIGSERIAL PK |
| `actor_id` | TEXT |
| `action` | TEXT |
| `resource_type` | TEXT |
| `resource_id` | TEXT |
| `company_id` | INT |
| `status` | TEXT |
| `request_id` | TEXT |
| `metadata` | TEXT JSON |
| `http_method` | TEXT |
| `http_path` | TEXT |
| `created_at` | TIMESTAMPTZ |

### `schema_migrations`

| Coluna | Tipo |
|--------|------|
| `version` | TEXT PK |
| `name` | TEXT |
| `applied_at` | TIMESTAMPTZ |

### `calendar_integrations`

| Coluna | Tipo |
|--------|------|
| `id` | BIGSERIAL PK |
| `company_id` | INT |
| `provider` | TEXT |
| `external_calendar_id` | TEXT |
| `config_json` | TEXT |
| `active` | INT |
| `created_at`, `updated_at` | TIMESTAMPTZ |

### `appointment_bookings`

| Coluna | Tipo |
|--------|------|
| `id` | BIGSERIAL PK |
| `company_id` | INT |
| `contact_id` | TEXT |
| `integration_id` | BIGINT FK |
| `provider` | TEXT |
| `external_event_id` | TEXT |
| `scheduling_profile_key` | TEXT |
| `customer_profile` | TEXT |
| `title` | TEXT |
| `description` | TEXT |
| `starts_at`, `ends_at` | TIMESTAMPTZ |
| `timezone` | TEXT |
| `status` | TEXT (`confirmed|canceled|rescheduled|completed`) |
| `created_at`, `updated_at` | TIMESTAMPTZ |
| (`outcome`, `cancellation_reason`, `outcome_recorded_at` em migrações 013) |

### `conversation_flows`

| Coluna | Tipo |
|--------|------|
| `id` | BIGSERIAL PK |
| `company_id` | INT |
| `contact_id` | TEXT |
| `flow_type` | TEXT |
| `status` | TEXT |
| `state_json` | TEXT |
| `created_at`, `updated_at` | TIMESTAMPTZ |

### `customer_reminders`

| Coluna | Tipo |
|--------|------|
| `id` | BIGSERIAL PK |
| `company_id` | INT |
| `contact_id` | TEXT |
| `reminder_text` | TEXT |
| `reminder_summary` | TEXT |
| `remind_at` | TIMESTAMPTZ |
| `timezone` | TEXT |
| `status` | TEXT (`pending|sent|failed|canceled`) |
| `audience` | TEXT (`customer|internal`) |
| `source` | TEXT |
| `appointment_booking_id` | INT |
| `outbound_message_id` | BIGINT FK |
| `created_at`, `updated_at` | TIMESTAMPTZ |

### `appointment_queue_entries`

| Coluna | Tipo |
|--------|------|
| `id` | BIGSERIAL PK |
| `company_id` | INT |
| `contact_id` | TEXT |
| `priority` | INT |
| `status` | TEXT |
| `desired_window` | TEXT |
| `preferred_dates` | TEXT |
| `scheduling_profile_key` | TEXT |
| `customer_profile` | TEXT |
| `request_summary` | TEXT |
| `source` | TEXT |
| `notes` | TEXT |
| `created_at`, `updated_at` | TIMESTAMPTZ |

### `appointment_queue_offers`

| Coluna | Tipo |
|--------|------|
| `id` | BIGSERIAL PK |
| `company_id` | INT |
| `queue_entry_id` | BIGINT FK |
| `booking_id` | BIGINT FK |
| `outbound_message_id` | BIGINT FK |
| `slot_starts_at`, `slot_ends_at` | TIMESTAMPTZ |
| `status` | TEXT (`offered|accepted|declined|expired|wait`) |
| `response_window_seconds` | INT |
| `responded_at` | TIMESTAMPTZ |
| `created_at`, `updated_at` | TIMESTAMPTZ |

### `inbound_message_receipts` *(migração 018)*

| Coluna | Tipo |
|--------|------|
| `id` | BIGSERIAL PK |
| `provider` | TEXT |
| `provider_message_id` | TEXT |
| `company_id` | INT |
| `contact_id` | TEXT |
| `payload_mode` | TEXT |
| `status` | TEXT (`started|completed|failed`) |
| `error_message` | TEXT |
| `created_at`, `updated_at` | TIMESTAMPTZ |
| **UNIQUE** | `(provider, provider_message_id, company_id)` |

### `billing_plans`

| Coluna | Tipo |
|--------|------|
| `id` | BIGSERIAL PK |
| `plan_key` | TEXT UNIQUE |
| `name` | TEXT |
| `price_monthly_cents`, `price_annual_monthly_cents` | INT |
| `display_order` | INT |
| `limits_json`, `features_json` | TEXT |
| `active` | INT |

### `billing_subscriptions`

| Coluna | Tipo |
|--------|------|
| `id` | BIGSERIAL PK |
| `company_id` | INT |
| `plan_key` | TEXT FK |
| `status` | TEXT (`trial|active|canceled|expired`) |
| `started_at`, `expires_at`, `canceled_at` | TIMESTAMPTZ |
| `metadata_json` | TEXT |

### `billing_usage_records`

| Coluna | Tipo |
|--------|------|
| `id` | BIGSERIAL PK |
| `company_id` | INT |
| `metric` | TEXT |
| `quantity` | INT |
| `period` | TEXT |
| `recorded_at` | TIMESTAMPTZ |

## Índices importantes

`db.py` cria índices operacionais e analíticos. Categorias:

- `idx_messages_company_created_at`
- `idx_pending_questions_company_status_created_at`
- `idx_outbound_messages_company_status_created_at`
- `idx_knowledge_entries_company_status_created_at`
- `idx_message_examples_company_active_score`
- `idx_finetuning_jobs_company_created_at`
- `idx_audit_events_company_created_at`, `idx_audit_events_request_id`, `idx_audit_events_actor_created_at`, `idx_audit_events_http_path_created_at`
- `idx_appointment_bookings_company_starts_at`, `idx_appointment_bookings_status_starts_at`
- `idx_calendar_integrations_company_active`
- `idx_conversation_flows_contact_status`
- `idx_customer_reminders_status_remind_at`, `idx_customer_reminders_company_status`
- `idx_contact_company_profiles_company_contact`, `idx_contact_company_profiles_company_updated_at`

## Próximas leituras

- [[40-Data/03-Migrações]]
- [[40-Data/04-Multi-tenant]]
