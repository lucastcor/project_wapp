/**
 * Contratos da API do Automation Studio.
 *
 * Espelham exatamente o que `app/api/automation_routes.py` devolve. O
 * navegador **não** decide nada de comportamento a partir daqui: ele desenha
 * o que o backend já validou e autorizou. Em particular:
 *
 * - o catálogo de nós vem do servidor, não de uma cópia local;
 * - a validação exibida é a do servidor; a local só antecipa o feedback;
 * - `company_id` nunca é escolhido pelo cliente fora do escopo autorizado —
 *   ele vem do que a plataforma resolveu para a credencial.
 */

export type NodeCategory =
  | "trigger"
  | "condition"
  | "ai"
  | "time"
  | "control"
  | "action"
  | "terminal"

export type FieldType = "string" | "text" | "number" | "boolean" | "select" | "multiselect"

export interface FieldSpec {
  readonly key: string
  readonly label: string
  readonly type: FieldType
  readonly required: boolean
  readonly options: readonly string[]
  readonly min_value: number | null
  readonly max_value: number | null
  readonly max_length: number | null
  readonly default: unknown
  readonly help_text: string
}

export interface NodeSpec {
  readonly node_type: string
  readonly category: NodeCategory
  readonly label: string
  readonly description: string
  readonly outputs: readonly string[]
  readonly fields: readonly FieldSpec[]
  readonly required_capability: string | null
  readonly produces_effect: boolean
  readonly suspends: boolean
  readonly accepts_input: boolean
  readonly is_terminal: boolean
}

export interface CatalogResponse {
  readonly schema_version: string
  readonly nodes: readonly NodeSpec[]
  readonly engine_enabled: boolean
  readonly worker_enabled: boolean
  readonly limits: {
    readonly max_steps_per_execution: number
    readonly max_attempts: number
    readonly max_event_bytes: number
  }
}

export type ConfigValue = string | number | boolean | readonly string[] | null

export interface GraphNode {
  readonly id: string
  readonly node_type: string
  readonly position: { readonly x: number; readonly y: number }
  readonly config: Readonly<Record<string, ConfigValue>>
}

export interface GraphEdge {
  readonly id: string
  readonly source: string
  readonly target: string
  readonly source_handle: string | null
}

export interface WorkflowGraph {
  readonly schema_version: string
  readonly nodes: readonly GraphNode[]
  readonly edges: readonly GraphEdge[]
}

export type DeploymentState = "active" | "paused" | "draft"

export interface WorkflowSummary {
  readonly id: number
  readonly company_id: number
  readonly workflow_key: string
  readonly name: string
  readonly description: string | null
  readonly archived: boolean
  readonly state: DeploymentState
  readonly deployed_version_id: number | null
  readonly trigger_type: string | null
  readonly updated_at: string
}

export interface DraftRecord {
  readonly id: number
  readonly workflow_id: number
  readonly revision: number
  readonly schema_version: string
  readonly graph: WorkflowGraph
  readonly updated_by: string | null
  readonly updated_at: string
}

export interface DeploymentRecord {
  readonly id: number
  readonly workflow_id: number
  readonly version_id: number
  readonly trigger_type: string
  readonly state: Exclude<DeploymentState, "draft">
  readonly activated_at: string | null
  readonly paused_at: string | null
}

export interface VersionRecord {
  readonly id: number
  readonly workflow_id: number
  readonly version: number
  readonly content_hash: string
  readonly trigger_type: string
  readonly published_by: string | null
  readonly published_at: string
  readonly notes: string | null
}

export interface WorkflowDetail extends WorkflowSummary {
  readonly draft: DraftRecord | null
  readonly deployment: DeploymentRecord | null
  readonly deployed_version: VersionRecord | null
  readonly draft_matches_published: boolean
  readonly has_unpublished_changes: boolean
}

export type IssueSeverity = "error" | "warning"

