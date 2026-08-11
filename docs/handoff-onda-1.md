# Continuação da Onda 1 — o que falta e por quê

Documento de retomada. Quem pegar daqui não precisa reconstruir contexto: está
tudo aqui, com IDs, arquivos e o comando que prova cada coisa.

**Estado:** Onda 1 **EM ANDAMENTO**. O P0 sistêmico de conexões (I-01) está
fechado e provado contra PostgreSQL real. Restam 1 item de severidade alta, 5 de
baixa/média e 9 fases do escopo original.

---

## Objetivo da Onda 1

Fechar isolamento multi-tenant e integridade da camada de dados a ponto de o
produto poder receber um piloto comercial controlado. Concretamente:

- toda leitura e escrita de dado privado carrega empresa, e o filtro entra
  **dentro** da query;
- nenhuma entrada malformada de um tenant degrada a disponibilidade dos outros;
- todo achado vira teste, e todo teste prova o que afirma provar.

O critério de saída está em `docs/closure-plan.md`, seção de critérios de
fechamento. A Onda 1 só fecha com revisão independente sem P0/P1 aberto.

---

## Como retomar

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt

# suíte rápida (SQLite)
.venv/bin/python -m pytest -q

# PostgreSQL real — necessário para I-01, filas e corrida
#   (o socket do macOS estoura 103 bytes; use um diretório curto como /tmp/wpg)
WAPP_TEST_DATABASE_URL="postgresql://wapp@127.0.0.1:55432/wapp_test" \
  .venv/bin/python -m pytest -q -p no:randomly

# a prova do I-01 (marcada como integração, por isso o -m "")
WAPP_TEST_DATABASE_URL="postgresql://wapp@127.0.0.1:55432/wapp_test" \
  .venv/bin/python -m pytest tests/test_connection_pool_postgres.py -q -m ""

# gates
.venv/bin/python -m ruff check app tests
modules() { grep -v '^[[:space:]]*#' checked_modules.txt | grep -v '^[[:space:]]*$' | tr '\n' '\0'; }
modules | xargs -0 .venv/bin/python -m mypy --ignore-missing-imports \
  --follow-imports=silent --disable-error-code=import-untyped
modules | xargs -0 .venv/bin/python -m bandit -q -s B608
```

Baseline verde no momento deste commit:

| Gate | Resultado |
|---|---|
| pytest SQLite | 866 passed, 5 skipped |
| pytest PostgreSQL 14.21 | 870 passed, 1 skipped |
| pool I-01 (PG real, `max_size=2`) | 5 passed |
| ruff `app tests` | All checks passed |
| mypy (78 módulos) | no issues found |
| bandit `-s B608` | 0 High, 0 `#nosec` |
| frontend typecheck/lint/test/build | OK / OK / 40 passed / OK |

---

## O que está FECHADO (não reabrir sem motivo)

| ID | O que era |
|---|---|
| **I-01** P0 | 99 de 107 aquisições fechavam só no caminho feliz; 10 escritas inválidas esgotavam o pool e derrubavam a plataforma |
| **L-06** P1 | SQLite não aplicava integridade referencial (FK desligada por padrão) |
| **E-01** P1 | 500/404 devolvido sobre mutações já comitadas, nos três ramos de `respond_to_queue_offer` |
| **T-03** P0 | 5 rotas mutavam recurso de outro tenant por ID |
| **T-05, T-07, T-08, T-09, T-10** | escopo de rota, agregados de plataforma, filtro em SQL, erro de escopo |
| **F-01** P1 | `has_role()` decidia com papel global, ignorando membership rebaixada |
| **D-01** | `/health/ready`, **sem autenticação**, aninhava conexões |
| **D-03** | cursor sobrevivia ao bloco, operando sobre conexão já devolvida ao pool |
| **D-09** | pool global criado sem lock: 8 threads → 8 pools, 7 vazando para sempre |
| **D-10** | E-01 estava fechado só para `released_booking_id` |
| **D-11** | o guarda estrutural aceitava 6 de 7 aquisições inseguras |
| **D-05, D-13** | `close()` residual; `sqlite3.connect` fora de `finally` |
| **A2** | `checked_modules.txt` deixava remover os 8 módulos mais críticos sem falhar |
| **D-01 (lint)** | o gate de lint eram 4 regras; 260 achados, 58 `noqa` mortos |

---

## O que está ABERTO

### Prioridade 1 — risco operacional real

#### [D-02] alta · `init_db()` sem advisory lock

`app/storage/db.py` — `init_db()` é chamado no lifespan da API
(`app/main.py:67`) e em todos os workers e tools (5 pontos).

**Medido** com barrier e 4 threads sobre banco vazio: 3 estouram
`UniqueViolation: duplicate key value violates unique constraint
"pg_type_typname_nsp_index"`, 1 termina. `CREATE TABLE IF NOT EXISTS` **não é
atômico** no PostgreSQL — o perdedor estoura em `pg_type` antes da primeira
migration.

Não corrompe (tudo roda numa transação; o perdedor faz rollback inteiro). O dano
é **disponibilidade**: num rolling update, os pods perdedores entram em
CrashLoopBackOff até o vencedor terminar. Numa migration tardia o perdedor ainda
segura `ACCESS EXCLUSIVE` enquanto espera, e o `statement_timeout` configurado
pode abortá-lo com tráfego de produção bloqueado atrás do lock.

