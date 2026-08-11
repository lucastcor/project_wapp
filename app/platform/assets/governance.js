// Data Governance: knowledge, importação de conversas, curadoria de candidatos,
// versões de dataset, avaliações e ativação/rollback de modelo por empresa.
//
// Vive em módulo próprio para não aumentar ainda mais app.js. Recebe do host
// os helpers de render e de rede, então não duplica formatação nem sessão.

export const GOVERNANCE_VIEW_KEY = "governance"

export const governanceNavItem = {
  key: GOVERNANCE_VIEW_KEY,
  label: "Data Governance",
  caption: "Knowledge, curadoria, dataset e modelo",
  icon: "shield",
}

export function createGovernanceState() {
  return {
    tab: "knowledge",
    knowledgeDocuments: [],
    knowledgeSearch: { query: "", result: null },
    importBatches: [],
    importResult: null,
    candidates: [],
    datasetVersions: [],
    datasetReadiness: null,
    selectedDatasetVersionId: null,
    evaluationCases: [],
    evaluationRuns: [],
    evaluationComparison: null,
    promotionGate: null,
    models: null,
    channelBindings: [],
    drafts: {
      knowledge: {
        title: "",
        documentType: "general_faq",
        content: "",
        externalRef: "",
        validUntil: "",
        approve: false,
      },
      conversationImport: {
        batchKey: "",
        sourceFormat: "whatsapp_txt",
        conversationRef: "",
        agentAuthors: "",
        customerProfile: "",
        rawText: "",
        dryRun: true,
      },
      dataset: { name: "", seed: 42, validationRatio: 0.2, holdoutRatio: 0.1 },
      evaluation: { approach: "base_rag", defaultReply: "" },
    },
  }
}

const TABS = [
  { key: "knowledge", label: "Knowledge" },
  { key: "import", label: "Importação" },
  { key: "review", label: "Curadoria" },
  { key: "dataset", label: "Dataset" },
  { key: "evaluation", label: "Avaliação" },
  { key: "models", label: "Modelos" },
  { key: "channels", label: "Canais" },
]

const DOCUMENT_TYPES = [
  "general_faq",
  "business_rule",
  "discount_policy",
  "availability_rule",
  "schedule_rule",
  "product_info",
  "price",
  "procedure",
]

export async function loadGovernanceData(ctx) {
  const { state, apiRequest, companyParams } = ctx
  const params = companyParams()
  if (!params.company_id && !params.company_slug) {
    // Todas as rotas de governança exigem empresa resolvida.
    state.governance.knowledgeDocuments = []
    state.governance.importBatches = []
    state.governance.candidates = []
    state.governance.datasetVersions = []
    state.governance.evaluationRuns = []
    state.governance.models = null
    state.governance.channelBindings = []
    return
  }

  const tab = state.governance.tab
  const requests = {
    knowledge: () =>
      apiRequest("/admin/knowledge/documents", { params }).then((documents) => {
        state.governance.knowledgeDocuments = Array.isArray(documents) ? documents : []
      }),
    import: () =>
      apiRequest("/admin/conversation-import/batches", { params }).then((batches) => {
        state.governance.importBatches = Array.isArray(batches) ? batches : []
      }),
    review: () =>
      apiRequest("/admin/training-candidates", {
        params: { ...params, review_status: "pending" },
      }).then((candidates) => {
        state.governance.candidates = Array.isArray(candidates) ? candidates : []
      }),
    dataset: () =>
      apiRequest("/admin/dataset-versions", { params }).then((versions) => {
        state.governance.datasetVersions = Array.isArray(versions) ? versions : []
      }),
    evaluation: () =>
      Promise.all([
        apiRequest("/admin/evaluation/runs", { params }),
        apiRequest("/admin/evaluation/cases", { params }),
        apiRequest("/admin/evaluation/promotion-gate", { params }),
      ]).then(([runs, cases, gate]) => {
        state.governance.evaluationRuns = Array.isArray(runs) ? runs : []
        state.governance.evaluationCases = Array.isArray(cases) ? cases : []
        state.governance.promotionGate = gate || null
      }),
    models: () =>
      apiRequest("/admin/models", { params }).then((models) => {
        state.governance.models = models || null
      }),
    channels: () =>
      apiRequest("/admin/channel-bindings", { params }).then((bindings) => {
        state.governance.channelBindings = Array.isArray(bindings) ? bindings : []
      }),
  }

  await (requests[tab] || requests.knowledge)()
}

function renderTabs(activeTab, escapeHtml) {
  return `
    <div class="quick-row" id="governance-tabs">
      ${TABS.map(
        (tab) => `
          <button
            class="${tab.key === activeTab ? "button" : "button-ghost"}"
            data-governance-tab="${escapeHtml(tab.key)}"
            type="button"
          >${escapeHtml(tab.label)}</button>
        `
      ).join("")}
    </div>
  `
}