export interface ValidationIssue {
  readonly code: string
  readonly message: string
  readonly severity: IssueSeverity
  readonly node_id: string | null
  readonly edge_id: string | null
  readonly field_key: string | null
}

export interface ValidationResult {
  readonly is_valid: boolean
  readonly trigger_type: string | null
  readonly error_count: number
  readonly warning_count: number
  readonly issues: readonly ValidationIssue[]
}

export interface SimulationStep {
  readonly sequence: number
  readonly node_id: string
  readonly node_type: string
  readonly outcome: string
  readonly chosen_handle: string | null
  readonly simulated_at: string
  readonly detail: Readonly<Record<string, unknown>>
  readonly effect: { readonly kind: string; readonly node_id: string } | null
}

export interface SimulationResult {
  readonly status: string
  readonly stopped_reason: string | null
  readonly path: readonly string[]
  readonly steps: readonly SimulationStep[]
  readonly effects: readonly { readonly kind: string; readonly node_id: string }[]
  readonly produced_no_effects: true
}

export interface SimulationResponse {
  readonly validation: ValidationResult
  readonly simulation: SimulationResult
}

export interface DiffResult {
  readonly has_published_version: boolean
  readonly published_version: number | null
  readonly identical: boolean
  readonly nodes_added: readonly string[]
  readonly nodes_removed: readonly string[]
  readonly nodes_changed: readonly string[]
  readonly edges_added: readonly string[]
  readonly edges_removed: readonly string[]
}

export type ExecutionStatus =
  | "queued"
  | "running"
  | "waiting"
  | "awaiting_human"
  | "succeeded"
  | "failed"
  | "canceled"
  | "dead_letter"

export interface ExecutionSummary {
  readonly id: number
  readonly workflow_id: number
  readonly version_id: number
  readonly status: ExecutionStatus
  readonly current_node_id: string | null
  readonly steps_executed: number
  readonly failure_reason: string | null
  readonly started_at: string
  readonly finished_at: string | null
  readonly context: Readonly<Record<string, unknown>>
}

export interface StepRecord {
  readonly id: number
  readonly sequence: number
  readonly node_id: string
  readonly node_type: string
  readonly status: string
  readonly attempt: number
  readonly chosen_handle: string | null
  readonly failure_reason: string | null
  readonly detail: Readonly<Record<string, unknown>>
  readonly finished_at: string | null
}

export interface JobRecord {
  readonly id: number
  readonly job_kind: string
  readonly node_id: string | null
  readonly status: string
  readonly attempts: number
  readonly max_attempts: number
  readonly run_at: string
  readonly last_error: string | null
}

export interface EffectRecord {
  readonly id: number
  readonly effect_kind: string
  readonly node_id: string
  readonly status: string
  readonly failure_reason: string | null
  readonly completed_at: string | null
}

export interface ExecutionDetail extends ExecutionSummary {
  readonly steps: readonly StepRecord[]
  readonly jobs: readonly JobRecord[]
  readonly effects: readonly EffectRecord[]
}

export interface ApprovalGate {
  readonly id: number
  readonly execution_id: number
  readonly workflow_id: number | null
  readonly workflow_name: string | null
  readonly node_id: string
  readonly status: string
  readonly title: string
  readonly summary: string | null
  readonly claimed_by: string | null
  readonly expires_at: string | null
  readonly created_at: string
}

export interface DeadLetterEntry {
  readonly job_id: number
  readonly execution_id: number
  readonly job_kind: string
  readonly node_id: string | null
  readonly attempts: number
  readonly last_error: string | null
  readonly updated_at: string
}

export interface PublishResult {
  readonly workflow_id: number
  readonly version_id: number
  readonly version: number
  readonly trigger_type: string
  readonly state: "paused"
  readonly warnings: readonly ValidationIssue[]
}

export interface AccessScope {
  readonly actor: string
  readonly global_role: string
  readonly is_global: boolean
  readonly authorized_company_id: number | null
  readonly authorized_company_ids: readonly number[]
}
