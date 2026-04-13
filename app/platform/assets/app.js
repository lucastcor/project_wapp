const STORAGE_KEY = "project-wapp-platform-config-v1"

const ICONS = {
  dashboard: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/></svg>',
  studio: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/></svg>',
  flow: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="6" height="6" rx="1.2"/><rect x="15" y="3" width="6" height="6" rx="1.2"/><rect x="9" y="15" width="6" height="6" rx="1.2"/><path d="M6 9v3h12V9"/><path d="M12 12v3"/></svg>',
  calendar: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="17" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/></svg>',
  contacts: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9.5" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>',
  training: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M22 10v6M2 10l10-5 10 5-10 5z"/><path d="M6 12v5c3 3 9 3 12 0v-5"/></svg>',
  plus: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>',
  trash: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/></svg>',
  chevronLeft: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg>',
  chevronRight: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>',
  zoomIn: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.3-4.3M11 8v6M8 11h6"/></svg>',
  zoomOut: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.3-4.3M8 11h6"/></svg>',
  fit: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 9V5a1 1 0 0 1 1-1h4M20 9V5a1 1 0 0 0-1-1h-4M4 15v4a1 1 0 0 0 1 1h4M20 15v4a1 1 0 0 1-1 1h-4"/></svg>',
  send: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M22 2L11 13M22 2l-7 20-4-9-9-4z"/></svg>',
  refresh: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.5 9a9 9 0 0 1 14.9-3.4L23 10M1 14l4.6 4.4A9 9 0 0 0 20.5 15"/></svg>',
  clock: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>',
  check: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>',
  search: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.3-4.3"/></svg>',
  spark: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v4M12 17v4M3 12h4M17 12h4M5.6 5.6l2.8 2.8M15.6 15.6l2.8 2.8M5.6 18.4l2.8-2.8M15.6 8.4l2.8-2.8"/></svg>',
  target: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/></svg>',
  handoff: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M17 11V7a5 5 0 0 0-10 0v4"/><rect x="5" y="11" width="14" height="10" rx="2"/></svg>',
  tag: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M20.6 13.4L13.4 20.6a2 2 0 0 1-2.8 0l-8.2-8.2V2h10.4l8.2 8.2a2 2 0 0 1 0 2.8z"/><circle cx="7.5" cy="7.5" r="1.2"/></svg>',
}

const NAV_ITEMS = [
  { key: "dashboard", label: "Control Tower", caption: "Readiness e postura operacional", icon: "dashboard" },
  { key: "studio", label: "Bot Studio", caption: "Teste conversas antes de publicar", icon: "studio" },
  { key: "flows", label: "Flow Studio", caption: "Pipelines, playbooks e respostas", icon: "flow" },
  { key: "agenda", label: "Agenda Console", caption: "Calendário, bookings e fila", icon: "calendar" },
  { key: "contacts", label: "Contacts Desk", caption: "Perfil, busca e notas", icon: "contacts" },
  { key: "training", label: "Training Ops", caption: "Importação, curadoria, fine-tune", icon: "training" },
]

const DEFAULT_PIPELINES = [
  {
    key: "lead_capture",
    label: "Entrada e qualificação",
    objective: "Entender rapidamente quem é o cliente, o contexto e o nível de intenção comercial.",
    trigger_terms: ["primeiro contato", "pedido de informação", "interesse inicial"],
    success_criteria: "Cliente identificado, necessidade resumida e próximo passo definido.",
    handoff_when: "Caso envolva exceção comercial, preço sensível ou contexto confuso.",
    preferred_cta: "Convidar o cliente a detalhar necessidade, objetivo ou urgência.",
    next_keys: ["appointment_management"],
    canvas_position: { x: 80, y: 80 },
  },
  {
    key: "appointment_management",
    label: "Agendamento e reagendamento",
    objective: "Conduzir disponibilidade, confirmação, cancelamento e remanejamento com linguagem natural.",
    trigger_terms: ["agendar", "cancelar horário", "remarcar", "encaixe"],
    success_criteria: "Reserva confirmada, vaga liberada ou fila atualizada sem atrito.",
    handoff_when: "Quando a política operacional não cobrir a exceção solicitada.",
    preferred_cta: "Apresentar janela disponível e pedir confirmação objetiva.",
    next_keys: ["conversion_followup"],
    canvas_position: { x: 420, y: 180 },
  },
  {
    key: "conversion_followup",
    label: "Conversão e follow-up",
    objective: "Mover leads mornos para próxima ação comercial sem perder naturalidade.",
    trigger_terms: ["orçamento", "quando pode", "vou pensar", "me chama depois"],
    success_criteria: "Próxima ação registrada com prazo ou CTA aceito pelo cliente.",
    handoff_when: "Quando o cliente pedir condição fora da política ou negociação especial.",
    preferred_cta: "Propor próximo passo claro: horário, proposta, visita ou retorno.",
    next_keys: [],
    canvas_position: { x: 760, y: 80 },
  },
]

const root = document.getElementById("platform-root")

const state = {
  view: "dashboard",
  loading: false,
  loadingMessage: "",
  settingsOpen: false,
  connected: false,
  session: null,
  errorMessage: "",
  toasts: [],
  config: loadStoredConfig(),
  companies: [],
  observabilitySummary: null,
  readiness: null,
  systemProfile: null,
  onboarding: null,
  companyContext: null,
  messageExamples: [],
  calendarProfile: null,
  calendarBookings: [],
  queueEntries: [],
  queueOffers: [],
  calendarTimeline: null,
  contacts: [],
  trainingReview: null,
  finetuningJobs: [],
  trainingImportResult: null,
  availabilityResult: null,
  simulator: {
    transcript: [],
    lastResult: null,
  },
  flowEditor: {
    selectedPipelineKey: "lead_capture",
  },
  agendaCalendar: {
    mode: "month",
    monthCursor: (() => {
      const now = new Date()
      return new Date(now.getFullYear(), now.getMonth(), 1).toISOString().slice(0, 10)
    })(),
    selectedDay: null,
  },
  drafts: {
    studio: {
      contactId: "5511999999999",
      contactName: "Cliente Premium",
      message: "",
    },
    availability: {
      startDate: todayString(),
      endDate: offsetDateString(7),
      durationMinutes: "60",
      limit: "6",
      schedulingProfileKey: "",
      customerProfile: "",
      serviceName: "",
      requestContext: "",
    },
    booking: {
      contactId: "",
      title: "",
      startsAt: "",
      endsAt: "",
      timezone: "America/Sao_Paulo",
      description: "",
      schedulingProfileKey: "",
      customerProfile: "",
    },
    contactSearch: "",
    trainingImport: {
      format: "csv",
      rawText: "",
      delimiter: ",",
      dryRun: true,
      updateContacts: true,
      createMessageExamples: true,
      autoPromoteEligibleExamples: false,
      defaultProfileName: "default",
      defaultScenarioType: "general",
      defaultQualityScore: "7",
    },
    auth: {
      adminToken: "",
      adminActor: "platform-admin",
    },
  },
}

function loadStoredConfig() {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY)
    if (!stored) {
      return {
        adminActor: "platform-admin",
        companySlug: "",
        companyId: "",
      }
    }
    const parsed = JSON.parse(stored)
    return {
      adminActor: parsed.adminActor || "platform-admin",
      companySlug: parsed.companySlug || "",
      companyId: parsed.companyId || "",
    }
  } catch {
    return {
      adminActor: "platform-admin",
      companySlug: "",
      companyId: "",
    }
  }
}

function persistConfig() {
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(state.config))
}

function todayString() {
  return new Date().toISOString().slice(0, 10)
}

function offsetDateString(days) {
  const value = new Date()
  value.setDate(value.getDate() + days)
  return value.toISOString().slice(0, 10)
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;")
}

function safeJsonParse(value, fallback) {
  try {
    return JSON.parse(value)
  } catch {
    return fallback
  }
}

function summarizeError(error) {
  if (!error) {
    return "Falha inesperada."
  }
  if (typeof error === "string") {
    return error
  }
  if (error.detail) {
    return typeof error.detail === "string" ? error.detail : JSON.stringify(error.detail)
  }
  if (error.message) {
    return error.message
  }
  return "Falha inesperada."
}

function formatNumber(value) {
  const normalized = Number(value || 0)
  return Number.isFinite(normalized) ? new Intl.NumberFormat("pt-BR").format(normalized) : "0"
}

function formatDateTime(value) {
  if (!value) {
    return "—"
  }
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) {
    return String(value)
  }
  return new Intl.DateTimeFormat("pt-BR", {
    dateStyle: "short",
    timeStyle: "short",
  }).format(parsed)
}

function toDatetimeLocal(value) {
  if (!value) {
    return ""
  }
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) {
    return ""
  }
  const year = parsed.getFullYear()
  const month = String(parsed.getMonth() + 1).padStart(2, "0")
  const day = String(parsed.getDate()).padStart(2, "0")
  const hours = String(parsed.getHours()).padStart(2, "0")
  const minutes = String(parsed.getMinutes()).padStart(2, "0")
  return `${year}-${month}-${day}T${hours}:${minutes}`
}

function normalizeDatetimeLocal(value) {
  if (!value) {
    return ""
  }
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? value : parsed.toISOString()
}

function getSelectedCompany() {
  return state.companies.find(
    (company) => String(company.id) === String(state.config.companyId) || company.slug === state.config.companySlug
  ) || null
}

function getCompanyScopeLabel() {
  const selectedCompany = getSelectedCompany()
  if (selectedCompany) {
    return `${selectedCompany.name} · ${selectedCompany.slug}`
  }
  if (state.config.companySlug) {
    return `Escopo manual · ${state.config.companySlug}`
  }
  return "Escopo global"
}

function getCompanyParams() {
  const params = {}
  if (state.config.companyId) {
    params.company_id = state.config.companyId
  }
  if (state.config.companySlug) {
    params.company_slug = state.config.companySlug
  }
  return params
}

function getAdminCompanyParams() {
  const params = {}
  if (state.config.companyId) {
    params.company_id = state.config.companyId
  }
  return params
}

function getCompanyContextParams() {
  return state.config.companySlug ? { company_slug: state.config.companySlug } : null
}

function pushToast(message, type = "success") {
  const toast = {
    id: window.crypto.randomUUID(),
    message,
    type,
  }
  state.toasts = [...state.toasts, toast].slice(-4)
  renderApp()
  window.setTimeout(() => {
    state.toasts = state.toasts.filter((item) => item.id !== toast.id)
    renderApp()
  }, 3600)
}

async function apiRequest(path, options = {}) {
  const {
    method = "GET",
    params = null,
    body = null,
    admin = true,
  } = options

  const url = new URL(path, window.location.origin)
  if (params) {
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== "") {
        url.searchParams.set(key, value)
      }
    })
  }

  const headers = {}
  if (body !== null) {
    headers["Content-Type"] = "application/json"
  }
  if (admin && state.config.adminActor) {
    headers["X-Admin-Actor"] = state.config.adminActor
  }

  const response = await fetch(url, {
    method,
    headers,
    body: body !== null ? JSON.stringify(body) : undefined,
    credentials: "same-origin",
  })

  const contentType = response.headers.get("content-type") || ""
  let payload = null

  if (contentType.includes("application/json")) {
    payload = await response.json()
  } else {
    const text = await response.text()
    payload = text ? { detail: text } : null
  }

  if (!response.ok) {
    const error = new Error(summarizeError(payload))
    error.status = response.status
    error.payload = payload
    throw error
  }

  return payload
}

async function fetchPlatformSession() {
  const session = await apiRequest("/platform/auth/session", {
    admin: false,
  })
  state.session = session
  state.connected = true
  return session
}

async function loginPlatformSession() {
  const session = await apiRequest("/platform/auth/session", {
    method: "POST",
    admin: false,
    body: {
      admin_token: state.drafts.auth.adminToken || null,
      admin_actor: state.drafts.auth.adminActor || state.config.adminActor || null,
    },
  })
  state.session = session
  state.connected = true
  state.config.adminActor = session.actor || state.config.adminActor
  state.drafts.auth.adminToken = ""
  state.drafts.auth.adminActor = session.actor || state.config.adminActor
  persistConfig()
  return session
}

async function logoutPlatformSession() {
  await apiRequest("/platform/auth/session", {
    method: "DELETE",
    admin: false,
  })
  state.session = null
  state.connected = false
}

async function loadCompanies() {
  const companies = await apiRequest("/admin/companies")
  state.companies = Array.isArray(companies) ? companies : []

  const selected = getSelectedCompany()
  if (!selected && state.companies.length > 0 && !state.config.companySlug) {
    const first = state.companies[0]
    state.config.companyId = String(first.id)
    state.config.companySlug = first.slug
    persistConfig()
  }
}