function renderKnowledgeTab(ctx) {
  const { state, escapeHtml } = ctx
  const draft = state.governance.drafts.knowledge
  const documents = state.governance.knowledgeDocuments
  const search = state.governance.knowledgeSearch

  return `
    <section class="grid-two">
      <article class="card">
        <div class="section-header">
          <div>
            <h3 class="section-title">Novo documento de conhecimento</h3>
            <p class="section-copy">
              Fato mutável vive aqui, não no modelo. O documento só é recuperável em runtime depois de aprovado e dentro da vigência.
            </p>
          </div>
        </div>
        <form id="knowledge-form" class="form-grid">
          <div class="form-grid-two">
            <div class="field">
              <label>Título</label>
              <input name="title" value="${escapeHtml(draft.title)}" required />
            </div>
            <div class="field">
              <label>Tipo</label>
              <select name="document_type">
                ${DOCUMENT_TYPES.map(
                  (type) =>
                    `<option value="${type}" ${draft.documentType === type ? "selected" : ""}>${type}</option>`
                ).join("")}
              </select>
            </div>
          </div>
          <div class="form-grid-two">
            <div class="field">
              <label>Referência externa (idempotência)</label>
              <input name="external_ref" value="${escapeHtml(draft.externalRef)}" placeholder="tabela-precos-2026" />
            </div>
            <div class="field">
              <label>Válido até</label>
              <input name="valid_until" type="date" value="${escapeHtml(draft.validUntil)}" />
            </div>
          </div>
          <div class="field">
            <label>Conteúdo</label>
            <textarea name="content" required placeholder="Regra, política, preço ou procedimento">${escapeHtml(draft.content)}</textarea>
          </div>
          <div class="quick-row">
            <label class="meta-chip"><input name="approve" type="checkbox" ${draft.approve ? "checked" : ""} /> Aprovar imediatamente</label>
          </div>
          <div class="toolbar-row">
            <button class="button" type="submit">Salvar documento</button>
            <button class="button-ghost" type="button" id="import-legacy-knowledge">Importar knowledge legado</button>
          </div>
        </form>
      </article>

      <article class="card">
        <div class="section-header">
          <div>
            <h3 class="section-title">Testar recuperação</h3>
            <p class="section-copy">Veja exatamente quais fontes o bot usaria para uma pergunta real.</p>
          </div>
        </div>
        <form id="knowledge-search-form" class="form-grid">
          <div class="field">
            <label>Pergunta do cliente</label>
            <input name="query" value="${escapeHtml(search.query)}" placeholder="Qual o horário de atendimento?" required />
          </div>
          <button class="button" type="submit">Buscar fontes</button>
        </form>
        ${
          search.result
            ? `
              <div class="job-stack" style="margin-top: 16px;">
                <article class="job-card">
                  <strong>${search.result.has_relevant_source ? "Fonte relevante encontrada" : "Nenhuma fonte relevante"}</strong>
                  <p>Modo: ${escapeHtml(search.result.mode || "—")} · candidatos avaliados: ${escapeHtml(String(search.result.candidates_considered ?? 0))}</p>
                </article>
                ${(search.result.chunks || [])
                  .map(
                    (chunk) => `
                      <article class="job-card">
                        <strong>[fonte:${escapeHtml(String(chunk.chunk_id))}] ${escapeHtml(chunk.document_title || "")}</strong>
                        <p>Score: ${escapeHtml(String(chunk.score))} · Tipo: ${escapeHtml(chunk.document_type || "")}${chunk.injection_flagged ? " · ⚠ conteúdo suspeito neutralizado" : ""}</p>
                        <p>${escapeHtml((chunk.content || "").slice(0, 240))}</p>
                      </article>
                    `
                  )
                  .join("")}
              </div>
            `
            : ""
        }
      </article>
    </section>

    <section class="card">
      <div class="section-header">
        <div>
          <h3 class="section-title">Documentos da empresa</h3>
          <p class="section-copy">Somente documentos aprovados e vigentes chegam ao runtime.</p>
        </div>
      </div>
      <div class="example-stack">
        ${
          documents.length
            ? documents
                .map(
                  (document) => `
                    <article class="example-card">
                      <strong>${escapeHtml(document.title || "")}</strong>
                      <p>
                        Tipo: ${escapeHtml(document.document_type || "")} ·
                        Status: ${escapeHtml(document.status || "")} ·
                        Prioridade: ${escapeHtml(String(document.priority ?? 0))}
                      </p>
                      <div class="quick-row">
                        ${
                          document.status !== "approved"
                            ? `<button class="button-ghost" data-approve-document="${escapeHtml(String(document.id))}" type="button">Aprovar</button>`
                            : ""
                        }
                        ${
                          document.status !== "archived"
                            ? `<button class="button-ghost" data-archive-document="${escapeHtml(String(document.id))}" type="button">Arquivar</button>`
                            : ""
                        }
                      </div>
                    </article>
                  `
                )
                .join("")
            : '<div class="empty-state">Nenhum documento cadastrado.</div>'
        }
      </div>
    </section>
  `
}

