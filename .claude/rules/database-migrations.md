---
description: Schema, migrations e compatibilidade entre SQLite e PostgreSQL.
globs:
  - "app/storage/**"
  - "tests/test_migrations*.py"
  - "tests/test_db_backend.py"
---

# Banco e migrations

## Migration aplicada não muda

Nunca edite uma migration já registrada em `schema_migrations`. Crie a próxima
na sequência. Um banco em produção já rodou a antiga; alterá-la produz dois
schemas diferentes com o mesmo número.

## Toda migration é aditiva e idempotente

Use os helpers de `schema_utils`: `create_table`, `ensure_column`,
`create_index`, `create_unique_index`. Todos são seguros para reexecução.

`init_db()` chamado quatro vezes seguidas precisa terminar com o mesmo número
de migrations aplicadas. `tests/test_migrations_platform.py` verifica isso, e
também o caminho a partir de um banco parado numa versão anterior.

## Dois backends, uma sintaxe

Escreva com a sintaxe canônica; `create_table` traduz:

| Canônico | SQLite | PostgreSQL |
|---|---|---|
| `ID_PK` | `INTEGER PRIMARY KEY AUTOINCREMENT` | `BIGSERIAL PRIMARY KEY` |
| `TIMESTAMP` | `TIMESTAMP` | `TIMESTAMPTZ` |
| `REAL` | `REAL` | `DOUBLE PRECISION` |

Tabela nova com `id` autoincremental entra no conjunto `*_ID_TABLES` do módulo.

## Placeholders

Sempre `?`. A camada traduz para `%s` no PostgreSQL. **Valor nunca é
interpolado** — sem exceção.

Identificador (nome de tabela ou coluna) não aceita placeholder. Quando for
inevitável interpolar um, ele precisa vir de constante literal do próprio
módulo, e a origem precisa estar registrada e provada em
`tests/test_sql_interpolation_guard.py`. Esse teste é a razão de o CI poder
pular B608: ele é mais forte que a heurística do Bandit.

## Isolamento no schema

Toda tabela tenant-scoped tem `company_id NOT NULL`, e o índice principal
começa por ele. Índice único de negócio inclui `company_id`: a mesma chave
lógica pode existir em empresas diferentes.

## Conexão

Toda conexão fecha, inclusive em erro. **Nunca** chame `get_connection()`
direto: use `transaction()`, `read_connection()` ou `connection_scope()` de
`app/storage/db.py`.

`connection.close()` como última linha da função devolve a conexão **apenas no
caminho feliz**. Qualquer coisa que lance antes dela — `cursor()`, `execute()`,
`fetchall()`, `commit()`, uma conversão de linha — pula o close. No SQLite o
coletor de lixo às vezes salva; no pool do PostgreSQL nunca, porque `putconn`
só acontece dentro de `close()`.

Isto já custou caro aqui: 99 de 107 aquisições tinham esse formato, e dez
escritas inválidas — ao alcance de qualquer operator autenticado de um único
tenant — esgotavam o pool e derrubavam a plataforma para todos os outros
(`PoolTimeout` na 11ª tentativa).

Regras que vêm junto:

- **devolução exatamente uma vez.** Devolver duas vezes é pior que vazar:
  corrompe a contabilidade do pool em vez de só desperdiçar um recurso;
- **rollback antes de devolver**, senão a próxima operação naquela conexão
  herda `current transaction is aborted`;
- **nada de aninhar.** Uma função que segura conexão e chama outra que abre a
  sua consome duas ao mesmo tempo. `/health/ready` fazia isso, sem
  autenticação, uma vez por empresa;
- **cursor não sobrevive ao bloco.** Usá-lo depois é operar sobre conexão já
  devolvida, que outra requisição pode ter recebido — escrita de um tenant
  dentro da transação de outro.

## Operações grandes

- backfill em lotes, não numa varredura só;
- índice de tabela grande de forma não bloqueante quando o backend suportar;
- deploy em rolling update exige que o schema novo seja compatível com o código
  antigo durante a janela;
- o readiness detecta schema atrasado.

## SQLite não prova PostgreSQL

SQLite serializa escrita pelo lock do arquivo. PostgreSQL não. Comportamento de
fila, `SKIP LOCKED`, isolamento de transação e corrida real **só valem se
testados no PostgreSQL**. Marque esses testes como integração e rode-os no CI
com service container.