async function loadDashboardData() {
  await loadCompanies()
  const [observabilitySummary, readiness, systemProfile, onboarding] = await Promise.all([
    apiRequest("/admin/observability/summary"),
    apiRequest("/admin/system/readiness"),
    apiRequest("/admin/system/profile"),
    apiRequest("/admin/system/onboarding", {
      params: getCompanyParams(),
    }),
  ])
  state.observabilitySummary = observabilitySummary
  state.readiness = readiness
  state.systemProfile = systemProfile
  state.onboarding = onboarding
}

async function loadStudioData() {
  if (!state.companyContext) {
    state.companyContext = await apiRequest("/admin/company-context", {
      params: getCompanyContextParams(),
    })
  }
}

async function loadFlowData() {
  const [context, examples] = await Promise.all([
    apiRequest("/admin/company-context", {
      params: getCompanyContextParams(),
    }),
    apiRequest("/admin/message-examples", {
      params: getAdminCompanyParams(),
    }),
  ])
  state.companyContext = ensureConversationPipelines(context)
  state.messageExamples = Array.isArray(examples) ? examples : []
}

async function loadAgendaData() {
  const [calendarProfile, calendarBookings, queueEntries, queueOffers, calendarTimeline] = await Promise.all([
    apiRequest("/admin/calendar/profile", {
      params: getAdminCompanyParams(),
    }),
    apiRequest("/admin/calendar/bookings", {
      params: getAdminCompanyParams(),
    }),
    apiRequest("/admin/calendar/queue", {
      params: getAdminCompanyParams(),
    }),
    apiRequest("/admin/calendar/queue-offers", {
      params: getAdminCompanyParams(),
    }),
    apiRequest("/admin/calendar/timeline", {
      params: {
        ...getAdminCompanyParams(),
        days: 7,
      },
    }),
  ])
  state.calendarProfile = calendarProfile
  state.calendarBookings = Array.isArray(calendarBookings) ? calendarBookings : []
  state.queueEntries = Array.isArray(queueEntries) ? queueEntries : []
  state.queueOffers = Array.isArray(queueOffers) ? queueOffers : []
  state.calendarTimeline = calendarTimeline
}

async function loadContactsData() {
  const params = {
    ...getAdminCompanyParams(),
    limit: 80,
  }
  if (state.drafts.contactSearch) {
    params.q = state.drafts.contactSearch
  }
  const contacts = await apiRequest("/admin/contacts", { params })
  state.contacts = Array.isArray(contacts) ? contacts : []
}

async function loadTrainingData() {
  const [reviewPayload, finetuningJobs] = await Promise.all([
    apiRequest("/admin/training-dataset/review", {
      params: getAdminCompanyParams(),
    }),
    apiRequest("/admin/finetuning/jobs", {
      params: { limit: 10 },
    }),
  ])
  state.trainingReview = safeJsonParse(reviewPayload?.dataset || "{}", {})
  state.finetuningJobs = Array.isArray(finetuningJobs) ? finetuningJobs : []
}

function ensureConversationPipelines(context) {
  const normalizedContext = { ...(context || {}) }
  const pipelines = Array.isArray(normalizedContext.conversation_pipelines)
    ? normalizedContext.conversation_pipelines
    : DEFAULT_PIPELINES
  normalizedContext.conversation_pipelines = pipelines.map((pipeline, index) => ({
    key: pipeline.key || `pipeline_${index + 1}`,
    label: pipeline.label || `Etapa ${index + 1}`,
    objective: pipeline.objective || "",
    trigger_terms: Array.isArray(pipeline.trigger_terms) ? pipeline.trigger_terms : [],
    success_criteria: pipeline.success_criteria || "",
    handoff_when: pipeline.handoff_when || "",
    preferred_cta: pipeline.preferred_cta || "",
    next_keys: Array.isArray(pipeline.next_keys) ? pipeline.next_keys : [],
    canvas_position: {
      x: Number(pipeline.canvas_position?.x ?? (80 + (index % 2) * 330)),
      y: Number(pipeline.canvas_position?.y ?? (70 + Math.floor(index / 2) * 240)),
    },
  }))
  return normalizedContext
}

async function refreshCurrentView({ silent = false } = {}) {
  state.loading = !silent
  state.loadingMessage = viewLoadingLabel(state.view)
  renderApp()

  try {
    if (state.view === "dashboard") {
      await loadDashboardData()
    } else if (state.view === "studio") {
      await loadCompanies()
      await loadStudioData()
    } else if (state.view === "flows") {
      await loadCompanies()
      await loadFlowData()
    } else if (state.view === "agenda") {
      await loadCompanies()
      await loadAgendaData()
    } else if (state.view === "contacts") {
      await loadCompanies()
      await loadContactsData()
    } else if (state.view === "training") {
      await loadCompanies()
      await loadTrainingData()
    }
    state.connected = true
    state.errorMessage = ""
  } catch (error) {
    state.errorMessage = summarizeError(error)
    if (error.status === 401 || error.status === 403) {
      state.connected = false
      state.session = null
      state.settingsOpen = true
    }
    pushToast(state.errorMessage, "error")
  } finally {
    state.loading = false
    state.loadingMessage = ""
    renderApp()
  }
}

function viewLoadingLabel(view) {
  if (view === "dashboard") return "Carregando panorama operacional..."
  if (view === "studio") return "Abrindo ambiente de simulação..."
  if (view === "flows") return "Montando playbooks e respostas aprovadas..."
  if (view === "agenda") return "Buscando agenda e fila de prioridade..."
  if (view === "contacts") return "Buscando contatos..."
  if (view === "training") return "Montando operação de curadoria..."
  return "Carregando..."
}

function getReadinessTone(status) {
  if (status === "pass" || status === "ready") return "tag-success"
  if (status === "warn" || status === "warning") return "tag-warn"
  return "tag-danger"
}

function renderReadinessPill() {
  const readiness = state.readiness
  if (!readiness) {
    return '<span class="pill"><span class="loader"></span><strong>Readiness</strong> sincronizando</span>'
  }
  const status = readiness.overall_status || "warning"
  const className =
    status === "pass" || status === "ready"
      ? "pill-status-pass"
      : status === "warning"
        ? "pill-status-warning"
        : "pill-status-blocked"
  return `<span class="pill ${className}"><strong>Readiness</strong>${escapeHtml(status)}</span>`
}

function renderTopbar() {
  const selectedCompany = getSelectedCompany()
  const sessionRole = state.session?.role || "sem sessão"
  const scopeOptions = [
    '<option value="__global__">Escopo global</option>',
    ...state.companies.map(
      (company) =>
        `<option value="${escapeHtml(company.slug)}" ${selectedCompany?.slug === company.slug ? "selected" : ""}>${escapeHtml(company.name)} · ${escapeHtml(company.slug)}</option>`
    ),
  ].join("")

  return `
    <header class="topbar">
      <div>
        <h1 class="topbar-title">${renderViewTitle()}</h1>
        <p class="topbar-copy">${renderViewCopy()}</p>
      </div>
      <div class="topbar-controls">
        ${renderReadinessPill()}
        <label class="pill">
          <span>Empresa</span>
          <select id="company-select" style="background: transparent; border: 0; color: inherit;">
            ${scopeOptions}
          </select>
        </label>
        <span class="pill"><strong>Sessão</strong>${escapeHtml(sessionRole)}</span>
        <span class="pill"><strong>Escopo</strong>${escapeHtml(getCompanyScopeLabel())}</span>
        <button class="button-ghost" id="open-settings">${state.session ? "Sessão" : "Entrar"}</button>
        ${state.session ? '<button class="button-danger" id="logout-session">Sair</button>' : ""}
      </div>
    </header>
  `
}

function renderViewTitle() {
  if (state.view === "dashboard") return "Uma camada premium para operar o bot como produto"
  if (state.view === "studio") return "Bot Studio para testar conversa real antes de publicar"
  if (state.view === "flows") return "Flow Studio para lapidar regras, playbooks e respostas"
  if (state.view === "agenda") return "Agenda Console com controle manual e visão operacional"
  if (state.view === "contacts") return "Contacts Desk para decidir com contexto e precisão"
  if (state.view === "training") return "Training Ops para curar base histórica com segurança"
  return "Project WAPP Platform"
}

function renderViewCopy() {
  if (state.view === "dashboard") return "Readiness, observabilidade, onboarding e comando central da operação."
  if (state.view === "studio") return "Simule cliente, operador e fluxos de agendamento na mesma interface."
  if (state.view === "flows") return "Desenhe a experiência do bot, organize pipelines e promova mensagens aprovadas."
  if (state.view === "agenda") return "Monitore disponibilidade, bookings, remanejamento e ajustes manuais."
  if (state.view === "contacts") return "Enriqueça customer_profile, organize notas e desambiguação de atendimento."
  if (state.view === "training") return "Importe CRM, faça dry-run, acompanhe o dataset e acione fine-tuning."
  return "Painel premium do Project WAPP."
}

function renderSidebar() {
  return `
    <aside class="sidebar">
      <div class="brand-card">
        <div class="brand-logo">
          <span class="brand-logo-mark">${ICONS.spark}</span>
          <span>WAPP Platform</span>
        </div>
        <span class="brand-eyebrow">Premium Ops</span>
        <h2 class="brand-title">Controle total do bot como produto.</h2>
        <p class="brand-copy">
          Teste, agenda, curadoria e operação em tempo real — em uma superfície só.
        </p>
      </div>

      <div class="nav-section-label">Workspace</div>
      <nav class="nav-list">
        ${NAV_ITEMS.map(
          (item) => `
            <button class="nav-button ${state.view === item.key ? "active" : ""}" data-view="${item.key}">
              <span class="nav-icon">${ICONS[item.icon] || ""}</span>
              <span>
                <span class="nav-label">${escapeHtml(item.label)}</span>
                <span class="nav-caption">${escapeHtml(item.caption)}</span>
              </span>
            </button>
          `
        ).join("")}
      </nav>

      <div class="sidebar-footnote">
        Streamlit continua como contingência. Superfície principal é <span class="mono">/platform</span>.
      </div>
    </aside>
  `
}

function renderMetricCard(label, value, caption, tone = "default", iconKey = "dashboard") {
  const classes = tone === "warm" ? "metric-card card card-muted" : "metric-card card"
  return `
    <article class="${classes}">
      <div class="metric-head">
        <span class="metric-icon">${ICONS[iconKey] || ICONS.dashboard}</span>
        <span class="metric-label">${escapeHtml(label)}</span>
      </div>
      <div class="metric-value">${escapeHtml(value)}</div>
      <p class="metric-caption">${escapeHtml(caption)}</p>
    </article>
  `
}