function renderImportTab(ctx) {
  const { state, escapeHtml, formatNumber } = ctx
  const draft = state.governance.drafts.conversationImport
  const result = state.governance.importResult

  return `
    <section class="card">
      <div class="section-header">
        <div>
          <h3 class="section-title">Importar conversas brutas</h3>
          <p class="section-copy">
            O conteúdo é anonimizado antes de qualquer escrita e nunca vira treino sem revisão humana. Rode em dry-run primeiro.
          </p>
        </div>
      </div>
      <form id="conversation-import-form" class="form-grid">
        <div class="form-grid-three">
          <div class="field">
            <label>Chave do lote (idempotência)</label>
            <input name="batch_key" value="${escapeHtml(draft.batchKey)}" required placeholder="export-2026-08" />
          </div>
          <div class="field">
            <label>Formato</label>
            <select name="source_format">
              <option value="whatsapp_txt" ${draft.sourceFormat === "whatsapp_txt" ? "selected" : ""}>WhatsApp TXT</option>
              <option value="csv" ${draft.sourceFormat === "csv" ? "selected" : ""}>CSV</option>
              <option value="json" ${draft.sourceFormat === "json" ? "selected" : ""}>JSON</option>
            </select>
          </div>
          <div class="field">
            <label>Perfil do cliente</label>
            <input name="customer_profile" value="${escapeHtml(draft.customerProfile)}" placeholder="new_customer" />
          </div>
        </div>
        <div class="form-grid-two">
          <div class="field">
            <label>Referência da conversa</label>
            <input name="conversation_ref" value="${escapeHtml(draft.conversationRef)}" placeholder="whatsapp-export" />
          </div>
          <div class="field">
            <label>Autores do atendimento (separados por vírgula)</label>
            <input name="agent_authors" value="${escapeHtml(draft.agentAuthors)}" placeholder="Atendimento, Recepção" />
          </div>
        </div>
        <div class="field">
          <label>Conteúdo bruto</label>
          <textarea name="raw_text" required placeholder="Cole aqui o export do WhatsApp, CSV ou JSON">${escapeHtml(draft.rawText)}</textarea>
        </div>
        <div class="quick-row">
          <label class="meta-chip"><input name="dry_run" type="checkbox" ${draft.dryRun ? "checked" : ""} /> Dry run</label>
        </div>
        <button class="button" type="submit">Executar importação</button>
      </form>
    </section>

    ${
      result
        ? `
          <section class="card">
            <div class="section-header">
              <div>
                <h3 class="section-title">Resultado do lote ${escapeHtml(result.batch_key || "")}</h3>
                <p class="section-copy">${result.dry_run ? "Dry-run: nada foi persistido." : "Lote persistido em staging."}</p>
              </div>
            </div>
            <div class="grid-three">
              ${ctx.renderMetricCard("Conversas", formatNumber(result.stats?.conversations || 0), "Sessões segmentadas por inatividade.")}
              ${ctx.renderMetricCard("Candidatos", formatNumber(result.stats?.candidates || 0), "Pares multi-turno extraídos.")}
              ${ctx.renderMetricCard("Linhas inválidas", formatNumber(result.stats?.invalid_records || 0), "Ignoradas sem derrubar o lote.", "warm")}
            </div>
            <div class="job-stack" style="margin-top: 16px;">
              <article class="job-card">
                <strong>PII substituída</strong>
                <p>${escapeHtml(JSON.stringify(result.stats?.pii_replacements || {}))} · política: ${escapeHtml(result.stats?.pii_policy || "standard")}</p>
              </article>
            </div>
          </section>
        `
        : ""
    }

    <section class="card">
      <div class="section-header">
        <div>
          <h3 class="section-title">Lotes importados</h3>
          <p class="section-copy">Reimportar a mesma chave substitui o staging em vez de duplicar.</p>
        </div>
      </div>
      <div class="example-stack">
        ${
          state.governance.importBatches.length
            ? state.governance.importBatches
                .map(
                  (batch) => `
                    <article class="example-card">
                      <strong>${escapeHtml(batch.batch_key || "")}</strong>
                      <p>Formato: ${escapeHtml(batch.source_format || "")} · Status: ${escapeHtml(batch.status || "")}</p>
                    </article>
                  `
                )
                .join("")
            : '<div class="empty-state">Nenhum lote importado ainda.</div>'
        }
      </div>
    </section>
  `
}

