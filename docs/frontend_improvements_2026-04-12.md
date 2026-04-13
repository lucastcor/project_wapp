# Melhorias Frontend + Agenda — 2026-04-12

Pass focado nas duas dores relatadas pelo produto: Flow Studio piscando ao arrastar nós e Agenda Console sem um calendário "de verdade". Tudo aplicado na plataforma dedicada em `app/platform/` (SPA vanilla servida pelo FastAPI), sem introduzir framework.

## 1. Flow Studio — drag agora é estável e profissional

### Diagnóstico
O handler de drag de nós em `bindFlowEvents()` (`app/platform/assets/app.js`) re-renderizava o SPA inteiro a cada `pointermove` (`renderApp()` dentro do `onMove`). Resultado: flicker, inputs do inspector perdendo foco, conexões tremendo, latência perceptível. Havia também um `click` separado sobrepondo o `pointerdown`, então um clique que "acontecia de ser um drag pequeno" disparava seleção duplicada.

### O que foi feito
`app/platform/assets/app.js`:
- Removido o handler de `click` redundante. O `pointerdown` agora é o único ponto de entrada.
- Pointer capture via `setPointerCapture`, `pointermove/pointerup/pointercancel` no próprio botão — não perde o evento se o cursor sair da área.
- Threshold de 4 px antes de considerar drag (clique "puro" continua funcionando para selecionar a etapa).
- Durante o drag:
  - O nó é movido **diretamente via DOM** (`button.style.left/top`) — zero re-render.
  - As conexões SVG são atualizadas **in place** recalculando apenas o atributo `d` das paths afetadas. Para isso cada `<path>` ganhou `data-edge-source`/`data-edge-target` em `renderFlowCanvas()`, e uma função `updateEdgePathsForKey()` reescreve só as paths conectadas ao nó arrastado.
  - O inspector (`#pipeline-inspector-form`) tem seus campos `canvas_x`/`canvas_y` sincronizados via atribuição direta — sem re-render, sem perda de foco.
- `renderApp()` só é chamado uma vez ao final, e apenas se a seleção mudou.
- `event.preventDefault()` no `pointerdown` evita o click fantasma depois do pointerup.

`app/platform/assets/styles.css`:
- `.flow-node` ganhou `touch-action: none` (mobile/tablet param de agora em diante arrasta sem scroll).
- Classe `.flow-node.dragging` adiciona sombra, `z-index`, `user-select: none` e desativa transição para feedback limpo.
- Transições de hover/seleção mantidas fora do drag.

### Resultado
- Drag fluido (60fps) mesmo com vários nós e conexões.
- Inputs do inspector não perdem mais o foco durante edição.
- Clicar no nó continua selecionando; só sai do modo seleção se o usuário realmente arrastar.

## 2. Agenda Console — calendário mensal de verdade + toggle Mês/Semana

### Diagnóstico
A agenda só tinha a "timeline semanal" (grade horária). Faltava a visão panorâmica do mês que toda operação premium espera: "o que tenho dia X?", "quantos clientes na terça?", "quero ver a semana inteira do mês anterior".

### O que foi feito
`app/platform/assets/app.js`:
- Novo estado `state.agendaCalendar` com `mode` (`month|week`), `monthCursor` (primeiro dia do mês, ISO) e `selectedDay`.
- Helpers de data puros (sem dependência): `parseDateKey`, `formatDateKey`, `formatTimeHM`, `groupBookingsByDay`.
- Nova função `renderMonthCalendar(bookings)` que desenha:
  - **Toolbar** com navegação ‹ mês › , label "Mês de Ano" e botão **Hoje**.
  - **Switch Mês/Semana** no canto direito.
  - **Grade 7×N** (6 semanas na maioria dos meses) com dias fora do mês em opacidade reduzida, indicador de "hoje" e célula selecionada.
  - Cada célula mostra até 3 chips de agendamento (hora + cliente/título) + contador "+N mais".
  - **Painel de detalhe** abaixo do grid: ao clicar num dia, lista **todos os atendimentos daquele dia** com horário, título, contato e perfil. Esse era o pedido central: "mostrando clientes naquele dia".
- `renderAgendaView()` agora escolhe entre `renderMonthCalendar` (padrão) e a timeline semanal existente baseado em `state.agendaCalendar.mode`.
- `bindAgendaEvents()` recebeu três bindings novos: `data-agenda-mode`, `data-month-nav` (prev/next/today) e `data-month-day`.

`app/platform/assets/styles.css`:
- Bloco completo `.calendar-month-*` — grid responsivo, chips, indicador de "hoje", célula selecionada, painel de detalhe.
- Media query até 900px: células compactas, chip só com horário, lista do dia em coluna única.

