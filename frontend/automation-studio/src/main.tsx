import { StrictMode } from "react"
import { createRoot } from "react-dom/client"

import { App } from "./App"
import "./styles/studio.css"

const container = document.getElementById("automation-root")
if (container === null) {
  throw new Error("O elemento #automation-root não existe no documento.")
}

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