function renderReviewTab(ctx) {
  const { state, escapeHtml } = ctx
  const candidates = state.governance.candidates

  return `
    <section class="card">
      <div class="section-header">
        <div>
          <h3 class="section-title">Candidatos pendentes de revisão</h3>
          <p class="section-copy">
            Nada entra no dataset sem aprovação explícita. Aprovar promove o par para message_examples com lineage até a conversa de origem.
          </p>
        </div>
      </div>
      <div class="example-stack">
        ${
          candidates.length
            ? candidates
                .map(
                  (candidate) => `
                    <article class="example-card" data-candidate-card="${escapeHtml(String(candidate.id))}">
                      <strong>Cliente: ${escapeHtml((candidate.customer_message || "").slice(0, 180))}</strong>
                      <p>Atendente: ${escapeHtml((candidate.approved_reply || "").slice(0, 240))}</p>
                      <div class="form-grid-three">
                        <div class="field">
                          <label>Cenário</label>
                          <input data-candidate-scenario="${escapeHtml(String(candidate.id))}" value="${escapeHtml(candidate.scenario_type || "general")}" />
                        </div>
                        <div class="field">
                          <label>Perfil do cliente</label>
                          <input data-candidate-profile="${escapeHtml(String(candidate.id))}" value="${escapeHtml(candidate.customer_profile || "")}" />
                        </div>
                        <div class="field">
                          <label>Qualidade</label>
                          <input data-candidate-quality="${escapeHtml(String(candidate.id))}" type="number" min="0" max="10" step="0.5" value="9" />
                        </div>
                      </div>
                      <div class="quick-row">
                        <button class="button" data-approve-candidate="${escapeHtml(String(candidate.id))}" type="button">Aprovar</button>
                        <button class="button-ghost" data-reject-candidate="${escapeHtml(String(candidate.id))}" type="button">Rejeitar</button>
                      </div>
                    </article>
                  `
                )
                .join("")
            : '<div class="empty-state">Nenhum candidato pendente.</div>'
        }
      </div>
    </section>
  `
}

function renderDatasetTab(ctx) {
  const { state, escapeHtml, formatNumber } = ctx
  const draft = state.governance.drafts.dataset
  const readiness = state.governance.datasetReadiness

  return `
    <section class="grid-two">
      <article class="card">
        <div class="section-header">
          <div>
            <h3 class="section-title">Nova versão de dataset</h3>
            <p class="section-copy">Split agrupado por conversa, deduplicação exata e near-duplicate, hash reproduzível.</p>
          </div>
        </div>
        <form id="dataset-version-form" class="form-grid">
          <div class="form-grid-two">
            <div class="field">
              <label>Nome</label>
              <input name="name" value="${escapeHtml(draft.name)}" placeholder="baseline-2026-08" />
            </div>
            <div class="field">
              <label>Seed</label>
              <input name="seed" type="number" value="${escapeHtml(String(draft.seed))}" />
            </div>
          </div>
          <div class="form-grid-two">
            <div class="field">
              <label>Validation ratio</label>
              <input name="validation_ratio" type="number" step="0.05" min="0" max="0.5" value="${escapeHtml(String(draft.validationRatio))}" />
            </div>
            <div class="field">
              <label>Holdout ratio</label>
              <input name="holdout_ratio" type="number" step="0.05" min="0" max="0.5" value="${escapeHtml(String(draft.holdoutRatio))}" />
            </div>
          </div>
          <button class="button" type="submit">Gerar versão</button>
        </form>
      </article>

      <article class="card">
        <div class="section-header">
          <div>
            <h3 class="section-title">Readiness da versão selecionada</h3>
            <p class="section-copy">O job de fine-tuning é bloqueado enquanto houver falha bloqueante.</p>
          </div>
        </div>
        ${
          readiness
            ? `
              <div class="job-stack">
                <article class="job-card">
                  <strong>${readiness.is_ready ? "Pronto para fine-tuning" : "Bloqueado"}</strong>
                  <p>Train: ${formatNumber(readiness.train_items || 0)} · Validation: ${formatNumber(readiness.validation_items || 0)}</p>
                </article>
                ${(readiness.checks || [])
                  .map(
                    (check) => `
                      <article class="job-card">
                        <strong>${check.status === "pass" ? "✓" : "✗"} ${escapeHtml(check.code)}</strong>
                        <p>${escapeHtml(check.detail || "")}</p>
                      </article>
                    `
                  )
                  .join("")}
              </div>
            `
            : '<div class="empty-state">Selecione uma versão para ver o readiness.</div>'
        }
      </article>
    </section>

    <section class="card">
      <div class="section-header">
        <div>
          <h3 class="section-title">Versões</h3>
          <p class="section-copy">Uma versão congelada é imutável e vira a única origem aceita pelo job.</p>
        </div>
      </div>
      <div class="example-stack">
        ${
          state.governance.datasetVersions.length
            ? state.governance.datasetVersions
                .map(
                  (version) => `
                    <article class="example-card">
                      <strong>v${escapeHtml(String(version.version))} · ${escapeHtml(version.status || "")}</strong>
                      <p>
                        Itens: ${formatNumber(version.stats?.total_items || 0)} ·
                        Grupos: ${formatNumber(version.stats?.distinct_groups || 0)} ·
                        Hash: ${escapeHtml(String(version.content_hash || "").slice(0, 12))}
                      </p>
                      <p>Distribuição: ${escapeHtml(JSON.stringify(version.stats?.split_distribution || {}))}</p>
                      <div class="quick-row">
                        <button class="button-ghost" data-dataset-readiness="${escapeHtml(String(version.id))}" type="button">Ver readiness</button>
                        ${
                          version.status !== "frozen" && version.status !== "superseded"
                            ? `<button class="button-ghost" data-freeze-dataset="${escapeHtml(String(version.id))}" type="button">Congelar</button>`
                            : ""
                        }
                        ${
                          version.status === "frozen"
                            ? `<button class="button" data-create-job="${escapeHtml(String(version.id))}" type="button">Criar job de fine-tuning</button>`
                            : ""
                        }
                      </div>
                    </article>
                  `
                )
                .join("")
            : '<div class="empty-state">Nenhuma versão de dataset gerada.</div>'
        }
      </div>
    </section>
  `
}