### Resultado
- Abrindo Agenda Console, você vê imediatamente o mês inteiro com a carga de clientes por dia.
- Um clique no dia abre a lista vertical completa (horário, título, contato, perfil).
- Navegação por mês é instantânea (opera só sobre `state.calendarBookings` já carregado).
- Toggle "Semana" mantém a timeline existente para quem precisa do zoom horário.

## 3. Pontos de melhoria de backend recomendados (não aplicados neste pass)

Identificados durante a leitura, mas deixados para decisão sua antes de mexer — alguns afetam contratos de API.

1. **`GET /admin/calendar/bookings` aceitar `from`/`to`.** Hoje `loadAgendaData()` puxa a lista inteira sem recorte. Para empresas com milhares de bookings isso vai escalar mal quando a base crescer. Sugestão: parâmetros `from_date`/`to_date` (opcionais) e `limit` default 500. O frontend já estaria pronto para passar janelas alinhadas ao mês visível.
2. **Endpoint `GET /admin/calendar/month-summary?year&month`.** Seria um colega de `/admin/calendar/timeline`, devolvendo `{ date, bookings_count, profiles_breakdown }` por dia. Permite construir heatmap de ocupação sem trazer payload completo.
3. **`calendar_service.get_calendar_timeline` — range parameterizável.** O cliente passa `days=7` fixo. Expor `start_date`/`end_date` permitiria usar a mesma timeline como "semana do mês visível" ao navegar no calendário.
4. **Paginação real em `/admin/contacts`.** Hoje o frontend força `limit=80`. Quando a base crescer vai faltar cursor/offset e contador total.
5. **WebSocket / SSE para Control Tower e Agenda.** O dashboard faz fetch a cada abertura de view; um canal push (`/ws/operations`) permitiria refletir bookings novos e dispatches outbound sem o usuário recarregar.
6. **Índice composto `(company_id, starts_at)` em `appointment_bookings`.** Valeria validar no `EXPLAIN` — crítico para o endpoint acima com `from/to`.
7. **`conversation_pipelines` — salvamento otimista por etapa.** Hoje o Flow Studio só persiste via `PUT /admin/company-context` inteiro. Um `PATCH /admin/company-context/pipelines/{key}` evitaria race-conditions quando múltiplos operadores editarem.
8. **Rate limit no `POST /messages/incoming` simulado do Bot Studio.** Quando subir em produção e o webhook real chegar no mesmo endpoint, operadores testando à vontade podem bater no runtime real.
9. **Validação de `next_keys` com sugestões.** O backend já valida unicidade e referências; adicionar `409` com lista de `suggested_keys` ajudaria a UI do Flow Studio a oferecer auto-correção.
10. **Teste E2E leve do Flow Studio e do calendário mensal.** Playwright/selenium cobrindo "arrastar nó não quebra canvas" e "clicar num dia carrega detalhe". Baratinho e impede regressão.

## 4. Arquivos tocados

```
app/platform/assets/app.js      # drag Flow Studio + calendário mensal + helpers
app/platform/assets/styles.css  # .flow-node.dragging + .calendar-month-*
docs/frontend_improvements_2026-04-12.md  # este arquivo
```

## 5. Como validar localmente

```bash
# subir a API
uvicorn app.main:app --reload
# abrir
open http://127.0.0.1:8000/platform
```

Checklist rápido:
- [ ] Flow Studio → arrastar qualquer nó: deve deslizar sem piscar e sem perder foco do inspector.
- [ ] Flow Studio → clicar (sem arrastar) num nó: seleciona e mostra no inspector.
- [ ] Flow Studio → as linhas (conexões) acompanham o nó em tempo real durante o drag.
- [ ] Agenda Console → abre no modo **Mês**, com navegação ‹/›/Hoje.
- [ ] Agenda Console → clicar num dia abre o detalhe vertical com os clientes daquele dia.
- [ ] Agenda Console → toggle "Semana" volta para a timeline horária existente.
- [ ] Responsivo: em < 900px o grid mensal compacta e a lista do dia vira coluna única.

## 6. Decisões que valem revisão

- O modo padrão da agenda virou **Mês**. Se o uso operacional do dia-a-dia for mais forte na timeline, é 1 linha para voltar ao default `week`.
- Os chips no calendário mensal mostram `title || contact_id`. Se o contato tiver display name no futuro (`contact.display_name`), trocar para ele.
- O calendário mensal lê `state.calendarBookings`. Se o recorte de bookings for limitado no backend, o mês pode aparecer incompleto — por isso o item #1 do backend acima é o mais importante para escalar.
