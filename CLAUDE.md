# Project WAPP

Plataforma SaaS multi-tenant de atendimento por WhatsApp: orquestração de
conversa com IA, RAG governado, agenda, e um Automation Studio de fluxos
visuais com execução durável.

FastAPI · Python 3.13 · SQLite (dev/teste) + PostgreSQL (produção) ·
React 18 + TypeScript strict (`frontend/automation-studio`).

## Regra que vale acima de todas

**`company_id` não é um parâmetro; é uma fronteira.** Toda leitura e escrita de
dado privado carrega empresa, e o filtro entra **dentro** da query — nunca como
compreensão de lista depois do fetch. Resolver a empresa não é autorizar o
acesso a ela.

## Mapa rápido

| Preciso mexer em… | Vá para |
|---|---|
| autorização, escopo de empresa | `app/api/company_scope.py`, `app/api/admin_security.py` |
| identidade, principals, sessões | `app/services/admin_principal_service.py`, `app/storage/identity_repository.py` |
| orquestração da conversa | `app/core/orchestrator.py` |
| domínio de automações (puro) | `app/automations/` |
| runtime de automações | `app/automations/engine.py`, `app/services/automation_*` |
| persistência | `app/storage/` (um repositório por assunto) |
| schema | `app/storage/migrations_*.py` |
| configuração | `app/core/config.py` |
| frontend do Studio | `frontend/automation-studio/src/` |

## Comandos

```bash
.venv/bin/python -m pytest -q                    # suíte (rápida, SQLite)
.venv/bin/python -m pytest -q -m integration     # exige PostgreSQL/Redis
.venv/bin/python -m ruff check app tests
uvicorn app.main:app --reload                    # API em :8000
python -m app.tools.automation_worker --run-once # um ciclo do worker

cd frontend/automation-studio
npm run typecheck && npm run lint && npm test && npm run build
```

## Regras detalhadas

Leia a regra do domínio antes de editar:

- `.claude/rules/security-tenancy.md` — isolamento, RBAC, escopo
- `.claude/rules/automation-runtime.md` — durabilidade, leases, efeitos
- `.claude/rules/database-migrations.md` — schema e compatibilidade
- `.claude/rules/testing-and-release.md` — testes e gates

## Revisores

Invoque antes de mexer numa área sensível e depois de implementar:

| Agente | Quando |
|---|---|
| `security-tenancy-reviewer` | rotas `/admin`, escopo, identidade |
| `automation-reliability-reviewer` | outbox, leases, efeitos, gates, worker |
| `rag-model-governance-reviewer` | knowledge, dataset, avaliação, modelos |
| `database-lifecycle-reviewer` | `db.py`, `sql_support.py`, repositórios, migrations, pool |
| `release-verifier` | ao fim de cada onda, antes de declarar pronto |

## O que nunca fazer

- **Não afrouxar** validação, isolamento, gate ou asserção para a suíte passar.
  Um teste que falha está dizendo algo.
- **Não esconder** com `noqa`, `# type: ignore`, `except Exception: pass` ou
  mock que remova justamente o comportamento sob teste.
- **Não interpretar `company_id=None` como acesso global** para um principal
  tenant-scoped.
- **Não usar papel global** (`global_role`) como papel dentro de uma empresa.
  O papel efetivo vem da membership.
- **Não alterar migration já aplicada.** Crie a próxima.
- **Não abrir conexão sem gerenciador.** Use `transaction()`, `read_connection()`
  ou `connection_scope()` de `app/storage/db.py`. `connection.close()` como
  última linha devolve a conexão só no caminho feliz — foi assim que dez
  escritas inválidas esgotaram o pool e derrubaram a plataforma (I-01).
- **Não provar comportamento PostgreSQL com SQLite.** São filas diferentes.
- **Não chamar provider externo** em teste. Nem OpenAI, nem Meta, nem Google,
  nem CalDAV.
- **Não commitar, fazer push ou abrir PR** sem pedido explícito.
- **Não declarar validado** o que não foi executado. `NÃO EXECUTADO` é uma
  resposta legítima; "passou" sem ter rodado, não.

## Idioma

Documentação, comentários e mensagens ao usuário em **português**.
Identificadores de código em **inglês**. Mensagens de log e códigos de erro em
inglês, para casar com a operação.

## Estado

Motor de automações desligado por padrão (`AUTOMATION_ENGINE_ENABLED=false`).
Nenhuma empresa executa automação sem duas decisões explícitas: ligar o motor e
ativar o fluxo. Ver `docs/automation_studio.md`.
