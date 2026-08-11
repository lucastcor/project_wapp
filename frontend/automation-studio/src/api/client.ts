/**
 * Cliente HTTP do Automation Studio.
 *
 * Regras que este módulo existe para garantir:
 *
 * 1. **Nenhum segredo no navegador.** A autenticação é o cookie HTTP-only de
 *    sessão, emitido pelo login da plataforma. Não há token em `localStorage`,
 *    em variável de build ou em memória — o JavaScript nunca vê a credencial.
 * 2. **Mesma origem.** Todas as chamadas são relativas. Sem CORS, sem host
 *    configurável: um host injetável seria um caminho para exfiltrar sessão.
 * 3. **O escopo de empresa vem do servidor.** `companyId` só é anexado quando
 *    a plataforma já resolveu um escopo para a credencial; o cliente nunca
 *    "escolhe" um tenant.
 * 4. **409 e 422 são dados, não exceções genéricas.** Conflito de rascunho e
 *    erro de validação carregam informação que a UI precisa mostrar.
 */

import type {
  AccessScope,
  ApprovalGate,
  CatalogResponse,
  DeadLetterEntry,
  DiffResult,
  ExecutionDetail,
  ExecutionStatus,
  ExecutionSummary,
  PublishResult,
  SimulationResponse,
  ValidationIssue,
  ValidationResult,
  VersionRecord,
  WorkflowDetail,
  WorkflowGraph,
  WorkflowSummary,
} from "./types"

const BASE = "/admin/automations"

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly detail?: unknown,
  ) {
    super(message)
    this.name = "ApiError"
  }
}

/** Rascunho salvo a partir de uma revisão que já não é a corrente. */
export class DraftConflictError extends ApiError {
  constructor(
    readonly expectedRevision: number,
    readonly currentRevision: number,
  ) {
    super(
      409,
      `O fluxo foi alterado por outra pessoa (revisão ${currentRevision}). ` +
        "Recarregue para não sobrescrever o trabalho dela.",
    )
    this.name = "DraftConflictError"
  }
}

/** Grafo recusado pelo servidor. Carrega os problemas localizados. */
export class GraphRejectedError extends ApiError {
  constructor(
    message: string,
    readonly issues: readonly ValidationIssue[],
  ) {
    super(422, message, issues)
    this.name = "GraphRejectedError"
  }
}

interface RequestOptions {
  readonly method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE"
  readonly body?: unknown
  readonly companyId?: number | null
  readonly signal?: AbortSignal
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const url = new URL(`${BASE}${path}`, window.location.origin)
  if (options.companyId != null) {
    url.searchParams.set("company_id", String(options.companyId))
  }

  const init: RequestInit = {
    method: options.method ?? "GET",
    // Mesma origem e cookie incluído: é assim que a sessão HTTP-only viaja.
    credentials: "same-origin",
    headers: { Accept: "application/json" },
    ...(options.signal ? { signal: options.signal } : {}),
  }
  if (options.body !== undefined) {
    init.headers = { ...init.headers, "Content-Type": "application/json" }
    init.body = JSON.stringify(options.body)
  }

  const response = await fetch(url.toString(), init)

  if (response.status === 204) {
    return undefined as T
  }

  const raw: unknown = await response.json().catch(() => null)

  if (response.ok) {
    return raw as T
  }

  const detail = isRecord(raw) ? raw["detail"] : null

  if (response.status === 409 && isRecord(detail)) {
    const expected = detail["expected_revision"]
    const current = detail["current_revision"]
    if (typeof expected === "number" && typeof current === "number") {
      throw new DraftConflictError(expected, current)
    }
  }

  if (response.status === 422 && isRecord(detail) && Array.isArray(detail["issues"])) {
    throw new GraphRejectedError(
      typeof detail["message"] === "string" ? detail["message"] : "O fluxo tem erros.",
      detail["issues"] as ValidationIssue[],
    )
  }