function renderEvaluationTab(ctx) {
  const { state, escapeHtml } = ctx
  const draft = state.governance.drafts.evaluation
  const gate = state.governance.promotionGate
  const comparison = state.governance.evaluationComparison

  return `
    <section class="grid-two">
      <article class="card">
        <div class="section-header">
          <div>
            <h3 class="section-title">Executar avaliação</h3>
            <p class="section-copy">
              Execução offline determinística: compara base, base+RAG e fine-tuned sem chamar provider externo.
            </p>
          </div>
        </div>
        <form id="evaluation-run-form" class="form-grid">
          <div class="field">
            <label>Abordagem</label>
            <select name="approach">
              <option value="base" ${draft.approach === "base" ? "selected" : ""}>base</option>
              <option value="base_rag" ${draft.approach === "base_rag" ? "selected" : ""}>base + RAG</option>
              <option value="finetuned" ${draft.approach === "finetuned" ? "selected" : ""}>fine-tuned</option>
            </select>
          </div>
          <div class="field">
            <label>Resposta padrão do gerador offline</label>
            <textarea name="default_reply" placeholder="Deixe em branco para usar o gerador padrão">${escapeHtml(draft.defaultReply)}</textarea>
          </div>
          <button class="button" type="submit">Rodar avaliação</button>
        </form>
        <p class="section-copy" style="margin-top: 12px;">
          Casos registrados: ${escapeHtml(String(state.governance.evaluationCases.length))}
        </p>
      </article>

      <article class="card">
        <div class="section-header">
          <div>
            <h3 class="section-title">Gate de promoção</h3>
            <p class="section-copy">Impede ativar um modelo que regride contra o baseline.</p>
          </div>
        </div>
        ${
          gate
            ? `
              <div class="job-stack">
                <article class="job-card">
                  <strong>${gate.passed ? "Liberado" : "Bloqueado"}</strong>
                  <p>Fine-tuned: ${escapeHtml(String(gate.finetuned_pass_rate ?? 0))} · Baseline: ${escapeHtml(String(gate.baseline_pass_rate ?? 0))}</p>
                  <p>${escapeHtml((gate.reasons || []).join(", ") || "Sem impedimentos.")}</p>
                </article>
              </div>
            `
            : '<div class="empty-state">Nenhuma avaliação executada.</div>'
        }
      </article>
    </section>

    <section class="card">
      <div class="section-header">
        <div>
          <h3 class="section-title">Execuções</h3>
          <p class="section-copy">Selecione duas ou mais para comparar.</p>
        </div>
        <button class="button-ghost" id="compare-evaluation-runs" type="button">Comparar selecionadas</button>
      </div>
      <div class="example-stack">
        ${
          state.governance.evaluationRuns.length
            ? state.governance.evaluationRuns
                .map(
                  (run) => `
                    <article class="example-card">
                      <label class="meta-chip">
                        <input type="checkbox" data-evaluation-run="${escapeHtml(String(run.id))}" />
                        ${escapeHtml(run.approach || "")} · pass rate ${escapeHtml(String(run.scores?.pass_rate ?? 0))}
                      </label>
                      <p>Modelo: ${escapeHtml(run.model_ref || "base")} · Casos: ${escapeHtml(String(run.total_cases ?? 0))}</p>
                      <p>Dimensões: ${escapeHtml(JSON.stringify(run.scores?.dimensions || {}))}</p>
                    </article>
                  `
                )
                .join("")
            : '<div class="empty-state">Nenhuma execução registrada.</div>'
        }
      </div>
      ${
        comparison
          ? `
            <div class="job-stack" style="margin-top: 16px;">
              ${comparison.runs
                .map(
                  (run) => `
                    <article class="job-card">
                      <strong>${escapeHtml(run.approach)} (run ${escapeHtml(String(run.run_id))})</strong>
                      <p>Pass rate: ${escapeHtml(String(run.pass_rate))} · Delta vs baseline: ${escapeHtml(String(run.delta_vs_baseline))}</p>
                    </article>
                  `
                )
                .join("")}
            </div>
          `
          : ""
      }
    </section>
  `
}