function renderDashboardView() {
  const totals = state.observabilitySummary?.totals || {}
  const readiness = state.readiness
  const onboarding = state.onboarding
  const warnings = readiness?.checks?.filter((check) => check.status !== "pass").slice(0, 5) || []
  const runtimeBackend = state.systemProfile?.database?.runtime?.backend || "—"
  const companyCount = state.companies.length

  return `
    <section class="hero">
      <span class="brand-eyebrow">Command Center</span>
      <h2 class="hero-title">A operação inteira do bot em uma camada visual própria.</h2>
      <p class="hero-copy">
        Aqui você mede maturidade, readiness e tração real. A ideia é sair de um painel utilitário e entrar numa experiência de produto, com capacidade de teste, curadoria, agenda e controle.
      </p>
      <div class="hero-actions">
        <button class="button" data-jump-view="studio">Testar conversa agora</button>
        <button class="button-ghost" data-jump-view="agenda">Abrir agenda operacional</button>
        <button class="button-ghost" data-refresh-view>Atualizar panorama</button>
      </div>
    </section>

    <section class="grid-four">
      ${renderMetricCard("Mensagens", formatNumber(totals.messages), "Volume já tratado pelo runtime atual.", "default", "studio")}
      ${renderMetricCard("Contatos", formatNumber(totals.contacts), "Base viva para perfil, memória e agenda.", "default", "contacts")}
      ${renderMetricCard("Bookings", formatNumber(state.calendarBookings.length), "Reservas carregadas na visão atual.", "warm", "calendar")}
      ${renderMetricCard("Empresas", formatNumber(companyCount), "Escopos disponíveis nesta instância.", "default", "tag")}
    </section>

    <section class="grid-two">
      <article class="card">
        <div class="card-header">
          <div>
            <h3 class="card-title">Postura operacional</h3>
            <p class="card-copy">Readiness, ambiente ativo e situação do onboarding.</p>
          </div>
          <span class="tag ${getReadinessTone(readiness?.overall_status)}">${escapeHtml(readiness?.overall_status || "sem dados")}</span>
        </div>
        <div class="timeline">
          <div class="timeline-item">
            <strong>Runtime</strong>
            <p>Backend atual: ${escapeHtml(runtimeBackend)}.</p>
          </div>
          <div class="timeline-item">
            <strong>Pilot ready</strong>
            <p>${readiness?.pilot_ready ? "Sim, a base de produto já sustenta piloto." : "Ainda existem bloqueios para piloto consistente."}</p>
          </div>
          <div class="timeline-item">
            <strong>Scale ready</strong>
            <p>${readiness?.scale_ready ? "Pronto para operação mais dura." : "Ainda exige endurecimento adicional para escala."}</p>
          </div>
          <div class="timeline-item">
            <strong>Onboarding</strong>
            <p>Status atual do escopo selecionado: ${escapeHtml(onboarding?.overall_status || "sem dados")}.</p>
          </div>
        </div>
      </article>

      <article class="card">
        <div class="card-header">
          <div>
            <h3 class="card-title">Itens que merecem atenção agora</h3>
            <p class="card-copy">O que mais impacta segurança operacional e go-live.</p>
          </div>
        </div>
        ${
          warnings.length
            ? `
              <div class="timeline">
                ${warnings
                  .map(
                    (check) => `
                      <div class="timeline-item">
                        <strong>${escapeHtml(check.title || check.code || "Check")}</strong>
                        <p>${escapeHtml(check.detail || "Sem detalhe adicional.")}</p>
                      </div>
                    `
                  )
                  .join("")}
              </div>
            `
            : '<div class="empty-state">Nenhum alerta principal no readiness atual.</div>'
        }
      </article>
    </section>

    <section class="grid-two">
      <article class="card">
        <div class="section-header">
          <div>
            <h3 class="section-title">Quick actions</h3>
            <p class="section-copy">Atalhos para os módulos que viram rotina operacional.</p>
          </div>
        </div>
        <div class="grid-two">
          <button class="card card-muted" data-view="studio" style="cursor:pointer;text-align:left;">
            <div class="card-header"><div><h3 class="card-title">Testar cliente ou operador</h3><p class="card-copy">Simule agendamento, cancelamento e agenda interna.</p></div><span class="metric-icon">${ICONS.studio}</span></div>
          </button>
          <button class="card card-muted" data-view="flows" style="cursor:pointer;text-align:left;">
            <div class="card-header"><div><h3 class="card-title">Editar pipelines</h3><p class="card-copy">Desenhe a forma como o bot conduz a conversa.</p></div><span class="metric-icon">${ICONS.flow}</span></div>
          </button>
          <button class="card card-muted" data-view="agenda" style="cursor:pointer;text-align:left;">
            <div class="card-header"><div><h3 class="card-title">Operar agenda</h3><p class="card-copy">Bookings, reagendamento e fila prioritária.</p></div><span class="metric-icon">${ICONS.calendar}</span></div>
          </button>
          <button class="card card-muted" data-view="training" style="cursor:pointer;text-align:left;">
            <div class="card-header"><div><h3 class="card-title">Importar histórico</h3><p class="card-copy">Faça dry-run antes de promover para treino.</p></div><span class="metric-icon">${ICONS.training}</span></div>
          </button>
        </div>
      </article>

      <article class="card">
        <div class="section-header">
          <div>
            <h3 class="section-title">Empresa em foco</h3>
            <p class="section-copy">O escopo ativo determina contexto, agenda e treinamento.</p>
          </div>
        </div>
        <div class="spotlight">
          <span class="tag">${escapeHtml(getCompanyScopeLabel())}</span>
          <strong>${escapeHtml(state.onboarding?.scope?.company_name || "Escopo global")}</strong>
          <p>${escapeHtml(state.onboarding?.context_status?.is_valid ? "Company context válido e operacional." : "Contexto ainda precisa de revisão ou carga.")}</p>
          <div class="meta-row">
            <span class="meta-chip">Exemplos elegíveis: ${formatNumber(state.onboarding?.finetuning_readiness?.eligible_examples_count || 0)}</span>
            <span class="meta-chip">Mensagens: ${formatNumber(state.onboarding?.operational_snapshot?.counts?.messages || 0)}</span>
          </div>
        </div>
      </article>
    </section>
  `
}

function renderStudioView() {
  const transcript = state.simulator.transcript
  const context = state.companyContext
  const operatorContacts = Array.isArray(context?.internal_operation?.operator_contact_ids)
    ? context.internal_operation.operator_contact_ids
    : []

  return `
    <section class="hero">
      <span class="brand-eyebrow">Bot Studio</span>
      <h2 class="hero-title">Teste conversa real antes de colocar para rodar.</h2>
      <p class="hero-copy">
        O simulador usa a mesma entrada pública do produto. Isso significa que você consegue validar fluxo natural de cliente, operador, cancelamento, reagendamento e agenda interna no mesmo runtime.
      </p>
      <div class="chip-row">
        <button class="chip" data-studio-prompt="Quero agendar uma consultoria inicial para amanhã às 10h">Agendamento</button>
        <button class="chip" data-studio-prompt="Preciso remarcar meu horário de amanhã para sexta à tarde">Reagendamento</button>
        <button class="chip" data-studio-prompt="Vou cancelar meu atendimento de amanhã">Cancelamento</button>
        <button class="chip" data-studio-prompt="Marque a Maria Silva amanhã às 10h para consultoria inicial">Operação interna</button>
      </div>
    </section>

    <section class="two-pane">
      <article class="card">
        <div class="section-header">
          <div>
            <h3 class="section-title">Contexto de simulação</h3>
            <p class="section-copy">Escolha quem está falando e em qual empresa o fluxo será testado.</p>
          </div>
        </div>
        <form id="studio-form" class="form-grid">
          <div class="field">
            <label for="studio-contact-id">Contact ID</label>
            <input id="studio-contact-id" name="contactId" value="${escapeHtml(state.drafts.studio.contactId)}" />
          </div>
          <div class="field">
            <label for="studio-contact-name">Nome exibido</label>
            <input id="studio-contact-name" name="contactName" value="${escapeHtml(state.drafts.studio.contactName)}" />
          </div>
          <div class="field">
            <label for="studio-message">Mensagem para o bot</label>
            <textarea id="studio-message" name="message" placeholder="Ex.: Quero remarcar meu atendimento para sexta às 15h">${escapeHtml(state.drafts.studio.message)}</textarea>
          </div>
          <div class="toolbar-row">
            <button class="button" type="submit">Enviar para o runtime</button>
            ${
              operatorContacts.length
                ? `<button class="button-ghost" type="button" id="use-operator-contact">Usar contato interno ${escapeHtml(operatorContacts[0])}</button>`
                : ""
            }
            <button class="button-ghost" type="button" id="clear-studio-log">Limpar conversa</button>
          </div>
        </form>
      </article>

      <article class="card chat-shell">
        <div class="section-header">
          <div>
            <h3 class="section-title">Transcript</h3>
            <p class="section-copy">A resposta mostra classificação, pipeline e ação aplicada.</p>
          </div>
          ${
            state.simulator.lastResult
              ? `<span class="tag ${getReadinessTone(state.simulator.lastResult.action === "auto_reply" ? "ready" : "warning")}">${escapeHtml(state.simulator.lastResult.action)}</span>`
              : ""
          }
        </div>
        <div class="chat-log">
          ${
            transcript.length
              ? transcript
                  .map((item) => {
                    if (item.role === "user") {
                      return `<div class="bubble bubble-user">${escapeHtml(item.text)}</div>`
                    }
                    const meta = item.meta || {}
                    return `
                      <div class="bubble bubble-assistant">
                        ${escapeHtml(item.text)}
                        <div class="bubble-meta">
                          <span class="meta-chip">Classificação: ${escapeHtml(meta.classification || "—")}</span>
                          <span class="meta-chip">Ação: ${escapeHtml(meta.action || "—")}</span>
                          <span class="meta-chip">Pipeline: ${escapeHtml(meta.pipeline_stage || "—")}</span>
                          <span class="meta-chip">Fonte: ${escapeHtml(meta.source || "—")}</span>
                        </div>
                      </div>
                    `
                  })
                  .join("")
              : '<div class="empty-state">Envie uma mensagem para ver o comportamento do bot dentro do runtime real.</div>'
          }
        </div>
      </article>
    </section>
  `
}

function renderFlowCanvas(pipelines) {
  const canvasWidth = 1180
  const canvasHeight = Math.max(
    560,
    ...pipelines.map((pipeline) => Number(pipeline.canvas_position?.y || 0) + 220)
  )

  const pipelineLookup = new Map(pipelines.map((pipeline) => [pipeline.key, pipeline]))
  const edges = pipelines.flatMap((pipeline) =>
    (pipeline.next_keys || [])
      .map((nextKey) => {
        const target = pipelineLookup.get(nextKey)
        if (!target) {
          return null
        }
        const startX = Number(pipeline.canvas_position?.x || 0) + 260
        const startY = Number(pipeline.canvas_position?.y || 0) + 70
        const endX = Number(target.canvas_position?.x || 0)
        const endY = Number(target.canvas_position?.y || 0) + 70
        const midX = startX + (endX - startX) / 2
        return `
          <path
            d="M ${startX} ${startY} C ${midX} ${startY}, ${midX} ${endY}, ${endX} ${endY}"
            class="flow-edge-path"
            data-edge-source="${escapeHtml(pipeline.key)}"
            data-edge-target="${escapeHtml(nextKey)}"
            fill="none"
          />
        `
      })
      .filter(Boolean)
  )

  return `
    <div class="flow-canvas-wrapper">
      <div class="flow-canvas-grid"></div>
      <svg class="flow-svg" viewBox="0 0 ${canvasWidth} ${canvasHeight}" preserveAspectRatio="none">
        <defs>
          <marker id="flow-arrow" markerWidth="12" markerHeight="12" refX="8" refY="6" orient="auto">
            <path d="M 0 0 L 12 6 L 0 12 z" fill="rgba(31, 210, 189, 0.7)"></path>
          </marker>
        </defs>
        ${edges.join("")}
      </svg>
      ${pipelines
        .map((pipeline) => {
          const isSelected = state.flowEditor.selectedPipelineKey === pipeline.key
          return `
            <button
              type="button"
              class="flow-node ${isSelected ? "selected" : ""}"
              data-select-pipeline="${escapeHtml(pipeline.key)}"
              data-pipeline-key="${escapeHtml(pipeline.key)}"
              style="left:${Number(pipeline.canvas_position?.x || 0)}px; top:${Number(pipeline.canvas_position?.y || 0)}px;"
            >
              <span class="flow-node-kicker">${escapeHtml(pipeline.key)}</span>
              <strong>${escapeHtml(pipeline.label || pipeline.key)}</strong>
              <p>${escapeHtml(pipeline.objective || "Sem objetivo descrito ainda.")}</p>
              <span class="flow-node-footer">${escapeHtml((pipeline.next_keys || []).length)} transições</span>
            </button>
          `
        })
        .join("")}
    </div>
  `
}

function renderFlowInspector(pipeline, allPipelines) {
  if (!pipeline) {
    return '<div class="empty-state">Selecione uma etapa no canvas para editar o playbook.</div>'
  }

  const nextKeysValue = (pipeline.next_keys || []).join(", ")

  return `
    <form id="pipeline-inspector-form" class="form-grid">
      <input type="hidden" name="key" value="${escapeHtml(pipeline.key)}" />
      <div class="field">
        <label>Label</label>
        <input name="label" value="${escapeHtml(pipeline.label || "")}" />
      </div>
      <div class="field">
        <label>Objetivo</label>
        <textarea name="objective">${escapeHtml(pipeline.objective || "")}</textarea>
      </div>
      <div class="field">
        <label>Triggers</label>
        <input name="trigger_terms" value="${escapeHtml((pipeline.trigger_terms || []).join(", "))}" />
      </div>
      <div class="field">
        <label>Critério de sucesso</label>
        <textarea name="success_criteria">${escapeHtml(pipeline.success_criteria || "")}</textarea>
      </div>
      <div class="field">
        <label>Handoff</label>
        <textarea name="handoff_when">${escapeHtml(pipeline.handoff_when || "")}</textarea>
      </div>
      <div class="field">
        <label>CTA preferido</label>
        <textarea name="preferred_cta">${escapeHtml(pipeline.preferred_cta || "")}</textarea>
      </div>
      <div class="form-grid-two">
        <div class="field">
          <label>Próximas etapas</label>
          <input name="next_keys" value="${escapeHtml(nextKeysValue)}" placeholder="Ex.: appointment_management, conversion_followup" />
          <small>Keys disponíveis: ${escapeHtml(allPipelines.map((item) => item.key).join(", "))}</small>
        </div>
        <div class="field">
          <label>Posição no canvas</label>
          <div class="quick-row">
            <input name="canvas_x" type="number" value="${escapeHtml(String(Number(pipeline.canvas_position?.x || 0)))}" placeholder="x" />
            <input name="canvas_y" type="number" value="${escapeHtml(String(Number(pipeline.canvas_position?.y || 0)))}" placeholder="y" />
          </div>
        </div>
      </div>
      <div class="inline-actions">
        <button class="button-ghost" type="submit">Aplicar no canvas</button>
        <button class="button-danger" type="button" data-remove-pipeline-key="${escapeHtml(pipeline.key)}">Remover etapa</button>
      </div>
    </form>
  `
}

