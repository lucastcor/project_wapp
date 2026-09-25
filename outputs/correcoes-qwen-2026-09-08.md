# Correções e validação — 08/09/2026

## Resultado

Os quatro pontos da análise foram tratados: extração de todos os textos do
lote Meta; confirmação HTTP após persistência com processamento em worker;
configuração local/produção coerente; readiness com saúde de workers e chave
de pseudonimização. O modelo local instalado, `qwen3.5:9b-q4_K_M`, foi integrado
via Ollama nativo e exercitado de ponta a ponta com dados sintéticos e envio mock.

A migration 068 adiciona fila persistente, deduplicação por empresa/ID do
provider e heartbeats. A ordem por conversa é preservada. Resultado e outbound
são salvos no mesmo commit. Jobs interrompidos com efeitos incertos ficam em
revisão; o operador reconcilia e conclui sem reexecutar automaticamente.

O `.env` local usa `runtime/local.db`, IA real e canais simulados. Credenciais
locais de segurança foram substituídas quando eram placeholders, sem exposição
em logs ou documentação. Backups privados dos arquivos anteriores foram
preservados e ignorados pelo Git. `.env.production` foi corrigido para modo
produção estrito; as credenciais externas ausentes continuam pendentes.

## Evidências

- Suíte completa SQLite: 1.318 passaram, 17 ignorados; três avisos de depreciação.
- Suíte completa PostgreSQL 17: 1.331 passaram, quatro ignorados; três avisos.
- Depois dessas suítes, foi acrescentado um teste de autorização da revisão:
  a suíte específica da fila passou com 11 testes em SQLite e PostgreSQL.
- Ruff passou; mypy passou em 150 arquivos.
- `git diff --check` e validação do Compose de produção passaram.
- Imagem `project-wapp-api:qwen-local` construída; container descartável com
  Python 3.13.15 aplicou todas as migrations e encontrou o Qwen no Ollama do Mac.
- Teste real final: classificação do boleto como `financeiro` em 3,08 s;
  interpretação de cancelamento em 1,59 s; confirmação do webhook em 0,02 s;
  geração no worker em 14,46 s; envio mock em 0,02 s; job `completed`.
- API local reiniciada na porta 8010: `/health/ready` com zero bloqueios,
  `overall_status=warning`, `pilot_ready=true`, `scale_ready=false`.

O smoke usa uma empresa sintética, sem clientes reais. Os resultados estão em
[qwen-local-smoke-2026-09-08.json](qwen-local-smoke-2026-09-08.json). Um teste
isolado não estima acurácia geral, capacidade de carga ou latência de produção.
O contexto operacional precisa estar publicado para a empresa; o template
global só é permitido pelo comportamento de desenvolvimento existente.

## Limites para um piloto empresarial

O código está preparado para validar o fluxo local e iniciar a preparação de
um piloto supervisionado. Ainda não há evidência de operação real na empresa:
faltam credenciais Meta, validação do canal/domínio e configuração específica,
avaliação representativa de conversas, ensaio de carga e operação humana de
contingência. O status local `pilot_ready` não substitui esses requisitos.

A fila de revisão possui endpoints administrativos, sem tela nova. Falha de
rede após aceitação do envio não permite garantir entrega exatamente uma vez;
nesses casos a revisão evita repetição automática. O limite de concorrência
Ollama é por processo. Treinamento local do Qwen não foi implementado: o
pipeline de fine-tuning OpenAI permanece opcional e desligado neste ambiente.

Procedimento completo: [guia local](../docs/local_qwen.md).
