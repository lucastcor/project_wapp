# Pacote 04 — release, pequenos clientes e evolução

## P08 — Release e homologação

**Depende:** P07. **Ler:** VALIDACAO, `docs/production_runbook.md`, `docs/local_qwen.md`, `compose.yaml`, Dockerfile, `.github/workflows/ci.yml`, `tools/preflight_check.py`, migration/backup/restore e workers. Conferir comportamento do comando antes de executá-lo: o preflight atual inicializa banco/migrations, portanto não é leitura pura em qualquer ambiente.

**Executar:** consolidar versão com código e documentos; atualizar exemplos de ambiente sem segredos; build reproduzível na versão Python da imagem; banco PostgreSQL e Redis isolados; migrations explícitas antes de API/workers; heartbeat real. Confirmar que Ollama está acessível ao runtime implantado e medir carga agregada entre processos. Não usar notebook suspensível como infraestrutura não supervisionada de cliente.

Preparar configuração real de uma empresa, número Meta permitido, webhook HTTPS e agenda de teste. Executar smoke completo com participantes/números autorizados; uma autorização de implementação não é autorização genérica para mensagens a clientes. Sem credencial, finalizar scripts/testes e registrar os valores ausentes por nome, nunca por segredo.

Ensaiar criação, remarcação, cancelamento e lembrete além da janela; inspecionar banco, calendário e status WhatsApp. Validar cenário de falha e intervenção humana. Testar backup/restore com todas as novas tabelas, rollback de release e compatibilidade de schema; não presumir que reverter imagem desfaz migration. Atualizar runbook para schema/estado novos.

**Aceite:** gates G0–G3 com evidências do artefato final, sem P0/P1 de segurança ou operação incorreta em aberto. Relatório de liberação enumera capacidades habilitadas, limites, responsáveis, fallback e pendências de capacidade desligada. Não usar `pilot_ready=true` isoladamente como sinal de aceite.

## P09 — Piloto e resultado de negócio

**Depende:** P08/G3. Começar com uma empresa de serviços e agenda, supervisão disponível e regras simples. Depois ampliar para 3–5 empresas do mesmo nicho; janela sugerida de observação de cerca de 30 dias. Seleção de empresas, horários e mensagens reais requer contexto/autorização do usuário; preparar tudo que puder antes de solicitar esses dados.

**Executar:** registrar baseline anterior, preço/compromisso de piloto quando acordado, responsáveis e SLA; acompanhar falhas diariamente no início. Medir agendamentos válidos, comparecimento, vagas recuperadas, horas poupadas descontando correções, latência, intervenção humana, custo direto e suporte. Não confundir reserva criada com atendimento realizado ou ROI.

**Aceite G4:** dado rastreável de uso e custos; feedback empresarial; conclusão explícita de continuar, corrigir ou reduzir escopo. Disposição a pagar e renovação são hipóteses até observadas. Não prometer viabilidade comercial baseada só em quantidade de testes ou módulos.

**Interromper automação afetada:** ação indevida, duplicação de reserva, vazamento entre empresas, divergência que possa causar atendimento errado ou falha sem operador disponível. Preservar evidência, assumir atendimento humano e reconciliar antes de retomar. Detalhar controle por empresa/capacidade, sem desligar todos os tenants por conveniência.

## P10 — Evolução semântica e GraphRAG, depois do piloto

**Depende:** baseline de P09. A ontologia mínima e políticas já entram em P01; esta tarefa é a expansão experimental, não uma pendência que impeça o primeiro cliente.

Fontes: seção 8 do diagnóstico de 13/09 resume PDFs 2407.06564v1 e 2604.15951v1 e artigo CIO. Não carregar novamente os PDFs salvo dúvida metodológica. Grafo de workflow, grafo de conhecimento e modelo GNN são conceitos diferentes.

**Executar:** padronizar relações entre serviço/recurso/unidade/política e proveniência. Comparar A = RAG atual, B = domínio tipado + consultas estruturadas + RAG, C = B + recuperação seletiva em grafo, com mesmo modelo/orçamento e casos separados do desenvolvimento. Proposta inicial: cerca de 100 casos revisados, com FAQ, relações compostas, políticas, informação ausente/desatualizada e isolamento.

Medir acerto, fonte correta, abstenção, política, latência p95, custo e esforço de atualização. Avaliação humana nos casos críticos; LLM-juiz não é única evidência. Fatos transacionais continuam no banco; projeção em grafo precisa de versão, vigência, tenant e atualização/exclusão recuperáveis.

**Aceite:** decisão com evidência de adotar ou adiar; adotar só com ganho sobre B e sem regressão de segurança/custo inaceitável. Banco de grafos dedicado, agentes dinâmicos e treinamento conjunto precisam de justificativa separada. Resultado negativo do experimento é conclusão válida; não implementar complexidade apenas para marcar tarefa como feita.