function renderTimelineBoard(timeline) {
  if (!timeline?.days?.length) {
    return '<div class="empty-state">Sem dados de timeline para o período solicitado.</div>'
  }

  const hourLabels = Array.from({ length: 13 }, (_, index) => {
    const hour = 8 + index
    return `<div class="calendar-hour">${String(hour).padStart(2, "0")}:00</div>`
  }).join("")

  return `
    <div class="calendar-board">
      <div class="calendar-hours-column">
        <div class="calendar-day-header muted-label">Horário</div>
        ${hourLabels}
      </div>
      ${timeline.days
        .map(
          (day) => `
            <div class="calendar-day-column">
              <div class="calendar-day-header">
                <strong>${escapeHtml(day.label)}</strong>
                <span>${escapeHtml(day.weekday_label)}</span>
              </div>
              <div class="calendar-day-body">
                ${Array.from({ length: 24 }, (_, hourIndex) => `<div class="calendar-hour-line" style="top:${hourIndex * 60}px;"></div>`).join("")}
                ${(day.segments || [])
                  .map((segment) => {
                    const top = Number(segment.start_minutes || 0)
                    const height = Math.max(34, Number(segment.duration_minutes || 30))
                    const columnIndex = Number(segment.column_index || 0)
                    const columnCount = Math.max(1, Number(segment.column_count || 1))
                    const widthPercent = 100 / columnCount
                    const leftPercent = columnIndex * widthPercent
                    return `
                      <button
                        type="button"
                        class="calendar-event"
                        data-scroll-booking="${segment.booking_id}"
                        style="top:${top}px; height:${height}px; width:calc(${widthPercent}% - 8px); left:calc(${leftPercent}% + ${columnIndex * 4}px);"
                      >
                        <strong>${escapeHtml(segment.title || "Agendamento")}</strong>
                        <span>${escapeHtml(formatDateTime(segment.segment_starts_at))}</span>
                        <span>${escapeHtml(segment.contact_id || "sem contato")}</span>
                      </button>
                    `
                  })
                  .join("")}
              </div>
            </div>
          `
        )
        .join("")}
    </div>
  `
}

const MONTH_NAMES_PT = [
  "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
  "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
]
const WEEKDAY_NAMES_PT = ["Dom", "Seg", "Ter", "Qua", "Qui", "Sex", "Sáb"]

function parseDateKey(value) {
  if (!value) return null
  try {
    const d = new Date(value)
    if (Number.isNaN(d.getTime())) return null
    return d
  } catch (_) { return null }
}

function formatDateKey(date) {
  const y = date.getFullYear()
  const m = String(date.getMonth() + 1).padStart(2, "0")
  const d = String(date.getDate()).padStart(2, "0")
  return `${y}-${m}-${d}`
}