function renderModelsTab(ctx) {
  const { state, escapeHtml } = ctx
  const models = state.governance.models

  return `
    <section class="card">
      <div class="section-header">
        <div>
          <h3 class="section-title">Modelos desta empresa</h3>
          <p class="section-copy">
            Cada empresa escolhe seu modelo. Ativar aposenta o anterior; rollback restaura o anterior ou cai no modelo base.
          </p>
        </div>
        <button class="button-ghost" id="rollback-model" type="button">Rollback</button>
      </div>
      ${
        models
          ? `
            <div class="job-stack">
              <article class="job-card">
                <strong>Modelo efetivo: ${escapeHtml(models.effective_model_ref || "—")}</strong>
                <p>Origem: ${escapeHtml(models.effective_model_status || "—")}</p>
              </article>
              ${
                (models.models || []).length
                  ? models.models
                      .map(
                        (model) => `
                          <article class="job-card">
                            <strong>${escapeHtml(model.model_ref || "")} · ${escapeHtml(model.status || "")}</strong>
                            <p>Dataset: ${escapeHtml(String(model.dataset_version_id ?? "—"))} · Avaliação: ${escapeHtml(String(model.evaluation_run_id ?? "—"))}</p>
                            ${
                              model.status !== "active"
                                ? `<button class="button-ghost" data-activate-model="${escapeHtml(String(model.id))}" type="button">Ativar</button>`
                                : ""
                            }
                          </article>
                        `
                      )
                      .join("")
                  : '<div class="empty-state">Nenhum modelo registrado.</div>'
              }
            </div>
          `
          : '<div class="empty-state">Selecione uma empresa.</div>'
      }
    </section>
  `
}

function renderChannelsTab(ctx) {
  const { state, escapeHtml } = ctx

  return `
    <section class="card">
      <div class="section-header">
        <div>
          <h3 class="section-title">Vínculos de canal</h3>
          <p class="section-copy">
            Em produção o tenant vem daqui, não do que o chamador envia. Cadastre o phone_number_id de cada número do provider.
          </p>
        </div>
      </div>
      <form id="channel-binding-form" class="form-grid">
        <div class="form-grid-three">
          <div class="field">
            <label>Provider</label>
            <input name="provider" value="whatsapp" />
          </div>
          <div class="field">
            <label>Identificador externo</label>
            <input name="external_id" required placeholder="phone_number_id do Meta" />
          </div>
          <div class="field">
            <label>Rótulo</label>
            <input name="label" placeholder="Número principal" />
          </div>
        </div>
        <button class="button" type="submit">Vincular canal</button>
      </form>
      <div class="example-stack" style="margin-top: 16px;">
        ${
          state.governance.channelBindings.length
            ? state.governance.channelBindings
                .map(
                  (binding) => `
                    <article class="example-card">
                      <strong>${escapeHtml(binding.provider || "")} · ${escapeHtml(binding.external_id || "")}</strong>
                      <p>${escapeHtml(binding.label || "—")} · ${Number(binding.active) ? "ativo" : "inativo"}</p>
                      ${
                        Number(binding.active)
                          ? `<button class="button-ghost" data-deactivate-binding="${escapeHtml(String(binding.id))}" type="button">Desativar</button>`
                          : ""
                      }
                    </article>
                  `
                )
                .join("")
            : '<div class="empty-state">Nenhum canal vinculado. O webhook será recusado em produção.</div>'
        }
      </div>
    </section>
  `
}

const TAB_RENDERERS = {
  knowledge: renderKnowledgeTab,
  import: renderImportTab,
  review: renderReviewTab,
  dataset: renderDatasetTab,
  evaluation: renderEvaluationTab,
  models: renderModelsTab,
  channels: renderChannelsTab,
}