  if (response.status === 401) {
    throw new ApiError(401, "Sua sessão expirou. Entre novamente para continuar.", detail)
  }
  if (response.status === 403) {
    throw new ApiError(403, "Você não tem acesso a este recurso nesta empresa.", detail)
  }
  if (response.status === 404) {
    throw new ApiError(404, "Recurso não encontrado.", detail)
  }

  const message =
    typeof detail === "string"
      ? detail
      : `A requisição falhou (${response.status}).`
  throw new ApiError(response.status, message, detail)
}

export interface AutomationApi {
  readonly getAccessScope: () => Promise<AccessScope>
  readonly getCatalog: (companyId: number | null) => Promise<CatalogResponse>
  readonly listWorkflows: (
    companyId: number | null,
    options?: { readonly search?: string; readonly includeArchived?: boolean },
  ) => Promise<readonly WorkflowSummary[]>
  readonly createWorkflow: (
    companyId: number | null,
    payload: { readonly workflow_key: string; readonly name: string; readonly description?: string },
  ) => Promise<WorkflowDetail>
  readonly getWorkflow: (companyId: number | null, workflowId: number) => Promise<WorkflowDetail>
  readonly saveDraft: (
    companyId: number | null,
    workflowId: number,
    graph: WorkflowGraph,
    expectedRevision: number,
  ) => Promise<WorkflowDetail>
  readonly validateGraph: (
    companyId: number | null,
    graph: WorkflowGraph,
  ) => Promise<ValidationResult>
  readonly simulateGraph: (
    companyId: number | null,
    graph: WorkflowGraph,
    sampleContext: Record<string, unknown>,
    approvalDecisions: Record<string, "approved" | "rejected">,
  ) => Promise<SimulationResponse>
  readonly diffDraft: (companyId: number | null, workflowId: number) => Promise<DiffResult>
  readonly publish: (
    companyId: number | null,
    workflowId: number,
    notes?: string,
  ) => Promise<PublishResult>
  readonly listVersions: (
    companyId: number | null,
    workflowId: number,
  ) => Promise<readonly VersionRecord[]>
  readonly setState: (
    companyId: number | null,
    workflowId: number,
    state: "active" | "paused",
  ) => Promise<WorkflowDetail>
  readonly trigger: (
    companyId: number | null,
    workflowId: number,
    idempotencyKey: string,
    contactId?: string,
  ) => Promise<{ readonly emitted: boolean; readonly created?: boolean }>
  readonly listExecutions: (
    companyId: number | null,
    options?: { readonly workflowId?: number; readonly status?: ExecutionStatus },
  ) => Promise<readonly ExecutionSummary[]>
  readonly getExecution: (
    companyId: number | null,
    executionId: number,
  ) => Promise<ExecutionDetail>
  readonly retryExecution: (
    companyId: number | null,
    executionId: number,
  ) => Promise<{ readonly retried: boolean; readonly reason?: string }>
  readonly cancelExecution: (
    companyId: number | null,
    executionId: number,
  ) => Promise<{ readonly canceled: boolean; readonly reason?: string }>
  readonly listDeadLetter: (companyId: number | null) => Promise<readonly DeadLetterEntry[]>
  readonly listApprovals: (companyId: number | null) => Promise<readonly ApprovalGate[]>
  readonly claimApproval: (companyId: number | null, gateId: number) => Promise<ApprovalGate>
  readonly decideApproval: (
    companyId: number | null,
    gateId: number,
    decision: "approved" | "rejected",
    note?: string,
  ) => Promise<{ readonly decided: boolean; readonly resumed?: boolean; readonly reason?: string }>
}