function formatTimeHM(value) {
  const d = parseDateKey(value)
  if (!d) return ""
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`
}

function groupBookingsByDay(bookings) {
  const map = new Map()
  for (const booking of bookings || []) {
    const d = parseDateKey(booking.starts_at)
    if (!d) continue
    const key = formatDateKey(d)
    if (!map.has(key)) map.set(key, [])
    map.get(key).push(booking)
  }
  for (const list of map.values()) {
    list.sort((a, b) => String(a.starts_at).localeCompare(String(b.starts_at)))
  }
  return map
}

function renderMonthCalendar(bookings) {
  const cursorStr = state.agendaCalendar.monthCursor
  const cursor = parseDateKey(cursorStr) || new Date()
  const year = cursor.getFullYear()
  const month = cursor.getMonth()
  const firstOfMonth = new Date(year, month, 1)
  const lastOfMonth = new Date(year, month + 1, 0)
  const startWeekday = firstOfMonth.getDay()
  const gridStart = new Date(year, month, 1 - startWeekday)
  const totalCells = Math.ceil((startWeekday + lastOfMonth.getDate()) / 7) * 7

  const bookingsByDay = groupBookingsByDay(bookings)
  const todayKey = formatDateKey(new Date())
  const selectedKey = state.agendaCalendar.selectedDay

  const header = `
    <div class="calendar-month-toolbar">
      <div class="calendar-month-nav">
        <button type="button" class="button-ghost" data-month-nav="prev" aria-label="Mês anterior">‹</button>
        <strong class="calendar-month-label">${MONTH_NAMES_PT[month]} de ${year}</strong>
        <button type="button" class="button-ghost" data-month-nav="next" aria-label="Próximo mês">›</button>
        <button type="button" class="button-ghost" data-month-nav="today">Hoje</button>
      </div>
      <div class="calendar-mode-switch">
        <button type="button" class="${state.agendaCalendar.mode === "month" ? "is-active" : ""}" data-agenda-mode="month">Mês</button>
        <button type="button" class="${state.agendaCalendar.mode === "week" ? "is-active" : ""}" data-agenda-mode="week">Semana</button>
      </div>
    </div>
    <div class="calendar-month-weekdays">
      ${WEEKDAY_NAMES_PT.map((w) => `<div>${w}</div>`).join("")}
    </div>
  `

  const cells = []
  for (let i = 0; i < totalCells; i++) {
    const day = new Date(gridStart.getFullYear(), gridStart.getMonth(), gridStart.getDate() + i)
    const key = formatDateKey(day)
    const inMonth = day.getMonth() === month
    const isToday = key === todayKey
    const isSelected = key === selectedKey
    const dayBookings = bookingsByDay.get(key) || []
    const maxChips = 3
    const chips = dayBookings.slice(0, maxChips).map((b) => `
      <div class="calendar-month-chip" title="${escapeHtml((b.title || "Agendamento") + " · " + (b.contact_id || ""))}">
        <span class="chip-time">${escapeHtml(formatTimeHM(b.starts_at))}</span>
        <span class="chip-title">${escapeHtml(b.title || b.contact_id || "Agendamento")}</span>
      </div>
    `).join("")
    const more = dayBookings.length > maxChips
      ? `<div class="calendar-month-more">+${dayBookings.length - maxChips} mais</div>`
      : ""
    cells.push(`
      <button type="button" class="calendar-month-cell${inMonth ? "" : " is-outside"}${isToday ? " is-today" : ""}${isSelected ? " is-selected" : ""}${dayBookings.length ? " has-events" : ""}" data-month-day="${key}">
        <div class="calendar-month-cell-head">
          <span class="calendar-month-daynumber">${day.getDate()}</span>
          ${dayBookings.length ? `<span class="calendar-month-count">${dayBookings.length}</span>` : ""}
        </div>
        <div class="calendar-month-cell-body">${chips}${more}</div>
      </button>
    `)
  }

  const detail = renderMonthDayDetail(bookingsByDay.get(selectedKey) || [], selectedKey)

  return `
    <div class="calendar-month-wrapper">
      ${header}
      <div class="calendar-month-grid">
        ${cells.join("")}
      </div>
      ${detail}
    </div>
  `
}

function renderMonthDayDetail(dayBookings, selectedKey) {
  if (!selectedKey) {
    return `<div class="calendar-month-detail empty-state">Clique em um dia para ver os clientes agendados.</div>`
  }
  const dateLabel = (() => {
    const d = parseDateKey(selectedKey)
    if (!d) return selectedKey
    return `${WEEKDAY_NAMES_PT[d.getDay()]}, ${d.getDate()} de ${MONTH_NAMES_PT[d.getMonth()]}`
  })()
  if (!dayBookings.length) {
    return `
      <div class="calendar-month-detail">
        <strong>${escapeHtml(dateLabel)}</strong>
        <p class="subtle">Sem atendimentos marcados nesse dia.</p>
      </div>
    `
  }
  return `
    <div class="calendar-month-detail">
      <strong>${escapeHtml(dateLabel)} · ${dayBookings.length} atendimento(s)</strong>
      <div class="calendar-month-detail-list">
        ${dayBookings.map((b) => `
          <article class="calendar-month-detail-item">
            <span class="detail-time">${escapeHtml(formatTimeHM(b.starts_at))} – ${escapeHtml(formatTimeHM(b.ends_at))}</span>
            <div class="detail-body">
              <strong>${escapeHtml(b.title || "Agendamento")}</strong>
              <span class="subtle">${escapeHtml(b.contact_id || "sem contato")} · ${escapeHtml(b.customer_profile || "perfil —")}</span>
            </div>
          </article>
        `).join("")}
      </div>
    </div>
  `
}

function renderFlowsView() {
  const context = ensureConversationPipelines(state.companyContext || {})
  const pipelines = context.conversation_pipelines || []
  if (!state.flowEditor.selectedPipelineKey || !pipelines.some((pipeline) => pipeline.key === state.flowEditor.selectedPipelineKey)) {
    state.flowEditor.selectedPipelineKey = pipelines[0]?.key || null
  }
  const selectedPipeline = pipelines.find((pipeline) => pipeline.key === state.flowEditor.selectedPipelineKey) || null
  const examples = state.messageExamples.slice(0, 6)

  return `
    <section class="hero">
      <span class="brand-eyebrow">Flow Studio</span>
      <h2 class="hero-title">Transforme regra de negócio em direção conversacional palpável.</h2>
      <p class="hero-copy">
        Esta área junta a camada editorial e estrutural do bot: playbooks conversacionais, transições entre etapas, sinais de handoff e respostas aprovadas que alimentam comportamento e treino.
      </p>
      <div class="hero-actions">
        <button class="button" id="save-pipelines">Salvar pipelines no company context</button>
        <button class="button-ghost" id="add-pipeline-stage">Adicionar etapa</button>
      </div>
    </section>

    <section class="grid-two">
      <article class="card">
        <div class="section-header">
          <div>
            <h3 class="section-title">Canvas visual de fluxo</h3>
            <p class="section-copy">Organize a lógica conversacional como um mapa operacional, com posições e transições persistidas no contexto.</p>
          </div>
          <span class="tag">${escapeHtml(String(pipelines.length))} etapas</span>
        </div>
        ${pipelines.length ? renderFlowCanvas(pipelines) : '<div class="empty-state">Nenhum pipeline configurado ainda.</div>'}
      </article>

      <article class="card">
        <div class="section-header">
          <div>
            <h3 class="section-title">Inspector da etapa</h3>
            <p class="section-copy">Edite a etapa selecionada, a posição no canvas e as transições para outras etapas.</p>
          </div>
        </div>
        ${renderFlowInspector(selectedPipeline, pipelines)}
      </article>
    </section>

    <section class="card">
      <div class="section-header">
        <div>
          <h3 class="section-title">Mensagens aprovadas</h3>
          <p class="section-copy">Crie respostas modelares para reutilização de estilo, revisão humana e treino.</p>
        </div>
      </div>
      <form id="example-form" class="form-grid">
        <div class="form-grid-three">
          <div class="field">
            <label>Profile name</label>
            <input name="profile_name" placeholder="default, vip, returning_customer..." />
          </div>
          <div class="field">
            <label>Scenario type</label>
            <input name="scenario_type" placeholder="general, reschedule, support..." />
          </div>
          <div class="field">
            <label>Quality score</label>
            <input name="quality_score" type="number" min="0" max="10" step="0.1" value="8.5" />
          </div>
        </div>
        <div class="field">
          <label>Mensagem do cliente</label>
          <textarea name="customer_message" placeholder="Ex.: Preciso reagendar meu atendimento de amanhã."></textarea>
        </div>
        <div class="field">
          <label>Resposta aprovada</label>
          <textarea name="approved_reply" placeholder="Ex.: Claro. Me diz o melhor período para eu buscar nova disponibilidade para você."></textarea>
        </div>
        <div class="field">
          <label>Notas de estilo</label>
          <textarea name="style_notes" placeholder="Ex.: tom consultivo, objetivo e acolhedor."></textarea>
        </div>
        <div class="toolbar-row">
          <button class="button" type="submit">Salvar mensagem aprovada</button>
        </div>
      </form>

      <div class="example-stack" style="margin-top: 20px;">
        ${
          examples.length
            ? examples
                .map(
                  (example) => `
                    <article class="example-card">
                      <strong>${escapeHtml(example.profile_name || "default")} · ${escapeHtml(example.scenario_type || "general")}</strong>
                      <p><span class="subtle">Cliente:</span> ${escapeHtml(example.customer_message || "")}</p>
                      <p style="margin-top: 10px;"><span class="subtle">Resposta:</span> ${escapeHtml(example.approved_reply || "")}</p>
                    </article>
                  `
                )
                .join("")
            : '<div class="empty-state">Nenhuma mensagem aprovada encontrada no escopo atual.</div>'
        }
      </div>
    </section>
  `
}

function renderAgendaView() {
  const profile = state.calendarProfile || {}
  const integration = profile.integration || {}
  const bookings = state.calendarBookings || []
  const queueEntries = state.queueEntries || []
  const queueOffers = state.queueOffers || []
  const timeline = state.calendarTimeline

  return `
    <section class="hero">
      <span class="brand-eyebrow">Agenda Console</span>
      <h2 class="hero-title">Agenda operacional com visão de monitoramento e ação manual.</h2>
      <p class="hero-copy">
        A agenda não fica escondida dentro do bot. Aqui você acompanha provider, disponibilidade, bookings, reagendamento, cancelamento e a fila que reduz faltas e ocupa vagas ociosas.
      </p>
    </section>

    <section class="grid-three">
      ${renderMetricCard("Provider", escapeHtml(integration.provider || "—"), "Integração ativa no escopo atual.")}
      ${renderMetricCard("Bookings", formatNumber(bookings.length), "Reservas visíveis no escopo selecionado.")}
      ${renderMetricCard("Fila ativa", formatNumber(queueEntries.length), "Clientes esperando encaixe ou antecipação.", "warm")}
    </section>

    <section class="card">
      <div class="section-header">
        <div>
          <h3 class="section-title">Calendário operacional</h3>
          <p class="section-copy">Visão mensal com clientes do dia e timeline semanal para inspeção detalhada.</p>
        </div>
        <span class="tag">${escapeHtml(timeline?.timezone || "America/Sao_Paulo")}</span>
      </div>
      ${state.agendaCalendar.mode === "month"
        ? renderMonthCalendar(bookings)
        : `<div class="calendar-month-toolbar">
             <div class="calendar-month-nav"><strong class="calendar-month-label">Semana atual</strong></div>
             <div class="calendar-mode-switch">
               <button type="button" class="${state.agendaCalendar.mode === "month" ? "is-active" : ""}" data-agenda-mode="month">Mês</button>
               <button type="button" class="${state.agendaCalendar.mode === "week" ? "is-active" : ""}" data-agenda-mode="week">Semana</button>
             </div>
           </div>
           ${renderTimelineBoard(timeline)}`}
    </section>

    <section class="grid-two">
      <article class="card">
        <div class="section-header">
          <div>
            <h3 class="section-title">Consultar disponibilidade</h3>
            <p class="section-copy">Valide a janela exata que o bot também usará no fluxo natural.</p>
          </div>
        </div>
        <form id="availability-form" class="form-grid">
          <div class="form-grid-three">
            <div class="field">
              <label>Data inicial</label>
              <input name="start_date" type="date" value="${escapeHtml(state.drafts.availability.startDate)}" />
            </div>
            <div class="field">
              <label>Data final</label>
              <input name="end_date" type="date" value="${escapeHtml(state.drafts.availability.endDate)}" />
            </div>
            <div class="field">
              <label>Duração (min)</label>
              <input name="duration_minutes" type="number" min="15" value="${escapeHtml(state.drafts.availability.durationMinutes)}" />
            </div>
          </div>
          <div class="form-grid-three">
            <div class="field">
              <label>Scheduling profile</label>
              <input name="scheduling_profile_key" value="${escapeHtml(state.drafts.availability.schedulingProfileKey)}" />
            </div>
            <div class="field">
              <label>Customer profile</label>
              <input name="customer_profile" value="${escapeHtml(state.drafts.availability.customerProfile)}" />
            </div>
            <div class="field">
              <label>Service name</label>
              <input name="service_name" value="${escapeHtml(state.drafts.availability.serviceName)}" />
            </div>
          </div>
          <div class="field">
            <label>Contexto do pedido</label>
            <textarea name="request_context">${escapeHtml(state.drafts.availability.requestContext)}</textarea>
          </div>
          <div class="toolbar-row">
            <button class="button" type="submit">Buscar slots</button>
          </div>
        </form>

        <div class="booking-stack" style="margin-top: 20px;">
          ${
            state.availabilityResult?.slots?.length
              ? state.availabilityResult.slots
                  .map(
                    (slot, index) => `
                      <article class="booking-card">
                        <strong>Slot ${index + 1}</strong>
                        <p>${escapeHtml(formatDateTime(slot.starts_at))} até ${escapeHtml(formatDateTime(slot.ends_at))}</p>
                        <div class="inline-actions">
                          <button class="button-ghost" type="button" data-use-slot="${index}">Usar no booking manual</button>
                        </div>
                      </article>
                    `
                  )
                  .join("")
              : '<div class="empty-state">Os slots encontrados aparecerão aqui.</div>'
          }
        </div>
      </article>

      <article class="card">
        <div class="section-header">
          <div>
            <h3 class="section-title">Criar booking manual</h3>
            <p class="section-copy">Use quando a equipe quiser registrar ou corrigir uma reserva manualmente.</p>
          </div>
        </div>
        <form id="booking-form" class="form-grid">
          <div class="form-grid-two">
            <div class="field">
              <label>Contact ID</label>
              <input name="contact_id" value="${escapeHtml(state.drafts.booking.contactId)}" />
            </div>
            <div class="field">
              <label>Título</label>
              <input name="title" value="${escapeHtml(state.drafts.booking.title)}" />
            </div>
          </div>
          <div class="form-grid-two">
            <div class="field">
              <label>Início</label>
              <input name="starts_at" type="datetime-local" value="${escapeHtml(state.drafts.booking.startsAt)}" />
            </div>
            <div class="field">
              <label>Fim</label>
              <input name="ends_at" type="datetime-local" value="${escapeHtml(state.drafts.booking.endsAt)}" />
            </div>
          </div>
          <div class="form-grid-three">
            <div class="field">
              <label>Timezone</label>
              <input name="timezone" value="${escapeHtml(state.drafts.booking.timezone)}" />
            </div>
            <div class="field">
              <label>Scheduling profile</label>
              <input name="scheduling_profile_key" value="${escapeHtml(state.drafts.booking.schedulingProfileKey)}" />
            </div>
            <div class="field">
              <label>Customer profile</label>
              <input name="customer_profile" value="${escapeHtml(state.drafts.booking.customerProfile)}" />
            </div>
          </div>
          <div class="field">
            <label>Descrição</label>
            <textarea name="description">${escapeHtml(state.drafts.booking.description)}</textarea>
          </div>
          <div class="toolbar-row">
            <button class="button" type="submit">Criar booking</button>
          </div>
        </form>
      </article>
    </section>

    <section class="grid-two">
      <article class="card">
        <div class="section-header">
          <div>
            <h3 class="section-title">Bookings atuais</h3>
            <p class="section-copy">Cada card permite reagendar ou cancelar sem sair da plataforma.</p>
          </div>
        </div>
        <div class="booking-stack">
          ${
            bookings.length
              ? bookings
                  .map(
                    (booking) => `
                      <article class="booking-card">
                        <strong>${escapeHtml(booking.title || "Agendamento")}</strong>
                        <p>${escapeHtml(formatDateTime(booking.starts_at))} até ${escapeHtml(formatDateTime(booking.ends_at))}</p>
                        <p>Contato: ${escapeHtml(booking.contact_id || "—")} · Perfil: ${escapeHtml(booking.customer_profile || "—")} · Scheduling profile: ${escapeHtml(booking.scheduling_profile_key || "—")}</p>
                        <form class="form-grid" data-reschedule-form="${booking.id}" style="margin-top: 12px;">
                          <div class="form-grid-two">
                            <div class="field">
                              <label>Novo início</label>
                              <input name="starts_at" type="datetime-local" value="${escapeHtml(toDatetimeLocal(booking.starts_at))}" />
                            </div>
                            <div class="field">
                              <label>Novo fim</label>
                              <input name="ends_at" type="datetime-local" value="${escapeHtml(toDatetimeLocal(booking.ends_at))}" />
                            </div>
                          </div>
                          <div class="form-grid-two">
                            <div class="field">
                              <label>Scheduling profile</label>
                              <input name="scheduling_profile_key" value="${escapeHtml(booking.scheduling_profile_key || "")}" />
                            </div>
                            <div class="field">
                              <label>Customer profile</label>
                              <input name="customer_profile" value="${escapeHtml(booking.customer_profile || "")}" />
                            </div>
                          </div>
                          <div class="inline-actions">
                            <button class="button-ghost" type="submit">Reagendar</button>
                            <button class="button-danger" type="button" data-cancel-booking="${booking.id}">Cancelar</button>
                          </div>
                        </form>
                      </article>
                    `
                  )
                  .join("")
              : '<div class="empty-state">Nenhum booking encontrado para o escopo atual.</div>'
          }
        </div>
      </article>

      <article class="card">
        <div class="section-header">
          <div>
            <h3 class="section-title">Fila de redução de faltas</h3>
            <p class="section-copy">Visão da fila prioritária e das ofertas abertas para ocupar vagas liberadas.</p>
          </div>
        </div>
        <div class="queue-stack">
          <article class="queue-item">
            <strong>Entradas na fila</strong>
            ${
              queueEntries.length
                ? queueEntries
                    .slice(0, 6)
                    .map(
                      (entry) => `
                        <p>${escapeHtml(entry.contact_id || "—")} · ${escapeHtml(entry.desired_title || "—")} · score ${escapeHtml(String(entry.priority_score ?? 0))}</p>
                      `
                    )
                    .join("")
                : '<p>Nenhuma entrada ativa no momento.</p>'
            }
          </article>
          <article class="queue-item">
            <strong>Ofertas abertas</strong>
            ${
              queueOffers.length
                ? queueOffers
                    .slice(0, 6)
                    .map(
                      (offer) => `
                        <p>Offer #${escapeHtml(String(offer.id))} · ${escapeHtml(offer.status || "—")} · ${escapeHtml(formatDateTime(offer.offered_starts_at))}</p>
                      `
                    )
                    .join("")
                : '<p>Sem ofertas em aberto agora.</p>'
            }
          </article>
        </div>
      </article>
    </section>
  `
}

function renderContactsView() {
  return `
    <section class="hero">
      <span class="brand-eyebrow">Contacts Desk</span>
      <h2 class="hero-title">Contexto do contato limpo deixa o bot mais preciso e a operação menos cega.</h2>
      <p class="hero-copy">
        Aqui você pesquisa por nome ou telefone, corrige identificação, define customer_profile e adiciona notas úteis para decisão humana e automação.
      </p>
    </section>

    <section class="card">
      <div class="section-header">
        <div>
          <h3 class="section-title">Busca e atualização</h3>
          <p class="section-copy">Use parte do nome, número ou o contato completo.</p>
        </div>
      </div>
      <form id="contact-search-form" class="form-grid-two">
        <div class="field">
          <label>Buscar contato</label>
          <input name="search" value="${escapeHtml(state.drafts.contactSearch)}" placeholder="Ex.: Maria, 5511..." />
        </div>
        <div class="inline-actions" style="align-self: end;">
          <button class="button" type="submit">Buscar</button>
          <button class="button-ghost" type="button" id="clear-contact-search">Limpar</button>
        </div>
      </form>
    </section>

    <section class="contact-stack">
      ${
        state.contacts.length
          ? state.contacts
              .map(
                (contact) => `
                  <article class="contact-card">
                    <strong>${escapeHtml(contact.contact_name || "Sem nome")} · ${escapeHtml(contact.contact_id || "—")}</strong>
                    <p>Pipeline: ${escapeHtml(contact.pipeline_stage || "—")} · Última classificação: ${escapeHtml(contact.last_classification || "—")}</p>
                    <form class="form-grid" data-contact-update="${escapeHtml(contact.contact_id)}" style="margin-top: 14px;">
                      <div class="form-grid-two">
                        <div class="field">
                          <label>Nome do contato</label>
                          <input name="contact_name" value="${escapeHtml(contact.contact_name || "")}" />
                        </div>
                        <div class="field">
                          <label>Customer profile</label>
                          <input name="customer_profile" value="${escapeHtml(contact.customer_profile || "")}" />
                        </div>
                      </div>
                      <div class="field">
                        <label>Notas operacionais</label>
                        <textarea name="notes">${escapeHtml(contact.notes || "")}</textarea>
                      </div>
                      <div class="inline-actions">
                        <button class="button-ghost" type="submit">Atualizar contato</button>
                      </div>
                    </form>
                  </article>
                `
              )
              .join("")
          : '<div class="empty-state">Nenhum contato carregado ainda para o escopo atual.</div>'
      }
    </section>
  `
}

function renderTrainingView() {
  const curation = state.trainingReview?.curation_summary || {}
  const split = state.trainingReview?.split_summary || {}
  const readiness = state.trainingReview?.readiness_summary || {}

  return `
    <section class="hero">
      <span class="brand-eyebrow">Training Ops</span>
      <h2 class="hero-title">O treino começa com governança de dados, não com upload cego.</h2>
      <p class="hero-copy">
        Faça dry-run de CRM ou base histórica, atualize customer_profile, gere message_examples curados e só depois avance para fine-tuning. Isso reduz ruído, sensível exposto e treino incoerente.
      </p>
    </section>

    <section class="grid-three">
      ${renderMetricCard("Curados", formatNumber(curation.total_curated_examples), "Exemplos aprovados e elegíveis no escopo atual.")}
      ${renderMetricCard("Train", formatNumber(split.train_examples), "Volume atual de treino após split.")}
      ${renderMetricCard("Ready", readiness.is_ready_for_finetuning ? "Sim" : "Não", "Situação atual de prontidão para fine-tuning.", "warm")}
    </section>

    <section class="grid-two">
      <article class="card">
        <div class="section-header">
          <div>
            <h3 class="section-title">Importar base histórica</h3>
            <p class="section-copy">CSV ou JSON curado, com dry-run antes de persistir.</p>
          </div>
        </div>
        <form id="training-import-form" class="form-grid">
          <div class="form-grid-three">
            <div class="field">
              <label>Formato</label>
              <select name="format">
                <option value="csv" ${state.drafts.trainingImport.format === "csv" ? "selected" : ""}>CSV</option>
                <option value="json" ${state.drafts.trainingImport.format === "json" ? "selected" : ""}>JSON</option>
              </select>
            </div>
            <div class="field">
              <label>Delimiter</label>
              <input name="delimiter" value="${escapeHtml(state.drafts.trainingImport.delimiter)}" />
            </div>
            <div class="field">
              <label>Quality padrão</label>
              <input name="default_quality_score" type="number" min="0" max="10" step="0.1" value="${escapeHtml(state.drafts.trainingImport.defaultQualityScore)}" />
            </div>
          </div>
          <div class="form-grid-two">
            <div class="field">
              <label>Profile padrão</label>
              <input name="default_profile_name" value="${escapeHtml(state.drafts.trainingImport.defaultProfileName)}" />
            </div>
            <div class="field">
              <label>Scenario padrão</label>
              <input name="default_scenario_type" value="${escapeHtml(state.drafts.trainingImport.defaultScenarioType)}" />
            </div>
          </div>
          <div class="field">
            <label>Conteúdo bruto</label>
            <textarea name="raw_text" placeholder="Cole aqui CSV ou JSON curado">${escapeHtml(state.drafts.trainingImport.rawText)}</textarea>
          </div>
          <div class="quick-row">
            <label class="meta-chip"><input name="dry_run" type="checkbox" ${state.drafts.trainingImport.dryRun ? "checked" : ""} /> Dry run</label>
            <label class="meta-chip"><input name="update_contacts" type="checkbox" ${state.drafts.trainingImport.updateContacts ? "checked" : ""} /> Atualizar contatos</label>
            <label class="meta-chip"><input name="create_message_examples" type="checkbox" ${state.drafts.trainingImport.createMessageExamples ? "checked" : ""} /> Criar message examples</label>
            <label class="meta-chip"><input name="auto_promote_eligible_examples" type="checkbox" ${state.drafts.trainingImport.autoPromoteEligibleExamples ? "checked" : ""} /> Auto-promover elegíveis</label>
          </div>
          <div class="toolbar-row">
            <button class="button" type="submit">Executar importação</button>
            <button class="button-ghost" type="button" id="refresh-training-data">Atualizar relatório</button>
          </div>
        </form>
      </article>

      <article class="card">
        <div class="section-header">
          <div>
            <h3 class="section-title">Dataset review e jobs</h3>
            <p class="section-copy">Leitura rápida do que está pronto e do histórico recente de fine-tuning.</p>
          </div>
          <button class="button-ghost" id="create-finetuning-job">Criar job</button>
        </div>

        <div class="job-stack">
          <article class="job-card">
            <strong>Readiness</strong>
            <p>Elegíveis: ${formatNumber(readiness.eligible_examples_count || 0)} · mínimo: ${formatNumber(readiness.minimum_required_examples || 0)} · faltando: ${formatNumber(readiness.missing_examples_count || 0)}</p>
          </article>

          ${
            state.finetuningJobs.length
              ? state.finetuningJobs
                  .map(
                    (job) => `
                      <article class="job-card">
                        <strong>${escapeHtml(job.job_id || job.id || "Job")}</strong>
                        <p>Status: ${escapeHtml(job.status || "—")} · Modelo: ${escapeHtml(job.model || job.base_model || "—")}</p>
                      </article>
                    `
                  )
                  .join("")
              : '<div class="empty-state">Nenhum job de fine-tuning listado ainda.</div>'
          }
        </div>
      </article>
    </section>

    ${
      state.trainingImportResult
        ? `
          <section class="card">
            <div class="section-header">
              <div>
                <h3 class="section-title">Resultado da última importação</h3>
                <p class="section-copy">Preview da operação executada pela plataforma.</p>
              </div>
            </div>
            <div class="grid-three">
              ${renderMetricCard("Registros", formatNumber(state.trainingImportResult.parsed_records), "Linhas aceitas pelo parser.")}
              ${renderMetricCard("Contatos", formatNumber(state.trainingImportResult.contacts_to_update), "Contatos que serão ou foram atualizados.")}
              ${renderMetricCard("Examples", formatNumber(state.trainingImportResult.message_examples_to_create), "Exemplos criados ou previstos.", "warm")}
            </div>
            <div class="example-stack" style="margin-top: 18px;">
              ${
                (state.trainingImportResult.preview || []).length
                  ? state.trainingImportResult.preview
                      .map(
                        (row) => `
                          <article class="example-card">
                            <strong>Linha ${escapeHtml(String(row.row_number))} · ${escapeHtml(row.contact_name || row.contact_id || "—")}</strong>
                            <p>Profile: ${escapeHtml(row.profile_name || "—")} · Scenario: ${escapeHtml(row.scenario_type || "—")} · Customer profile: ${escapeHtml(row.customer_profile || "—")}</p>
                          </article>
                        `
                      )
                      .join("")
                  : '<div class="empty-state">Nenhum preview disponível.</div>'
              }
            </div>
          </section>
        `
        : ""
    }
  `
}

function renderCurrentView() {
  if (state.view === "dashboard") return renderDashboardView()
  if (state.view === "studio") return renderStudioView()
  if (state.view === "flows") return renderFlowsView()
  if (state.view === "agenda") return renderAgendaView()
  if (state.view === "contacts") return renderContactsView()
  if (state.view === "training") return renderTrainingView()
  return ""
}

function renderSettingsModal() {
  return `
    <div class="modal-backdrop">
      <div class="modal">
        <div class="section-header">
          <div>
            <h3 class="section-title">${state.session ? "Sessão ativa da plataforma" : "Entrar na plataforma"}</h3>
            <p class="section-copy">
              ${state.session
                ? "A plataforma já está autenticada com cookie HTTP-only. Você pode trocar o ator da sessão ou encerrar o acesso."
                : "O token administrativo é usado apenas para emitir uma sessão segura da plataforma. Depois disso, as chamadas usam cookie HTTP-only."}
            </p>
          </div>
          <button class="button-ghost" id="close-settings">Fechar</button>
        </div>
        <form id="settings-form" class="form-grid">
          <div class="field">
            <label>Admin token</label>
            <input name="adminToken" type="password" value="${escapeHtml(state.drafts.auth.adminToken)}" placeholder="Obrigatório quando ENABLE_ADMIN_AUTH=true" />
          </div>
          <div class="field">
            <label>Admin actor</label>
            <input name="adminActor" value="${escapeHtml(state.drafts.auth.adminActor || state.config.adminActor)}" />
          </div>
          <div class="field">
            <label>Company slug preferido</label>
            <input name="companySlug" value="${escapeHtml(state.config.companySlug)}" placeholder="Ex.: empresa-x" />
          </div>
          ${
            state.session
              ? `<div class="message-card"><strong>Sessão atual</strong><p>Papel: ${escapeHtml(state.session.role)} · Ator: ${escapeHtml(state.session.actor)} · Expira em: ${escapeHtml(formatDateTime(state.session.expires_at * 1000))}</p></div>`
              : ""
          }
          ${
            state.errorMessage
              ? `<div class="message-card"><strong>Último erro</strong><p>${escapeHtml(state.errorMessage)}</p></div>`
              : ""
          }
          <div class="toolbar-row">
            <button class="button" type="submit">${state.session ? "Renovar sessão" : "Entrar"}</button>
            ${state.session ? '<button class="button-danger" type="button" id="logout-session-modal">Encerrar sessão</button>' : ""}
          </div>
        </form>
      </div>
    </div>
  `
}

function renderToasts() {
  return `
    <div class="toast-stack">
      ${state.toasts
        .map(
          (toast) => `
            <div class="toast ${toast.type}">
              ${escapeHtml(toast.message)}
            </div>
          `
        )
        .join("")}
    </div>
  `
}

function renderLoadingBanner() {
  if (!state.loading) {
    return ""
  }
  return `
    <div class="pill" style="position: fixed; left: 50%; top: 20px; transform: translateX(-50%); z-index: 150; background: rgba(8, 16, 24, 0.95);">
      <span class="loader"></span>
      <strong>${escapeHtml(state.loadingMessage || "Carregando")}</strong>
    </div>
  `
}

function renderApp() {
  root.innerHTML = `
    <div class="shell">
      ${renderSidebar()}
      <div class="workspace">
        ${renderTopbar()}
        <main class="view">${renderCurrentView()}</main>
        <p class="footer-note">Project WAPP Platform · camada dedicada para operação premium, teste controlado e agenda viva.</p>
      </div>
    </div>
    ${state.settingsOpen ? renderSettingsModal() : ""}
    ${renderToasts()}
    ${renderLoadingBanner()}
  `
  bindEvents()
}

function bindEvents() {
  document.querySelectorAll("[data-view]").forEach((button) => {
    button.addEventListener("click", async () => {
      state.view = button.dataset.view
      renderApp()
      await refreshCurrentView()
    })
  })

  document.querySelectorAll("[data-jump-view]").forEach((button) => {
    button.addEventListener("click", async () => {
      state.view = button.dataset.jumpView
      renderApp()
      await refreshCurrentView()
    })
  })

  document.querySelectorAll("[data-refresh-view]").forEach((button) => {
    button.addEventListener("click", async () => {
      await refreshCurrentView()
    })
  })

  const openSettings = document.getElementById("open-settings")
  if (openSettings) {
    openSettings.addEventListener("click", () => {
      state.settingsOpen = true
      renderApp()
    })
  }

  const closeSettings = document.getElementById("close-settings")
  if (closeSettings) {
    closeSettings.addEventListener("click", () => {
      state.settingsOpen = false
      renderApp()
    })
  }

  const settingsForm = document.getElementById("settings-form")
  if (settingsForm) {
    settingsForm.addEventListener("submit", async (event) => {
      event.preventDefault()
      const formData = new FormData(settingsForm)
      state.drafts.auth.adminToken = String(formData.get("adminToken") || "").trim()
      state.drafts.auth.adminActor = String(formData.get("adminActor") || "").trim()
      state.config.adminActor = state.drafts.auth.adminActor
      state.config.companySlug = String(formData.get("companySlug") || "").trim()
      persistConfig()
      state.loading = true
      state.loadingMessage = "Autenticando sessão da plataforma..."
      renderApp()
      try {
        await loginPlatformSession()
        state.settingsOpen = false
        pushToast("Sessão da plataforma autenticada.")
        await refreshCurrentView()
      } catch (error) {
        state.errorMessage = summarizeError(error)
        pushToast(state.errorMessage, "error")
      } finally {
        state.loading = false
        state.loadingMessage = ""
        renderApp()
      }
    })
  }

  const logoutSession = document.getElementById("logout-session")
  if (logoutSession) {
    logoutSession.addEventListener("click", async () => {
      state.loading = true
      state.loadingMessage = "Encerrando sessão..."
      renderApp()
      try {
        await logoutPlatformSession()
        state.settingsOpen = true
        pushToast("Sessão encerrada.")
      } catch (error) {
        pushToast(summarizeError(error), "error")
      } finally {
        state.loading = false
        state.loadingMessage = ""
        renderApp()
      }
    })
  }

  const logoutSessionModal = document.getElementById("logout-session-modal")
  if (logoutSessionModal) {
    logoutSessionModal.addEventListener("click", async () => {
      state.loading = true
      state.loadingMessage = "Encerrando sessão..."
      renderApp()
      try {
        await logoutPlatformSession()
        state.settingsOpen = true
        pushToast("Sessão encerrada.")
      } catch (error) {
        pushToast(summarizeError(error), "error")
      } finally {
        state.loading = false
        state.loadingMessage = ""
        renderApp()
      }
    })
  }

  const companySelect = document.getElementById("company-select")
  if (companySelect) {
    companySelect.value = getSelectedCompany()?.slug || "__global__"
    companySelect.addEventListener("change", async (event) => {
      const selectedSlug = event.target.value
      if (selectedSlug === "__global__") {
        state.config.companyId = ""
        state.config.companySlug = ""
      } else {
        const selectedCompany = state.companies.find((company) => company.slug === selectedSlug)
        state.config.companyId = selectedCompany ? String(selectedCompany.id) : ""
        state.config.companySlug = selectedSlug
      }
      persistConfig()
      state.companyContext = null
      state.trainingImportResult = null
      await refreshCurrentView()
    })
  }

  bindStudioEvents()
  bindFlowEvents()
  bindAgendaEvents()
  bindContactsEvents()
  bindTrainingEvents()
}

function bindStudioEvents() {
  const studioForm = document.getElementById("studio-form")
  if (studioForm) {
    studioForm.addEventListener("submit", async (event) => {
      event.preventDefault()
      const formData = new FormData(studioForm)
      state.drafts.studio.contactId = String(formData.get("contactId") || "").trim()
      state.drafts.studio.contactName = String(formData.get("contactName") || "").trim()
      state.drafts.studio.message = String(formData.get("message") || "").trim()

      if (!state.drafts.studio.contactId || !state.drafts.studio.message) {
        pushToast("Informe contact_id e mensagem para testar o runtime.", "error")
        return
      }

      state.loading = true
      state.loadingMessage = "Enviando mensagem para o runtime..."
      renderApp()
      try {
        const result = await apiRequest("/messages/incoming", {
          method: "POST",
          admin: false,
          params: getCompanyParams(),
          body: {
            contact_id: state.drafts.studio.contactId,
            contact_name: state.drafts.studio.contactName || null,
            message: state.drafts.studio.message,
          },
        })
        state.simulator.transcript.push(
          { role: "user", text: state.drafts.studio.message },
          { role: "assistant", text: result.reply || "(sem resposta)", meta: result }
        )
        state.simulator.lastResult = result
        state.drafts.studio.message = ""
        pushToast("Simulação concluída com o runtime real.")
      } catch (error) {
        pushToast(summarizeError(error), "error")
      } finally {
        state.loading = false
        state.loadingMessage = ""
        renderApp()
      }
    })
  }

  document.querySelectorAll("[data-studio-prompt]").forEach((button) => {
    button.addEventListener("click", () => {
      state.drafts.studio.message = button.dataset.studioPrompt || ""
      renderApp()
    })
  })

  const clearStudioLog = document.getElementById("clear-studio-log")
  if (clearStudioLog) {
    clearStudioLog.addEventListener("click", () => {
      state.simulator.transcript = []
      state.simulator.lastResult = null
      renderApp()
    })
  }

  const useOperatorContact = document.getElementById("use-operator-contact")
  if (useOperatorContact) {
    useOperatorContact.addEventListener("click", () => {
      const operatorId = state.companyContext?.internal_operation?.operator_contact_ids?.[0]
      if (operatorId) {
        state.drafts.studio.contactId = operatorId
        state.drafts.studio.contactName = "Operador interno"
        renderApp()
      }
    })
  }
}

function bindFlowEvents() {
  const addPipelineStage = document.getElementById("add-pipeline-stage")
  if (addPipelineStage) {
    addPipelineStage.addEventListener("click", () => {
      const context = ensureConversationPipelines(state.companyContext || {})
      const nextIndex = context.conversation_pipelines.length + 1
      const newKey = `stage_${nextIndex}`
      context.conversation_pipelines.push({
        key: newKey,
        label: "Nova etapa",
        objective: "",
        trigger_terms: [],
        success_criteria: "",
        handoff_when: "",
        preferred_cta: "",
        next_keys: [],
        canvas_position: {
          x: 120 + ((nextIndex - 1) % 3) * 320,
          y: 90 + Math.floor((nextIndex - 1) / 3) * 220,
        },
      })
      state.companyContext = context
      state.flowEditor.selectedPipelineKey = newKey
      renderApp()
    })
  }

  const updateEdgePathsForKey = (movedKey) => {
    const context = ensureConversationPipelines(state.companyContext || {})
    const pipelines = context.conversation_pipelines || []
    const lookup = new Map(pipelines.map((p) => [p.key, p]))
    document
      .querySelectorAll(`.flow-edge-path[data-edge-source="${CSS.escape(movedKey)}"], .flow-edge-path[data-edge-target="${CSS.escape(movedKey)}"]`)
      .forEach((path) => {
        const source = lookup.get(path.dataset.edgeSource)
        const target = lookup.get(path.dataset.edgeTarget)
        if (!source || !target) return
        const startX = Number(source.canvas_position?.x || 0) + 260
        const startY = Number(source.canvas_position?.y || 0) + 70
        const endX = Number(target.canvas_position?.x || 0)
        const endY = Number(target.canvas_position?.y || 0) + 70
        const midX = startX + (endX - startX) / 2
        path.setAttribute("d", `M ${startX} ${startY} C ${midX} ${startY}, ${midX} ${endY}, ${endX} ${endY}`)
      })
  }

  document.querySelectorAll("[data-select-pipeline]").forEach((button) => {
    button.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return
      if (!(event.target instanceof HTMLElement) || event.target.closest("input, textarea, button:not([data-select-pipeline])")) {
        return
      }
      const pipelineKey = button.dataset.selectPipeline || ""
      const context = ensureConversationPipelines(state.companyContext || {})
      const pipeline = context.conversation_pipelines.find((item) => item.key === pipelineKey)
      if (!pipeline) return

      const startX = event.clientX
      const startY = event.clientY
      const originX = Number(pipeline.canvas_position?.x || 0)
      const originY = Number(pipeline.canvas_position?.y || 0)
      let dragging = false
      let currentX = originX
      let currentY = originY

      try {
        button.setPointerCapture(event.pointerId)
      } catch (_) {}
      event.preventDefault()

      const onMove = (moveEvent) => {
        const dx = moveEvent.clientX - startX
        const dy = moveEvent.clientY - startY
        if (!dragging && Math.hypot(dx, dy) < 4) return
        dragging = true
        button.classList.add("dragging")
        currentX = Math.max(20, originX + dx)
        currentY = Math.max(20, originY + dy)
        button.style.left = `${currentX}px`
        button.style.top = `${currentY}px`
        pipeline.canvas_position = { x: currentX, y: currentY }
        updateEdgePathsForKey(pipelineKey)
      }

      const onUp = () => {
        button.removeEventListener("pointermove", onMove)
        button.removeEventListener("pointerup", onUp)
        button.removeEventListener("pointercancel", onUp)
        button.classList.remove("dragging")
        try { button.releasePointerCapture(event.pointerId) } catch (_) {}

        if (dragging) {
          const targetContext = ensureConversationPipelines(state.companyContext || {})
          targetContext.conversation_pipelines = targetContext.conversation_pipelines.map((item) => (
            item.key === pipelineKey
              ? { ...item, canvas_position: { x: currentX, y: currentY } }
              : item
          ))
          state.companyContext = targetContext
          const inspectorX = document.querySelector('#pipeline-inspector-form [name="canvas_x"]')
          const inspectorY = document.querySelector('#pipeline-inspector-form [name="canvas_y"]')
          if (inspectorX) inspectorX.value = String(currentX)
          if (inspectorY) inspectorY.value = String(currentY)
          if (state.flowEditor.selectedPipelineKey !== pipelineKey) {
            state.flowEditor.selectedPipelineKey = pipelineKey
            renderApp()
          }
        } else {
          if (state.flowEditor.selectedPipelineKey !== pipelineKey) {
            state.flowEditor.selectedPipelineKey = pipelineKey
            renderApp()
          }
        }
      }

      button.addEventListener("pointermove", onMove)
      button.addEventListener("pointerup", onUp)
      button.addEventListener("pointercancel", onUp)
    })
  })

  document.querySelectorAll("[data-remove-pipeline-key]").forEach((button) => {
    button.addEventListener("click", () => {
      const pipelineKey = button.dataset.removePipelineKey || ""
      const context = ensureConversationPipelines(state.companyContext || {})
      context.conversation_pipelines = context.conversation_pipelines
        .filter((pipeline) => pipeline.key !== pipelineKey)
        .map((pipeline) => ({
          ...pipeline,
          next_keys: (pipeline.next_keys || []).filter((nextKey) => nextKey !== pipelineKey),
        }))
      state.companyContext = context
      state.flowEditor.selectedPipelineKey = context.conversation_pipelines[0]?.key || null
      renderApp()
    })
  })

  const pipelineInspector = document.getElementById("pipeline-inspector-form")
  if (pipelineInspector) {
    pipelineInspector.addEventListener("submit", (event) => {
      event.preventDefault()
      const context = ensureConversationPipelines(state.companyContext || {})
      const formData = new FormData(pipelineInspector)
      const key = String(formData.get("key") || "").trim()
      context.conversation_pipelines = context.conversation_pipelines.map((pipeline) => {
        if (pipeline.key !== key) {
          return pipeline
        }
        return {
          ...pipeline,
          label: String(formData.get("label") || "").trim(),
          objective: String(formData.get("objective") || "").trim(),
          trigger_terms: String(formData.get("trigger_terms") || "")
            .split(",")
            .map((item) => item.trim())
            .filter(Boolean),
          success_criteria: String(formData.get("success_criteria") || "").trim(),
          handoff_when: String(formData.get("handoff_when") || "").trim(),
          preferred_cta: String(formData.get("preferred_cta") || "").trim(),
          next_keys: String(formData.get("next_keys") || "")
            .split(",")
            .map((item) => item.trim())
            .filter(Boolean),
          canvas_position: {
            x: Number(formData.get("canvas_x") || 0),
            y: Number(formData.get("canvas_y") || 0),
          },
        }
      })
      state.companyContext = context
      renderApp()
      pushToast("Etapa atualizada no canvas. Salve para persistir no backend.")
    })
  }

  const savePipelines = document.getElementById("save-pipelines")
  if (savePipelines) {
    savePipelines.addEventListener("click", async () => {
      const context = ensureConversationPipelines(state.companyContext || {})

      state.loading = true
      state.loadingMessage = "Salvando pipelines no company context..."
      renderApp()
      try {
        const saved = await apiRequest("/admin/company-context", {
          method: "PUT",
          params: getCompanyContextParams(),
          body: { data: context },
        })
        state.companyContext = ensureConversationPipelines(saved)
        pushToast("Pipelines salvos no company context.")
      } catch (error) {
        pushToast(summarizeError(error), "error")
      } finally {
        state.loading = false
        state.loadingMessage = ""
        renderApp()
      }
    })
  }

  const exampleForm = document.getElementById("example-form")
  if (exampleForm) {
    exampleForm.addEventListener("submit", async (event) => {
      event.preventDefault()
      const formData = new FormData(exampleForm)
      const payload = {
        profile_name: String(formData.get("profile_name") || "").trim(),
        scenario_type: String(formData.get("scenario_type") || "").trim(),
        customer_message: String(formData.get("customer_message") || "").trim(),
        approved_reply: String(formData.get("approved_reply") || "").trim(),
        style_notes: String(formData.get("style_notes") || "").trim() || null,
        quality_score: Number(formData.get("quality_score") || 0),
        used_for_training: true,
        active: true,
      }

      if (!payload.profile_name || !payload.scenario_type || !payload.customer_message || !payload.approved_reply) {
        pushToast("Preencha profile, scenario, mensagem do cliente e resposta aprovada.", "error")
        return
      }

      state.loading = true
      state.loadingMessage = "Salvando mensagem aprovada..."
      renderApp()
      try {
        await apiRequest("/admin/message-examples", {
          method: "POST",
          params: getAdminCompanyParams(),
          body: payload,
        })
        pushToast("Mensagem aprovada criada.")
        await loadFlowData()
      } catch (error) {
        pushToast(summarizeError(error), "error")
      } finally {
        state.loading = false
        state.loadingMessage = ""
        renderApp()
      }
    })
  }
}

function bindAgendaEvents() {
  document.querySelectorAll("[data-agenda-mode]").forEach((button) => {
    button.addEventListener("click", () => {
      const mode = button.dataset.agendaMode
      if (mode && mode !== state.agendaCalendar.mode) {
        state.agendaCalendar.mode = mode
        renderApp()
      }
    })
  })

  document.querySelectorAll("[data-month-nav]").forEach((button) => {
    button.addEventListener("click", () => {
      const action = button.dataset.monthNav
      const cursor = parseDateKey(state.agendaCalendar.monthCursor) || new Date()
      let next = new Date(cursor.getFullYear(), cursor.getMonth(), 1)
      if (action === "prev") next.setMonth(next.getMonth() - 1)
      else if (action === "next") next.setMonth(next.getMonth() + 1)
      else if (action === "today") {
        const now = new Date()
        next = new Date(now.getFullYear(), now.getMonth(), 1)
      }
      state.agendaCalendar.monthCursor = formatDateKey(next)
      renderApp()
    })
  })

  document.querySelectorAll("[data-month-day]").forEach((cell) => {
    cell.addEventListener("click", () => {
      const key = cell.dataset.monthDay || null
      state.agendaCalendar.selectedDay = state.agendaCalendar.selectedDay === key ? null : key
      renderApp()
    })
  })

  const availabilityForm = document.getElementById("availability-form")
  if (availabilityForm) {
    availabilityForm.addEventListener("submit", async (event) => {
      event.preventDefault()
      const formData = new FormData(availabilityForm)
      state.drafts.availability = {
        startDate: String(formData.get("start_date") || ""),
        endDate: String(formData.get("end_date") || ""),
        durationMinutes: String(formData.get("duration_minutes") || ""),
        limit: "6",
        schedulingProfileKey: String(formData.get("scheduling_profile_key") || ""),
        customerProfile: String(formData.get("customer_profile") || ""),
        serviceName: String(formData.get("service_name") || ""),
        requestContext: String(formData.get("request_context") || ""),
      }

      state.loading = true
      state.loadingMessage = "Buscando disponibilidade..."
      renderApp()
      try {
        state.availabilityResult = await apiRequest("/admin/calendar/availability/query", {
          method: "POST",
          params: getAdminCompanyParams(),
          body: {
            start_date: state.drafts.availability.startDate,
            end_date: state.drafts.availability.endDate,
            duration_minutes: Number(state.drafts.availability.durationMinutes || 60),
            limit: 6,
            scheduling_profile_key: state.drafts.availability.schedulingProfileKey || null,
            customer_profile: state.drafts.availability.customerProfile || null,
            service_name: state.drafts.availability.serviceName || null,
            request_context: state.drafts.availability.requestContext || null,
          },
        })
        pushToast("Disponibilidade atualizada.")
      } catch (error) {
        pushToast(summarizeError(error), "error")
      } finally {
        state.loading = false
        state.loadingMessage = ""
        renderApp()
      }
    })
  }

  document.querySelectorAll("[data-use-slot]").forEach((button) => {
    button.addEventListener("click", () => {
      const index = Number(button.dataset.useSlot)
      const slot = state.availabilityResult?.slots?.[index]
      if (!slot) {
        return
      }
      state.drafts.booking.startsAt = toDatetimeLocal(slot.starts_at)
      state.drafts.booking.endsAt = toDatetimeLocal(slot.ends_at)
      state.drafts.booking.timezone = slot.timezone || "America/Sao_Paulo"
      state.drafts.booking.schedulingProfileKey = state.drafts.availability.schedulingProfileKey
      state.drafts.booking.customerProfile = state.drafts.availability.customerProfile
      renderApp()
      pushToast("Slot aplicado ao formulário de booking.")
    })
  })

  const bookingForm = document.getElementById("booking-form")
  if (bookingForm) {
    bookingForm.addEventListener("submit", async (event) => {
      event.preventDefault()
      const formData = new FormData(bookingForm)
      state.drafts.booking = {
        contactId: String(formData.get("contact_id") || "").trim(),
        title: String(formData.get("title") || "").trim(),
        startsAt: String(formData.get("starts_at") || ""),
        endsAt: String(formData.get("ends_at") || ""),
        timezone: String(formData.get("timezone") || "America/Sao_Paulo").trim(),
        description: String(formData.get("description") || "").trim(),
        schedulingProfileKey: String(formData.get("scheduling_profile_key") || "").trim(),
        customerProfile: String(formData.get("customer_profile") || "").trim(),
      }

      if (!state.drafts.booking.title || !state.drafts.booking.startsAt || !state.drafts.booking.endsAt) {
        pushToast("Título, início e fim são obrigatórios para criar booking.", "error")
        return
      }

      state.loading = true
      state.loadingMessage = "Criando booking..."
      renderApp()
      try {
        await apiRequest("/admin/calendar/bookings", {
          method: "POST",
          params: getAdminCompanyParams(),
          body: {
            contact_id: state.drafts.booking.contactId || null,
            title: state.drafts.booking.title,
            starts_at: normalizeDatetimeLocal(state.drafts.booking.startsAt),
            ends_at: normalizeDatetimeLocal(state.drafts.booking.endsAt),
            timezone: state.drafts.booking.timezone || null,
            description: state.drafts.booking.description || null,
            scheduling_profile_key: state.drafts.booking.schedulingProfileKey || null,
            customer_profile: state.drafts.booking.customerProfile || null,
          },
        })
        pushToast("Booking criado com sucesso.")
        await loadAgendaData()
      } catch (error) {
        pushToast(summarizeError(error), "error")
      } finally {
        state.loading = false
        state.loadingMessage = ""
        renderApp()
      }
    })
  }

  document.querySelectorAll("[data-reschedule-form]").forEach((form) => {
    form.addEventListener("submit", async (event) => {
      event.preventDefault()
      const bookingId = Number(form.dataset.rescheduleForm)
      const formData = new FormData(form)
      state.loading = true
      state.loadingMessage = "Reagendando booking..."
      renderApp()
      try {
        await apiRequest(`/admin/calendar/bookings/${bookingId}/reschedule`, {
          method: "POST",
          body: {
            starts_at: normalizeDatetimeLocal(String(formData.get("starts_at") || "")),
            ends_at: normalizeDatetimeLocal(String(formData.get("ends_at") || "")),
            scheduling_profile_key: String(formData.get("scheduling_profile_key") || "").trim() || null,
            customer_profile: String(formData.get("customer_profile") || "").trim() || null,
          },
        })
        pushToast(`Booking #${bookingId} reagendado.`)
        await loadAgendaData()
      } catch (error) {
        pushToast(summarizeError(error), "error")
      } finally {
        state.loading = false
        state.loadingMessage = ""
        renderApp()
      }
    })
  })

  document.querySelectorAll("[data-cancel-booking]").forEach((button) => {
    button.addEventListener("click", async () => {
      const bookingId = Number(button.dataset.cancelBooking)
      state.loading = true
      state.loadingMessage = "Cancelando booking..."
      renderApp()
      try {
        await apiRequest(`/admin/calendar/bookings/${bookingId}/cancel`, {
          method: "POST",
          body: {
            trigger_queue_recovery: true,
            reason: "Cancelado manualmente pela plataforma dedicada.",
          },
        })
        pushToast(`Booking #${bookingId} cancelado.`)
        await loadAgendaData()
      } catch (error) {
        pushToast(summarizeError(error), "error")
      } finally {
        state.loading = false
        state.loadingMessage = ""
        renderApp()
      }
    })
  })

  document.querySelectorAll("[data-scroll-booking]").forEach((button) => {
    button.addEventListener("click", () => {
      const bookingId = button.dataset.scrollBooking
      const target = document.querySelector(`[data-reschedule-form="${bookingId}"]`)
      target?.scrollIntoView({ behavior: "smooth", block: "center" })
    })
  })
}