export function renderGovernanceView(ctx) {
  const { state, escapeHtml } = ctx
  const renderer = TAB_RENDERERS[state.governance.tab] || renderKnowledgeTab

  return `
    <section class="hero">
      <span class="brand-eyebrow">Data Governance</span>
      <h2 class="hero-title">Fato vem de fonte. Estilo vem de exemplo. Nada vira treino sem revisão.</h2>
      <p class="hero-copy">
        Aqui você governa o que o bot sabe, o que ele aprendeu e qual modelo cada empresa usa — com trilha de auditoria em cada mutação.
      </p>
    </section>

    ${renderTabs(state.governance.tab, escapeHtml)}
    ${renderer(ctx)}
  `
}

export function bindGovernanceEvents(ctx) {
  const { state, apiRequest, companyParams, pushToast, reload } = ctx
  const params = () => companyParams()

  document.querySelectorAll("[data-governance-tab]").forEach((button) => {
    button.addEventListener("click", async () => {
      state.governance.tab = button.dataset.governanceTab
      await reload()
    })
  })

  const runAction = async (action, successMessage) => {
    try {
      await action()
      if (successMessage) pushToast(successMessage)
      await reload()
    } catch (error) {
      pushToast(ctx.summarizeError(error), "error")
    }
  }

  const knowledgeForm = document.getElementById("knowledge-form")
  if (knowledgeForm) {
    knowledgeForm.addEventListener("submit", async (event) => {
      event.preventDefault()
      const data = new FormData(knowledgeForm)
      await runAction(
        () =>
          apiRequest("/admin/knowledge/documents", {
            method: "POST",
            params: params(),
            body: {
              title: String(data.get("title") || ""),
              content: String(data.get("content") || ""),
              document_type: String(data.get("document_type") || "general_faq"),
              external_ref: String(data.get("external_ref") || "") || null,
              valid_until: String(data.get("valid_until") || "") || null,
              approve: data.get("approve") === "on",
            },
          }),
        "Documento salvo."
      )
    })
  }

  const searchForm = document.getElementById("knowledge-search-form")
  if (searchForm) {
    searchForm.addEventListener("submit", async (event) => {
      event.preventDefault()
      const query = String(new FormData(searchForm).get("query") || "")
      state.governance.knowledgeSearch.query = query
      await runAction(async () => {
        state.governance.knowledgeSearch.result = await apiRequest("/admin/knowledge/search", {
          method: "POST",
          params: params(),
          body: { query },
        })
      })
    })
  }

  document.getElementById("import-legacy-knowledge")?.addEventListener("click", () =>
    runAction(
      () =>
        apiRequest("/admin/knowledge/import-legacy", {
          method: "POST",
          params: params(),
        }),
      "Knowledge legado importado."
    )
  )

  document.querySelectorAll("[data-approve-document]").forEach((button) => {
    button.addEventListener("click", () =>
      runAction(
        () =>
          apiRequest(`/admin/knowledge/documents/${button.dataset.approveDocument}/approve`, {
            method: "POST",
            params: params(),
          }),
        "Documento aprovado."
      )
    )
  })

  document.querySelectorAll("[data-archive-document]").forEach((button) => {
    button.addEventListener("click", () =>
      runAction(
        () =>
          apiRequest(`/admin/knowledge/documents/${button.dataset.archiveDocument}/archive`, {
            method: "POST",
            params: params(),
          }),
        "Documento arquivado."
      )
    )
  })

  const importForm = document.getElementById("conversation-import-form")
  if (importForm) {
    importForm.addEventListener("submit", async (event) => {
      event.preventDefault()
      const data = new FormData(importForm)
      const agentAuthors = String(data.get("agent_authors") || "")
        .split(",")
        .map((value) => value.trim())
        .filter(Boolean)

      await runAction(async () => {
        state.governance.importResult = await apiRequest("/admin/conversation-import", {
          method: "POST",
          params: params(),
          body: {
            batch_key: String(data.get("batch_key") || ""),
            source_format: String(data.get("source_format") || "whatsapp_txt"),
            raw_text: String(data.get("raw_text") || ""),
            conversation_ref: String(data.get("conversation_ref") || "") || "whatsapp-export",
            agent_authors: agentAuthors,
            customer_profile: String(data.get("customer_profile") || "") || null,
            dry_run: data.get("dry_run") === "on",
          },
        })
      }, "Importação executada.")
    })
  }

  const reviewCandidate = (candidateId, approved) => {
    const scenario = document.querySelector(`[data-candidate-scenario="${candidateId}"]`)?.value
    const profile = document.querySelector(`[data-candidate-profile="${candidateId}"]`)?.value
    const quality = document.querySelector(`[data-candidate-quality="${candidateId}"]`)?.value

    return runAction(
      () =>
        apiRequest(`/admin/training-candidates/${candidateId}/review`, {
          method: "POST",
          params: params(),
          body: {
            approved,
            scenario_type: scenario || null,
            customer_profile: profile || null,
            quality_score: quality ? Number(quality) : null,
          },
        }),
      approved ? "Candidato aprovado e promovido." : "Candidato rejeitado."
    )
  }

  document.querySelectorAll("[data-approve-candidate]").forEach((button) => {
    button.addEventListener("click", () => reviewCandidate(button.dataset.approveCandidate, true))
  })
  document.querySelectorAll("[data-reject-candidate]").forEach((button) => {
    button.addEventListener("click", () => reviewCandidate(button.dataset.rejectCandidate, false))
  })

  const datasetForm = document.getElementById("dataset-version-form")
  if (datasetForm) {
    datasetForm.addEventListener("submit", async (event) => {
      event.preventDefault()
      const data = new FormData(datasetForm)
      await runAction(
        () =>
          apiRequest("/admin/dataset-versions", {
            method: "POST",
            params: params(),
            body: {
              name: String(data.get("name") || "") || null,
              seed: Number(data.get("seed") || 42),
              validation_ratio: Number(data.get("validation_ratio") || 0.2),
              holdout_ratio: Number(data.get("holdout_ratio") || 0.1),
            },
          }),
        "Versão de dataset gerada."
      )
    })
  }

  document.querySelectorAll("[data-dataset-readiness]").forEach((button) => {
    button.addEventListener("click", () =>
      runAction(async () => {
        const versionId = button.dataset.datasetReadiness
        state.governance.selectedDatasetVersionId = versionId
        state.governance.datasetReadiness = await apiRequest(
          `/admin/dataset-versions/${versionId}/readiness`,
          { params: params() }
        )
      })
    )
  })

  document.querySelectorAll("[data-freeze-dataset]").forEach((button) => {
    button.addEventListener("click", () =>
      runAction(
        () =>
          apiRequest(`/admin/dataset-versions/${button.dataset.freezeDataset}/freeze`, {
            method: "POST",
            params: params(),
            body: { confirm: true },
          }),
        "Versão congelada."
      )
    )
  })

  document.querySelectorAll("[data-create-job]").forEach((button) => {
    button.addEventListener("click", () =>
      runAction(
        () =>
          apiRequest("/admin/finetuning/jobs", {
            method: "POST",
            params: params(),
            body: {
              dataset_version_id: Number(button.dataset.createJob),
              require_evaluation: true,
            },
          }),
        "Job de fine-tuning criado."
      )
    )
  })

  const evaluationForm = document.getElementById("evaluation-run-form")
  if (evaluationForm) {
    evaluationForm.addEventListener("submit", async (event) => {
      event.preventDefault()
      const data = new FormData(evaluationForm)
      await runAction(
        () =>
          apiRequest("/admin/evaluation/runs", {
            method: "POST",
            params: params(),
            body: {
              approach: String(data.get("approach") || "base_rag"),
              default_reply: String(data.get("default_reply") || "") || null,
            },
          }),
        "Avaliação executada."
      )
    })
  }

  document.getElementById("compare-evaluation-runs")?.addEventListener("click", () => {
    const runIds = Array.from(document.querySelectorAll("[data-evaluation-run]"))
      .filter((input) => input.checked)
      .map((input) => Number(input.dataset.evaluationRun))

    if (runIds.length < 2) {
      pushToast("Selecione ao menos duas execuções.", "error")
      return
    }

    return runAction(async () => {
      state.governance.evaluationComparison = await apiRequest("/admin/evaluation/compare", {
        method: "POST",
        params: params(),
        body: { run_ids: runIds },
      })
    })
  })

  document.querySelectorAll("[data-activate-model]").forEach((button) => {
    button.addEventListener("click", () =>
      runAction(
        () =>
          apiRequest(`/admin/models/${button.dataset.activateModel}/activate`, {
            method: "POST",
            params: params(),
            body: { require_evaluation: true },
          }),
        "Modelo ativado."
      )
    )
  })

  document.getElementById("rollback-model")?.addEventListener("click", () =>
    runAction(
      () => apiRequest("/admin/models/rollback", { method: "POST", params: params() }),
      "Rollback aplicado."
    )
  )

  const bindingForm = document.getElementById("channel-binding-form")
  if (bindingForm) {
    bindingForm.addEventListener("submit", async (event) => {
      event.preventDefault()
      const data = new FormData(bindingForm)
      await runAction(
        () =>
          apiRequest("/admin/channel-bindings", {
            method: "POST",
            params: params(),
            body: {
              provider: String(data.get("provider") || "whatsapp"),
              external_id: String(data.get("external_id") || ""),
              label: String(data.get("label") || "") || null,
            },
          }),
        "Canal vinculado."
      )
    })
  }

  document.querySelectorAll("[data-deactivate-binding]").forEach((button) => {
    button.addEventListener("click", () =>
      runAction(
        () =>
          apiRequest(`/admin/channel-bindings/${button.dataset.deactivateBinding}`, {
            method: "DELETE",
            params: params(),
          }),
        "Canal desativado."
      )
    )
  })
}