export const api: AutomationApi = {
  // O escopo é a primeira coisa que o app pede: é a plataforma dizendo qual
  // empresa esta credencial pode operar. O cliente nunca decide isso.
  getAccessScope: async () => {
    const response = await fetch("/admin/access/scope", { credentials: "same-origin" })
    if (!response.ok) {
      throw new ApiError(
        response.status,
        response.status === 401
          ? "Sua sessão expirou. Entre novamente para continuar."
          : "Não foi possível resolver seu escopo de acesso.",
      )
    }
    return (await response.json()) as AccessScope
  },

  getCatalog: (companyId) => request<CatalogResponse>("/catalog", { companyId }),

  listWorkflows: (companyId, options) => {
    const params = new URLSearchParams()
    if (options?.search) params.set("search", options.search)
    if (options?.includeArchived) params.set("include_archived", "true")
    const query = params.toString()
    return request<readonly WorkflowSummary[]>(`/workflows${query ? `?${query}` : ""}`, {
      companyId,
    })
  },

  createWorkflow: (companyId, payload) =>
    request<WorkflowDetail>("/workflows", { method: "POST", body: payload, companyId }),

  getWorkflow: (companyId, workflowId) =>
    request<WorkflowDetail>(`/workflows/${workflowId}`, { companyId }),

  saveDraft: (companyId, workflowId, graph, expectedRevision) =>
    request<WorkflowDetail>(`/workflows/${workflowId}/draft`, {
      method: "PUT",
      body: { graph, expected_revision: expectedRevision },
      companyId,
    }),

  validateGraph: (companyId, graph) =>
    request<ValidationResult>("/workflows/validate", {
      method: "POST",
      body: { graph },
      companyId,
    }),

  simulateGraph: (companyId, graph, sampleContext, approvalDecisions) =>
    request<SimulationResponse>("/workflows/simulate", {
      method: "POST",
      body: {
        graph,
        sample_context: sampleContext,
        approval_decisions: approvalDecisions,
      },
      companyId,
    }),

  diffDraft: (companyId, workflowId) =>
    request<DiffResult>(`/workflows/${workflowId}/diff`, { companyId }),

  publish: (companyId, workflowId, notes) =>
    request<PublishResult>(`/workflows/${workflowId}/publish`, {
      method: "POST",
      body: notes ? { notes } : {},
      companyId,
    }),

  listVersions: (companyId, workflowId) =>
    request<readonly VersionRecord[]>(`/workflows/${workflowId}/versions`, { companyId }),

  setState: (companyId, workflowId, state) =>
    request<WorkflowDetail>(`/workflows/${workflowId}/state`, {
      method: "POST",
      body: { state },
      companyId,
    }),

  trigger: (companyId, workflowId, idempotencyKey, contactId) =>
    request(`/workflows/${workflowId}/trigger`, {
      method: "POST",
      body: {
        idempotency_key: idempotencyKey,
        ...(contactId ? { contact_id: contactId } : {}),
      },
      companyId,
    }),

  listExecutions: (companyId, options) => {
    const params = new URLSearchParams()
    if (options?.workflowId != null) params.set("workflow_id", String(options.workflowId))
    if (options?.status) params.set("status", options.status)
    const query = params.toString()
    return request<readonly ExecutionSummary[]>(`/executions${query ? `?${query}` : ""}`, {
      companyId,
    })
  },

  getExecution: (companyId, executionId) =>
    request<ExecutionDetail>(`/executions/${executionId}`, { companyId }),

  retryExecution: (companyId, executionId) =>
    request(`/executions/${executionId}/retry`, { method: "POST", companyId }),

  cancelExecution: (companyId, executionId) =>
    request(`/executions/${executionId}/cancel`, { method: "POST", companyId }),

  listDeadLetter: (companyId) => request<readonly DeadLetterEntry[]>("/dead-letter", { companyId }),

  listApprovals: (companyId) => request<readonly ApprovalGate[]>("/approvals", { companyId }),

  claimApproval: (companyId, gateId) =>
    request<ApprovalGate>(`/approvals/${gateId}/claim`, { method: "POST", companyId }),

  decideApproval: (companyId, gateId, decision, note) =>
    request(`/approvals/${gateId}/decide`, {
      method: "POST",
      body: { decision, ...(note ? { note } : {}) },
      companyId,
    }),
}