function bindContactsEvents() {
  const contactSearchForm = document.getElementById("contact-search-form")
  if (contactSearchForm) {
    contactSearchForm.addEventListener("submit", async (event) => {
      event.preventDefault()
      const formData = new FormData(contactSearchForm)
      state.drafts.contactSearch = String(formData.get("search") || "").trim()
      await refreshCurrentView({ silent: false })
    })
  }

  const clearContactSearch = document.getElementById("clear-contact-search")
  if (clearContactSearch) {
    clearContactSearch.addEventListener("click", async () => {
      state.drafts.contactSearch = ""
      await refreshCurrentView()
    })
  }

  document.querySelectorAll("[data-contact-update]").forEach((form) => {
    form.addEventListener("submit", async (event) => {
      event.preventDefault()
      const contactId = form.dataset.contactUpdate
      const formData = new FormData(form)
      state.loading = true
      state.loadingMessage = "Atualizando contato..."
      renderApp()
      try {
        await apiRequest(`/admin/contacts/${contactId}`, {
          method: "PATCH",
          params: getAdminCompanyParams(),
          body: {
            contact_name: String(formData.get("contact_name") || "").trim() || null,
            customer_profile: String(formData.get("customer_profile") || "").trim() || null,
            notes: String(formData.get("notes") || "").trim() || null,
          },
        })
        pushToast(`Contato ${contactId} atualizado.`)
        await loadContactsData()
      } catch (error) {
        pushToast(summarizeError(error), "error")
      } finally {
        state.loading = false
        state.loadingMessage = ""
        renderApp()
      }
    })
  })
}

