/**
 * Casca do Automation Studio.
 *
 * Duas coisas acontecem antes de qualquer tela: o escopo autorizado e o
 * catálogo são pedidos ao servidor. O escopo diz **qual empresa** esta
 * credencial pode operar — o cliente nunca escolhe um tenant — e o catálogo é
 * a fonte única do que existe.
 *
 * Navegação por hash, para conviver com o `/platform` existente sem exigir um
 * roteador ou mudanças no servidor de arquivos estáticos.
 */

import { useCallback, useEffect, useMemo, useState } from "react"

import { ApiError, api } from "@/api/client"
import type { AccessScope, CatalogResponse } from "@/api/types"
import { Approvals } from "./components/Approvals"
import { Editor } from "./components/Editor"
import { Executions } from "./components/Executions"
import { WorkflowList } from "./components/WorkflowList"
import { ErrorState, LoadingState, ToastStack, type Toast } from "./components/primitives"

type Route =
  | { readonly view: "list" }
  | { readonly view: "editor"; readonly workflowId: number }
  | { readonly view: "executions" }
  | { readonly view: "approvals" }

function parseHash(hash: string): Route {
  const path = hash.replace(/^#\/?/, "")
  const editorMatch = /^workflows\/(\d+)$/.exec(path)
  if (editorMatch?.[1] !== undefined) {
    return { view: "editor", workflowId: Number(editorMatch[1]) }
  }
  if (path === "executions") return { view: "executions" }
  if (path === "approvals") return { view: "approvals" }
  return { view: "list" }
}

const ROLE_WEIGHT: Record<string, number> = { viewer: 1, operator: 2, superadmin: 3 }

let toastSequence = 0

export function App(): JSX.Element {
  const [scope, setScope] = useState<AccessScope | null>(null)
  const [catalog, setCatalog] = useState<CatalogResponse | null>(null)
  const [bootError, setBootError] = useState<string | null>(null)
  const [route, setRoute] = useState<Route>(() => parseHash(window.location.hash))
  const [toasts, setToasts] = useState<readonly Toast[]>([])

  const pushToast = useCallback((toast: Omit<Toast, "id">): void => {
    toastSequence += 1
    const entry: Toast = { ...toast, id: toastSequence }
    setToasts((current) => [...current, entry])
    window.setTimeout(() => {
      setToasts((current) => current.filter((item) => item.id !== entry.id))
    }, 6000)
  }, [])

  useEffect(() => {
    const onHashChange = (): void => setRoute(parseHash(window.location.hash))
    window.addEventListener("hashchange", onHashChange)
    return () => window.removeEventListener("hashchange", onHashChange)
  }, [])

  useEffect(() => {
    async function boot(): Promise<void> {
      try {
        const accessScope = await api.getAccessScope()
        setScope(accessScope)
        setCatalog(await api.getCatalog(accessScope.authorized_company_id))
      } catch (error) {
        setBootError(
          error instanceof ApiError
            ? error.message
            : "Não foi possível iniciar o Automation Studio.",
        )
      }
    }
    void boot()
  }, [])

  const navigate = useCallback((next: Route): void => {
    const hash =
      next.view === "editor"
        ? `#/workflows/${next.workflowId}`
        : next.view === "list"
          ? "#/"
          : `#/${next.view}`
    window.location.hash = hash
  }, [])

  const permissions = useMemo(() => {
    const weight = ROLE_WEIGHT[scope?.global_role ?? "viewer"] ?? 1
    return {
      canOperate: weight >= 2,
      canEdit: weight >= 3,
    }
  }, [scope])

  const capabilities = useMemo(
    () =>
      catalog === null
        ? []
        : // Uma capacidade aparece como habilitada quando nenhum nó que a
          // exige foi recusado — a informação autoritativa vem da validação,
          // e a paleta só antecipa o que o servidor diria.
          Array.from(
            new Set(
              catalog.nodes
                .map((spec) => spec.required_capability)
                .filter((capability): capability is string => capability !== null),
            ),
          ),
    [catalog],
  )

  if (bootError !== null) {
    return (
      <div className="studio">
        <div className="page">
          <ErrorState
            title="Automation Studio indisponível"
            text={bootError}
            action={
              <a className="btn" href="/platform">
                Voltar para a plataforma
              </a>
            }
          />
        </div>
      </div>
    )
  }

  if (scope === null || catalog === null) {
    return <LoadingState label="Resolvendo seu acesso…" />
  }

  const companyId = scope.authorized_company_id

  if (route.view === "editor") {
    return (
      <>
        <Editor
          companyId={companyId}
          workflowId={route.workflowId}
          catalog={catalog}
          capabilities={capabilities}
          canEdit={permissions.canEdit}
          onBack={() => navigate({ view: "list" })}
          onToast={pushToast}
        />
        <ToastStack
          toasts={toasts}
          onDismiss={(id) => setToasts((current) => current.filter((item) => item.id !== id))}
        />
      </>
    )
  }

  return (
    <div className="studio">
      <header className="topbar">
        <a className="topbar__back" href="/platform">
          ← Plataforma
        </a>

        <div className="topbar__identity">
          <span className="topbar__title">Automation Studio</span>
          <span className="topbar__meta">
            {scope.actor} · {scope.global_role}
            {companyId !== null && ` · empresa ${companyId}`}
          </span>
        </div>

        <span className="topbar__spacer" />

        <nav className="section-nav" aria-label="Seções">
          <button
            type="button"
            className="section-nav__item"
            aria-current={route.view === "list" ? "page" : undefined}
            onClick={() => navigate({ view: "list" })}
          >
            Fluxos
          </button>
          <button
            type="button"
            className="section-nav__item"
            aria-current={route.view === "executions" ? "page" : undefined}
            onClick={() => navigate({ view: "executions" })}
          >
            Execuções
          </button>
          <button
            type="button"
            className="section-nav__item"
            aria-current={route.view === "approvals" ? "page" : undefined}
            onClick={() => navigate({ view: "approvals" })}
          >
            Aprovações
          </button>
        </nav>

        {!catalog.engine_enabled && (
          <span className="badge badge--paused" title="AUTOMATION_ENGINE_ENABLED=false">
            Motor desligado
          </span>
        )}
      </header>

      <div className="studio__body">
        <main className="studio__main">
          {route.view === "list" && (
            <WorkflowList
              companyId={companyId}
              canCreate={permissions.canEdit}
              onOpen={(workflowId) => navigate({ view: "editor", workflowId })}
              onToast={pushToast}
            />
          )}
          {route.view === "executions" && (
            <Executions
              companyId={companyId}
              canOperate={permissions.canOperate}
              onToast={pushToast}
            />
          )}
          {route.view === "approvals" && (
            <Approvals
              companyId={companyId}
              canDecide={permissions.canOperate}
              onToast={pushToast}
            />
          )}
        </main>
      </div>

      <ToastStack
        toasts={toasts}
        onDismiss={(id) => setToasts((current) => current.filter((item) => item.id !== id))}
      />
    </div>
  )
}
