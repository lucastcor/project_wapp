---
description: Durabilidade, concorrência e idempotência do runtime de automações.
globs:
  - "app/automations/**"
  - "app/services/automation_*.py"
  - "app/storage/automation_repository.py"
  - "app/tools/automation_worker.py"
  - "tests/test_automation_*.py"
---

# Runtime de automações

## A pergunta antes de cada escrita

**Se o processo morrer imediatamente depois desta linha, o sistema fica
consistente?** Se a resposta depende de sorte, o desenho está errado.

## Separação que sustenta o módulo

- `app/automations/` é **puro**: sem I/O, sem relógio implícito, sem banco. O
  relógio entra como parâmetro.
- `semantics.evaluate_node` decide **o que** acontece. Quem chama decide o que
  fazer com isso: a simulação avança o relógio em linha, o motor suspende e
  agenda um job.
- Existe **uma** interpretação das regras. Se simulação e runtime divergirem,
  `tests/test_automation_conformance.py` falha — e essa é a intenção.

## Efeito externo

Ordem obrigatória, sem exceção:

1. reservar no ledger (transação fecha);
2. chamar o adapter **fora de qualquer transação**;
3. gravar o resultado numa segunda escrita.

Chamada de rede dentro de transação reexecutável duplica efeito quando a
transação repete.

### Os três estados anteriores

- `succeeded` → **não repete**. A ação aconteceu.
- `failed` → **repete**. O adapter reportou falha; por contrato a ação não
  se completou.
- `pending`/indeterminado → **não repete e não avança**. Uma tentativa morreu
  entre reservar e registrar. Repetir arrisca duplicar; pular arrisca omitir.
  A execução vai para reconciliação humana, o step **não** vira `succeeded`, e
  o próximo nó **não** executa.

Tratar indeterminado como sucesso é o defeito mais caro do módulo.

## Leases e stale workers

- claim atômico: `FOR UPDATE SKIP LOCKED` no PostgreSQL, `UPDATE` guardado por
  `status` no SQLite, com nova tentativa em disputa;
- **finalização valida o dono**. `UPDATE ... WHERE id = ?` sem checar lease
  permite que um worker obsoleto conclua trabalho que outro já assumiu;
- lease vencido volta para a fila pelo reconciliador;
- o mesmo vale para o outbox e para os gates.

## Cancelamento é monotônico

Uma execução cancelada **nunca** volta a `running`, e nenhum job nasce depois.
Verifique o cancelamento antes de aplicar efeito e de novo antes de agendar o
próximo nó. Efeito já aplicado permanece auditado, e o resultado distingue
cancelado-antes-do-efeito de cancelado-depois.

## Chave de efeito

Considera empresa, workflow, execução, nó e **ocorrência lógica**. A ocorrência
precisa ser **estável entre tentativas do mesmo passo** — derivá-la de
`steps_executed` faz a chave mudar a cada retry e o ledger deixa de reconhecer
o efeito aplicado.

Na v1 a ocorrência é sempre 1, e isso é consequência do validador rejeitar
ciclos. Quando loops existirem, passa a ser o índice da iteração.

## Version pinning

A execução carrega `version_id` e o motor carrega **a versão dela**, nunca a do
deployment corrente. Republicar não muda o comportamento de quem já está em
espera.

## Pausa ≠ cancelamento

Pausar bloqueia **novas entradas** e preserva quem espera. A verificação de
deployment ativo acontece no dispatch, não no motor. Decisão humana tomada com
o fluxo pausado fica registrada e só avança na reativação.

## Retry

Backoff exponencial com **full jitter**, `max_attempts`, teto de delay,
`retry-after` do provider quando houver, e classificação explícita entre
retryable e permanente. Esgotado, vai para dead-letter consultável com retry
manual. **Nunca** aplique retry cego a operação indeterminada.

Aleatoriedade vem de `secrets`, não de `random`: cronograma de retry previsível
é observável por quem consegue provocar falhas.

## Testes

Concorrência se prova com **barrier e clock injetável**, não com `sleep` nem
com duas chamadas em sequência. Cada ponto de queda listado acima merece um
teste. Teste probabilístico é recusado.
