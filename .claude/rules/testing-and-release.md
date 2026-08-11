---
description: Como testar e o que precisa passar antes de declarar algo pronto.
globs:
  - "tests/**"
  - ".github/workflows/**"
  - "frontend/automation-studio/tests/**"
  - "checked_modules.txt"
---

# Testes e release

## Um teste que falha está dizendo algo

Nunca ajuste uma asserção, afrouxe uma validação, reduza cobertura ou troque um
mock para fazer a suíte passar. Se o teste ficou errado porque o comportamento
correto mudou, **reescreva o teste explicando por que o antigo estava errado** —
em comentário, no próprio teste.

Já aconteceu aqui: um teste afirmava que editar um rascunho mudava o título do
documento publicado. O teste codificava o defeito.

## O que um bom teste prova

- **um comportamento**, com nome que descreve o comportamento, não a função;
- o **caminho de falha**, não só o feliz;
- concorrência com **barrier e clock injetável** — nunca `sleep`, nunca
  probabilidade;
- isolamento com **duas empresas** e dados marcadores distintos.

Teste que só verifica que a função foi chamada não prova nada.

## Banco de teste é por processo

Cada sessão pytest recebe um SQLite exclusivo (o PID entra no nome) e remove
**apenas o seu** no fim. Um caminho fixo compartilhado faz a segunda sessão
apagar o banco da primeira no meio da execução, e o sintoma é o pior possível:
falhas em massa, em testes sem relação entre si, que somem quando você roda o
arquivo sozinho.

## Vazamento de conexão se prova contando

Um teste que afirma "não vaza" sem contar aberturas e fechamentos não prova
nada. Instrumente e exija `aberturas == fechamentos` depois do sucesso **e**
depois de cada exceção. Contra PostgreSQL real, use pool pequeno
(`max_size=2`), provoque mais falhas do que o pool comporta e exija que a
leitura seguinte responda.

## Nenhuma chamada externa

A suíte roda offline. Providers em mock, socket bloqueado nos cenários que
tocariam rede. Nenhuma fixture com dado real de cliente, telefone ou documento.

## Marcadores

```bash
pytest -q                    # rápida, SQLite, sem serviço externo
pytest -q -m integration     # exige PostgreSQL e Redis
```

Teste de integração **falha** quando o serviço não existe; não pula silenciosamente
naquilo que precisa provar. Quando pular for legítimo, o motivo aparece no skip.

## Gates

Backend:

```bash
git diff --check
ruff check app tests --select F821,F822,F823,E9   # base do CI
ruff check <arquivos tocados>                      # completo nos novos/alterados
mypy  <checked_modules.txt>
bandit -q -s B608 <checked_modules.txt>
pip-audit -r requirements.txt
pytest -q
compileall -q app
```

Frontend:

```bash
npm run typecheck && npm run lint && npm test && npm run build
npm audit --omit=dev
```

`checked_modules.txt` é a fonte única das listas de mypy/Bandit.
`tests/test_checked_modules.py` prova que nenhuma superfície sensível ficou de
fora — o CI lê o arquivo em vez de repetir a lista.

## Sem supressão

Nada de `# noqa`, `# type: ignore` ou `# nosec` para calar uma ferramenta.
Corrija a causa. Quando a exceção for legítima, ela precisa de justificativa
individual e de um teste que prove a garantia — como o guard de SQL faz.

## Validado ≠ executado

`NÃO EXECUTADO` é uma resposta legítima e honesta. "Passou" sem ter rodado, não.

Nunca declare produção com base em SQLite e mock. Fila, corrida e recuperação
precisam de PostgreSQL real; imagem precisa de build real.

## Classificação honesta

| Nível | Exige |
|---|---|
| desenvolvimento | suíte rápida passando |
| homologação | + gates completos + migrations verificadas |
| piloto controlado | + PostgreSQL real + dois workers + crash/recovery + Docker |
| produção | + operação observada sob carga real |
