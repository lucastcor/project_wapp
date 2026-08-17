import "@testing-library/jest-dom/vitest"

import { afterEach, vi } from "vitest"
import { cleanup } from "@testing-library/react"

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

// O canvas do @xyflow mede o container; jsdom não tem layout. Sem estes
// stubs os testes de componente falhariam por um detalhe de ambiente, não
// por um defeito de código.
globalThis.ResizeObserver = class ResizeObserverStub implements ResizeObserver {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

globalThis.DOMMatrixReadOnly ??= class {
  m22 = 1
} as unknown as typeof globalThis.DOMMatrixReadOnly
