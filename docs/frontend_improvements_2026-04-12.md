# Refactor Frontend Platform — 2026-04-12 → 2026-04-13

Pass completo de UX/UI na plataforma dedicada em `app/platform/`. O objetivo era
sair do visual utilitário e chegar em algo à altura de produtos premium de
WhatsApp Business, mantendo o stack vanilla JS + FastAPI.

## Fase A — Refactor visual completo (concluída)

### 1. Novo design system (`app/platform/assets/styles.css`)

Reescrito do zero (~1900 linhas) com:

- **Tokens de design** — paleta WhatsApp (`--brand: #25d366`, `--accent: #1fd2bd`),
  surface em camadas (`--surface-2`, `--surface-3`, `--bg-0`), sistema de sombras
  e easing curves consistentes.
- **Tipografia** — Sora (headings) + IBM Plex Sans (body) + mono para tokens.
- **Componentes** — shell, sidebar com ícones, topbar, pills, tags, cards,
  hero, grids (2/3/4), metric-cards com ícone, forms, segmented, tab-bar,
  chat bubble, timeline, spotlight, empty-state.
- **Flow Studio** — `.flow-layout` 3 colunas (palette + canvas + inspector),
  `.flow-canvas-inner` 2400×1600 escalável via transform, `.flow-node-head`
  com ícone e kicker, edges SVG com filter drop-shadow.
- **Calendário** — `.calendar-month-*` (grid mensal com chips de booking e
  painel de detalhe) + `.calendar-board` (timeline semanal existente).
- **Contacts** — `.contact-grid` com `.contact-row` (avatar + nome + pipeline +
  perfil em grid responsivo).
- **Breakpoints** — 1400px (flow compacta), 1200px (sidebar horizontal),
  900px (single column).

### 2. `app/platform/assets/app.js`

Refactorado tela por tela, preservando state shape, API layer (`apiRequest`)
e loaders. Mudanças principais:

- **Library de ícones inline** — objeto `ICONS` com 21 SVGs usados em sidebar,
  metric cards, toolbar, botões e flow nodes.
- **Sidebar** — brand card com `.brand-logo` + mark, nav buttons agora com
  `.nav-icon` no grid 36px + label.
- **Control Tower** — metric cards ganham `.metric-head` com ícone; quick
  actions viram cards próprios (deixam de reusar `.nav-button`).
- **Bot Studio** — mantém a estrutura de chat bubble; botão send com ícone.
- **Flow Studio** — refactor estrutural:
  - `FLOW_NODE_TEMPLATES` com categorias (Entrada, Agenda, Conversão,
    Escalonamento) e defaults pré-preenchidos.
  - Palette à esquerda com click-to-add.
  - Canvas em `.flow-canvas-card` com toolbar de zoom (in/out/fit) e
    percent readout.
  - Canvas interno fixo 2400×1600 com `transform: scale(zoom)`; drag logic
    divide delta do pointer pelo zoom para manter 1:1 com o cursor.
  - Nós redesenhados: `.flow-node-head` com ícone auto-selecionado pela key,
    footer com chips de transição/triggers.
  - Keys auto-geradas derivam do label (em vez de `stage_N`).
- **Agenda Console** — mode switch month/week agora usa `.segmented`, nav
  buttons viraram `.button-icon` com chevrons SVG, metric cards com ícones.
- **Contacts Desk** — base de contatos virou `.contact-grid` de `.contact-row`
  (avatar com iniciais + nome + pipeline + perfil em grid). Edição inline via
  `<details>` nativo, sem precisar de state de seleção.
- **Training Ops** — ícones nas metric cards e botões de ação.

### 3. Validação

- `node --check` passa em `app.js`.
- Commits por tela (`cb92db7` checkpoint → `4fb1c8d` shell/dashboard →
  `ec91d5a` flow studio → commits subsequentes) — cada um revertível.
- Feedback visual no navegador pendente (checklist abaixo).

### Checklist para validar no navegador

```bash
uvicorn app.main:app --reload
open http://127.0.0.1:8000/platform
```

- [ ] Sidebar com ícones, nav buttons em grid 36px+label
- [ ] Control Tower com metric cards iconados e quick action tiles
- [ ] Bot Studio: chat bubble funcionando, send com ícone
- [ ] Flow Studio: palette 3-col, click em template adiciona nó, zoom in/out/fit
- [ ] Flow Studio: drag de nó respeita zoom (sem descolar do cursor)
- [ ] Agenda: toggle mês/semana via segmented, chevrons SVG
- [ ] Agenda: clicar num dia do mês mostra clientes agendados
- [ ] Contacts: linhas com avatar, clicar expande edição inline
- [ ] Training Ops: cards iconados, botões com ícones

## Fase B em diante (não executada nesta sessão)

Acordadas com o usuário para sessões futuras:

### Fase B — Agenda: scheduling toggle + janela de horário
- Backend novo: `PUT /admin/calendar/scheduling-config` com
  `{enabled, windows_by_weekday, slot_duration_minutes}`
- Persistência: provavelmente em `company_context.scheduling` ou nova tabela
- Validação no `calendar_service.query_availability` para respeitar a janela
- Frontend: toggle no topo da Agenda + editor de janela por dia da semana

### Fase C — Drag-to-resize da janela + booking manual com confirmação
- Drag handles nas bordas do slot no timeline semanal
- `POST /admin/calendar/bookings` com flag `request_bot_confirmation`
- Reuso do outbound dispatcher para enviar mensagem de confirmação

### Fase D — Bot Studio com sessões de chat
- Tabela `platform_chat_sessions` ou reuso de contato fixo por sessão
- Endpoints `GET/POST /admin/studio/sessions`
- UI: lista lateral de sessões + chat persistente sem reinserir contact_id

### Fase E — Contacts: busca avançada
- Query params em `GET /admin/contacts`: filtro por pipeline_stage,
  customer_profile, last_activity_range, search
- UI: chips de filtro no topo

### Fase F — Flow Studio fluidez + instruções, Training Ops revisão,
fila de prioridade review
- Otimizar redraw de edges durante drag (possivelmente usar
  `requestAnimationFrame` ou mover SVG para camada separada)
- Onboarding tour / tooltips no Flow Studio
- Revisar `appointment_queue_service` e melhorar se necessário

## Arquivos tocados nesta fase

```
app/platform/assets/styles.css    # reescrito completo
app/platform/assets/app.js        # icons + refactor tela a tela
docs/frontend_improvements_2026-04-12.md   # este arquivo
docs/platform_frontend_guide.md   # mapeamento de telas ↔ endpoints (anterior)
```
