---
description: Isolamento multi-tenant, escopo autorizado e RBAC por membership.
globs:
  - "app/api/**"
  - "app/services/admin_*.py"
  - "app/services/tenant_*.py"
  - "app/storage/identity_repository.py"
  - "tests/test_*authoriz*.py"
  - "tests/test_*isolation*.py"
  - "tests/test_*security*.py"
---

# Isolamento e autorização

## O escopo autorizado é a única fonte

`require_company_scope` resolve **e autoriza** a empresa, e grava o resultado em
`request.state`. Depois disso:

- use `require_authorized_company_id(request)` ou `get_authorized_scope(request)`;
- **nunca** volte a ler `company_id` da query no handler;
- **nunca** chame `repository.resolve_company_id(...)` dentro de um handler.

O motivo é concreto: `resolve_company_id(None, None)` cai em
`get_default_company()`. Um principal com uma membership que omitisse a empresa
passava a operar na primeira empresa ativa do banco.

## `None` nunca é "todas"

Para um principal tenant-scoped, ausência de empresa significa:

- **uma** membership → aquela empresa;
- **várias** memberships em leitura → o conjunto da membership, com o filtro
  dentro da query;
- **várias** memberships em mutação → 400, exigindo que o operador diga qual;
- **nenhuma** membership → 403.

Só um principal global recebe escopo global.

## Todo ID é autorizado, venha de onde vier

Query, path, body, evento ou registro carregado por ID — todos passam pela
mesma autorização. Carregar o recurso e **depois** inferir a empresa é
autorização invertida: nesse ponto o dado do outro tenant já foi lido.

```python
# errado: o registro decide a empresa
record = repo.get_by_id(resource_id)
company_id = record["company_id"]

# certo: a empresa decide o que pode ser lido
company_id = require_authorized_company_id(request)
record = repo.get_by_id(resource_id, company_id=company_id)
```

## Papel efetivo vem da membership

`global_role` autoriza **operações globais**. Dentro de uma empresa, o papel é
o da membership naquela empresa. Não descarte `role` ao carregar memberships.

| Papel | Pode |
|---|---|
| `viewer` | ler dados e execuções redigidas |
| `operator` | disparar, retry/cancel, operar portões |
| `superadmin` (tenant) | criar, editar, publicar, ativar na empresa |
| superadmin **global** | identidades, criação de empresa, worker |

Trocar de empresa recalcula permissão. Elevação ou revogação invalida sessão.

## Classificação de rota

Cada grupo declara política em `ROUTE_POLICIES`:

| Política | Significado |
|---|---|
| `unscoped` | sem dimensão de empresa; **nenhum dado de tenant** |
| `platform_global` | exige principal global |
| `tenant` | exige empresa autorizada |

Caminho não declarado cai em `tenant` (o mais restritivo) e é registrado.
Uma rota nova mutável sem declaração quebra
`test_every_mutating_admin_route_is_covered_by_a_scope_policy`.

Atenção a agregados: contagem, saúde e metadados globais numa rota `unscoped`
vazam informação de outros tenants.

## Não permita enumeração

Inexistente e não autorizado respondem **igual**. Diferenciar 404 de 403
permite mapear recursos alheios.

## Segredos

Token só como hash (SHA-256 de `secrets.token_urlsafe`), comparado com
`hmac.compare_digest`. Nunca em log, resposta, erro, fixture ou `graph_json`.
`X-Admin-Actor` é rótulo, **nunca** identidade.

## Todo achado vira teste

A matriz mínima: principal sem/uma/várias memberships × viewer/operator/
superadmin × global/não-global × company omitido/autorizado/de outro tenant ×
query/path/body × GET/POST/PATCH/DELETE. Com duas empresas e dados marcadores
distintos, provando ausência de leitura, escrita, enumeração e efeito cruzado.