**Fazer:** `pg_advisory_lock` em torno de `_run_migrations`, liberado em
`finally`. No SQLite o lock do arquivo já serializa. **Teste:** N processos com
barrier sobre banco vazio; N−1 esperam e terminam OK, 53 versões distintas.
Hoje esse teste falha.

### Prioridade 2 — fases do escopo não alcançadas

| Fase | Item | Onde |
|---|---|---|
| 4 | **G-01** — `TenantScopeError` interno vira 400; precisa distinguir erro de entrada (400) de invariante quebrada (500 com `request_id`) | `app/main.py`, `app/core/tenancy.py` |
| 5 | **D-01 dashboard** — `Promise.all` derruba a tela inteira quando um bloco dá 403; precisa degradar por bloco | `app/platform/assets/app.js:575`, `app/ui/pages/system_page.py:31` |
| 6 | **A-01** — o detector AST ainda não cobre GET nem todos os rebindings; inventariar as ~150 rotas e declarar quais têm prova estrutural e quais têm prova comportamental | `tests/test_tenant_scope_consumption.py` |
| 7 | **B-03** — ingestão pública de A grava em `contacts.contact_name` global; nome de tenant deve ir para `contact_company_profiles` | `app/storage/repository.py:1198,633` |
| 8A | política de rotas por `startswith` — classificar por método e template real | `app/api/company_scope.py` |
| 8C | gate de supressões deve olhar tracked + diff + untracked, comparando contra base SHA no CI | `.github/workflows/ci.yml` |
| 8D | `git diff --check` no CI é inócuo: precisa comparar `base...HEAD` no PR e `before..HEAD` no push, com `fetch-depth` suficiente | `.github/workflows/ci.yml` |
| 9 | **A6** OpenAPI (`company_id`/`company_slug` sumiram do schema), **A7** escopo de path/body, **A8** matriz de multi-membership | `app/api/company_scope.py`, `app/api/admin_routes.py` |
| 10 | **T-11** auditoria/broadcast derivando de `AuthorizedScope`; **B-02** `release_booking_slot` e `reschedule_calendar_booking` sem `company_id` | `app/services/conversation_workflow_service.py:959,1010` |

### Prioridade 3 — achados menores do ciclo de vida

| ID | O que é |
|---|---|
| **D-04** média | `commit()` após erro engolido preserva no SQLite e descarta tudo no PostgreSQL, em silêncio. Nenhum ponto de produção faz isso hoje; é guarda-corpo. |
| **D-12** baixa | `ScopedCursor` tem 3 desvios (`cursor._cursor`, método capturado dentro do bloco, conexão de `db.transaction()` sem wrapper). Não explorado hoje — o defeito é o guarda prometer o que não entrega. |
| **D-06** baixa | read-modify-write sem árbitro em `save_contact`, `update_contact_memory`, `upsert_billing_plan`, `upsert_conversation_flow`. O último não tem índice único de negócio: duplica em silêncio. |
| **D-08** info | `/health/ready` abre TCP fora do pool a cada sonda. |
| **D-14** cosmético | sessão pytest morta por `kill -9` deixa o banco órfão no tempdir. |

---

## Contagem no momento deste commit

```
achados=66  corrigidos=26  pendentes=40
P0=16  P1=14  P2=10
```

Os 16 P0 pendentes (R1–R7, G-01–G-03, INF-01–INF-05, INF-07) são das **Ondas
2–6**, fora do escopo da Onda 1. Recontar sempre a partir do arquivo:

```bash
.venv/bin/python - <<'PY'
import re, pathlib
from collections import Counter
h = re.findall(r"^### \[([^\]]+)\] (P\d) — (.+?) · \*\*([^*]+)\*\*",
               pathlib.Path("docs/closure-plan.md").read_text(), re.M)
pend = [x for x in h if x[3].startswith(("PENDENTE", "MITIGADO"))]
print(f"achados={len(h)} corrigidos={len(h)-len(pend)} pendentes={len(pend)}")
print(Counter(x[1] for x in pend))
PY
```

---

## NÃO EXECUTADO — e por quê

| O quê | Motivo |
|---|---|
| Docker build e smoke | daemon parado nesta máquina (`docker compose config` passa) |
| Redis | não instalado; também não está em `requirements.txt` (INF-01) |
| Dois workers, concorrência real, crash/recovery, fencing | Onda 2, não iniciada |
| Upgrade a partir de versão anterior | só banco limpo e reexecução foram verificados |
| `npm ci` | usei o `node_modules` existente para não tocar no lockfile |

---

## Duas lições de método que custaram caro

Estão aqui porque as duas se repetiram, e a segunda vez foi pior que a primeira.

**1. Verde não é prova.** Três guardas deste repositório passavam sem testar
nada: o detector de escopo aceitava o próprio defeito que existia para pegar; a
asserção de auditoria vivia dentro de um laço que nunca executava; a lista de
módulos críticos era protegida por uma contagem. Todo guarda novo precisa de
**controle negativo por mutação** — quebre de propósito e exija que falhe.

**2. "Custou zero" merece desconfiança.** Ligar `PRAGMA foreign_keys` fez os 830
testes passarem de primeira, e eu registrei isso como prova de que era seguro.
Era uma negação de serviço: a suíte não exercita esgotamento de pool, então o
verde não dizia nada sobre isso. A ordem certa era fechar o vazamento primeiro —
e está no comentário de `app/storage/db.py`, onde a próxima pessoa vai olhar.
