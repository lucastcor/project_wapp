import { fileURLToPath, URL } from "node:url"

import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"

/**
 * O bundle é servido pelo próprio FastAPI, na mesma origem de `/platform`.
 *
 * Mesma origem não é detalhe de conveniência: é o que permite reutilizar o
 * cookie HTTP-only de sessão sem expor token ao JavaScript e sem abrir CORS.
 * Por isso a saída vai para dentro de `app/platform/` e a base é o caminho
 * público final.
 */
export default defineConfig({
  plugins: [react()],
  base: "/platform/automations/",
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  build: {
    outDir: fileURLToPath(new URL("../../app/platform/automations", import.meta.url)),
    emptyOutDir: true,
    sourcemap: false,
    rollupOptions: {
      output: {
        // O runtime do canvas é grande e muda pouco: separá-lo mantém o
        // bundle da aplicação pequeno entre deploys.
        manualChunks: {
          canvas: ["@xyflow/react"],
        },
      },
    },
  },
  server: {
    proxy: {
      "/admin": { target: "http://127.0.0.1:8000", changeOrigin: false },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./tests/setup.ts"],
    include: ["tests/**/*.test.{ts,tsx}"],
  },
})
