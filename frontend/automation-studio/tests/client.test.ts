/**
 * Cliente HTTP: autenticação por cookie, escopo do servidor e erros tipados.
 *
 * O teste de "nenhum segredo no navegador" é o cenário 22: nem o código nem
 * as requisições carregam credencial de provider.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"

import { DraftConflictError, GraphRejectedError, api } from "@/api/client"
import type { ApiError } from "@/api/client"
import { EMPTY_GRAPH } from "@/state/graph"

function mockFetch(response: {
  readonly status: number
  readonly body?: unknown
}): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: response.status >= 200 && response.status < 300,
    status: response.status,
    json: () => Promise.resolve(response.body ?? null),
  })
  vi.stubGlobal("fetch", fetchMock)
  return fetchMock
}

beforeEach(() => {
  vi.restoreAllMocks()
})

describe("autenticação", () => {
  it("envia o cookie de sessão e nunca um header de token", async () => {
    const fetchMock = mockFetch({ status: 200, body: [] })

    await api.listWorkflows(7)

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(init.credentials).toBe("same-origin")

    const headers = init.headers as Record<string, string>
    expect(Object.keys(headers).map((key) => key.toLowerCase())).not.toContain("authorization")
    expect(Object.keys(headers).map((key) => key.toLowerCase())).not.toContain("x-admin-token")
  })

  it("usa sempre a mesma origem", async () => {
    const fetchMock = mockFetch({ status: 200, body: [] })

    await api.listWorkflows(1)

    const [url] = fetchMock.mock.calls[0] as [string]
    expect(new URL(url).origin).toBe(window.location.origin)
  })
})

describe("escopo de empresa", () => {
  it("anexa company_id quando a plataforma resolveu um escopo", async () => {
    const fetchMock = mockFetch({ status: 200, body: [] })

    await api.listWorkflows(42)

    const [url] = fetchMock.mock.calls[0] as [string]
    expect(new URL(url).searchParams.get("company_id")).toBe("42")
  })

  it("omite company_id quando o escopo é global", async () => {
    const fetchMock = mockFetch({ status: 200, body: [] })

    await api.listWorkflows(null)

    const [url] = fetchMock.mock.calls[0] as [string]
    expect(new URL(url).searchParams.has("company_id")).toBe(false)
  })
})

describe("erros tipados", () => {
  it("traduz 409 de rascunho em DraftConflictError", async () => {
    mockFetch({
      status: 409,
      body: { detail: { message: "stale", expected_revision: 3, current_revision: 5 } },
    })

    await expect(api.saveDraft(1, 2, EMPTY_GRAPH, 3)).rejects.toBeInstanceOf(DraftConflictError)

    try {
      await api.saveDraft(1, 2, EMPTY_GRAPH, 3)
    } catch (error) {
      const conflict = error as DraftConflictError
      expect(conflict.expectedRevision).toBe(3)
      expect(conflict.currentRevision).toBe(5)
    }
  })

  it("traduz 422 de publicação em GraphRejectedError com os problemas", async () => {
    mockFetch({
      status: 422,
      body: {
        detail: {
          message: "inválido",
          issues: [
            {
              code: "cycle_detected",
              message: "ciclo",
              severity: "error",
              node_id: "a",
              edge_id: null,
              field_key: null,
            },
          ],
        },
      },
    })

    try {
      await api.publish(1, 2)
      expect.unreachable("deveria ter lançado")
    } catch (error) {
      const rejected = error as GraphRejectedError
      expect(rejected).toBeInstanceOf(GraphRejectedError)
      expect(rejected.issues).toHaveLength(1)
      expect(rejected.issues[0]?.code).toBe("cycle_detected")
    }
  })

  it("dá uma mensagem acionável para 401 e 403", async () => {
    mockFetch({ status: 401, body: { detail: "x" } })
    await expect(api.listWorkflows(1)).rejects.toThrow(/sessão expirou/i)

    mockFetch({ status: 403, body: { detail: "x" } })
    await expect(api.listWorkflows(1)).rejects.toThrow(/não tem acesso/i)
  })

  it("mantém o status no erro para quem quiser tratar", async () => {
    mockFetch({ status: 500, body: { detail: "boom" } })

    try {
      await api.listWorkflows(1)
      expect.unreachable("deveria ter lançado")
    } catch (error) {
      expect((error as ApiError).status).toBe(500)
    }
  })
})

describe("superfície do cliente", () => {
  it("não expõe nenhuma credencial de provider em nenhum fonte", async () => {
    // Cenário 22: o frontend não carrega segredo de provider nem executa
    // fluxo de verdade. A varredura é sobre o código-fonte inteiro, não
    // apenas sobre este módulo.
    const fs = await import("node:fs")
    const path = await import("node:path")

    const files: string[] = []
    const walk = (dir: string): void => {
      for (const entry of fs.readdirSync(dir)) {
        const full = path.join(dir, entry)
        if (fs.statSync(full).isDirectory()) walk(full)
        else if (/\.(ts|tsx)$/.test(entry)) files.push(full)
      }
    }
    walk(path.join(process.cwd(), "src"))
    expect(files.length).toBeGreaterThan(5)

    // Formatos de credencial, não as palavras: "secret" aparece em comentário
    // justamente explicando que segredo nenhum entra aqui.
    const credentialShapes = [
      /\bsk-[A-Za-z0-9_-]{16,}/,
      /\bwapp(adm)?_[A-Za-z0-9_-]{16,}/,
      /\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\./,
      /Bearer\s+[A-Za-z0-9_-]{12,}/,
    ]

    for (const file of files) {
      const text = fs.readFileSync(file, "utf8")
      for (const shape of credentialShapes) {
        expect(shape.test(text), `${file} parece conter uma credencial`).toBe(false)
      }
      // O navegador não executa fluxo: nenhum `eval` e nenhum `Function`.
      expect(/\beval\s*\(/.test(text), `${file} usa eval`).toBe(false)
      expect(/new\s+Function\s*\(/.test(text), `${file} usa new Function`).toBe(false)
    }
  })

  it("nunca chama um host externo", async () => {
    const fetchMock = mockFetch({ status: 200, body: [] })

    await api.listWorkflows(1)
    await api.listApprovals(1)
    await api.listExecutions(1)

    for (const call of fetchMock.mock.calls) {
      const [url] = call as [string]
      expect(url.startsWith(window.location.origin)).toBe(true)
    }
  })
})