function bindTrainingEvents() {
  const trainingImportForm = document.getElementById("training-import-form")
  if (trainingImportForm) {
    trainingImportForm.addEventListener("submit", async (event) => {
      event.preventDefault()
      const formData = new FormData(trainingImportForm)
      state.drafts.trainingImport = {
        format: String(formData.get("format") || "csv"),
        rawText: String(formData.get("raw_text") || ""),
        delimiter: String(formData.get("delimiter") || ","),
        dryRun: formData.get("dry_run") === "on",
        updateContacts: formData.get("update_contacts") === "on",
        createMessageExamples: formData.get("create_message_examples") === "on",
        autoPromoteEligibleExamples: formData.get("auto_promote_eligible_examples") === "on",
        defaultProfileName: String(formData.get("default_profile_name") || "default"),
        defaultScenarioType: String(formData.get("default_scenario_type") || "general"),
        defaultQualityScore: String(formData.get("default_quality_score") || "7"),
      }

      if (!state.drafts.trainingImport.rawText.trim()) {
        pushToast("Cole a base histórica ou export curado antes de rodar a importação.", "error")
        return
      }

      state.loading = true
      state.loadingMessage = "Executando importação histórica..."
      renderApp()
      try {
        state.trainingImportResult = await apiRequest("/admin/training-import", {
          method: "POST",
          params: getAdminCompanyParams(),
          body: {
            format: state.drafts.trainingImport.format,
            raw_text: state.drafts.trainingImport.rawText,
            delimiter: state.drafts.trainingImport.delimiter,
            dry_run: state.drafts.trainingImport.dryRun,
            update_contacts: state.drafts.trainingImport.updateContacts,
            create_message_examples: state.drafts.trainingImport.createMessageExamples,
            auto_promote_eligible_examples: state.drafts.trainingImport.autoPromoteEligibleExamples,
            default_profile_name: state.drafts.trainingImport.defaultProfileName,
            default_scenario_type: state.drafts.trainingImport.defaultScenarioType,
            default_quality_score: Number(state.drafts.trainingImport.defaultQualityScore || 7),
          },
        })
        pushToast(state.trainingImportResult?.dry_run ? "Dry-run concluído." : "Importação persistida com sucesso.")
        await loadTrainingData()
      } catch (error) {
        pushToast(summarizeError(error), "error")
      } finally {
        state.loading = false
        state.loadingMessage = ""
        renderApp()
      }
    })
  }

  const refreshTrainingData = document.getElementById("refresh-training-data")
  if (refreshTrainingData) {
    refreshTrainingData.addEventListener("click", async () => {
      await refreshCurrentView()
    })
  }

  const createFinetuningJob = document.getElementById("create-finetuning-job")
  if (createFinetuningJob) {
    createFinetuningJob.addEventListener("click", async () => {
      state.loading = true
      state.loadingMessage = "Criando job de fine-tuning..."
      renderApp()
      try {
        await apiRequest("/admin/finetuning/jobs", {
          method: "POST",
        })
        pushToast("Job de fine-tuning criado.")
        await loadTrainingData()
      } catch (error) {
        pushToast(summarizeError(error), "error")
      } finally {
        state.loading = false
        state.loadingMessage = ""
        renderApp()
      }
    })
  }
}

async function bootstrap() {
  renderApp()
  try {
    await fetchPlatformSession()
    await refreshCurrentView()
    state.settingsOpen = false
    renderApp()
  } catch {
    state.settingsOpen = true
    renderApp()
  }
}

bootstrap()
