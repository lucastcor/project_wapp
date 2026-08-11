const STORAGE_KEY = "project-wapp-platform-config-v1"
const STUDIO_SESSIONS_KEY = "project-wapp-studio-sessions-v1"

function loadStudioSessions() {
  try {
    const raw = window.localStorage.getItem(STUDIO_SESSIONS_KEY)
    if (!raw) return {}
    const parsed = JSON.parse(raw)
    return parsed && typeof parsed === "object" ? parsed : {}
  } catch {
    return {}
  }
}

function persistStudioSessions() {
  try {
    window.localStorage.setItem(STUDIO_SESSIONS_KEY, JSON.stringify(state.studio.sessionsByCompany || {}))
  } catch {
    // ignore quota errors
  }
}

function studioCompanyKey() {
  return state.config.companySlug || state.config.companyId || "__default__"
}

function getStudioSessions() {
  const key = studioCompanyKey()
  if (!state.studio.sessionsByCompany[key]) {
    state.studio.sessionsByCompany[key] = []
  }
  return state.studio.sessionsByCompany[key]
}

function getActiveStudioSession() {
  const sessions = getStudioSessions()
  const activeId = state.studio.activeSessionId
  return sessions.find((s) => s.id === activeId) || null
}

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
  menu: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 12h18M3 6h18M3 18h18"/></svg>',
  close: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6L6 18M6 6l12 12"/></svg>',
  bell: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>',
}

const FLOW_NODE_TEMPLATES = [
  {
    category: "Entrada",
    items: [
      { icon: "spark", label: "Qualificação inicial", description: "Entende o contexto e intenção do cliente no primeiro contato.", defaults: { label: "Entrada e qualificação", objective: "Entender rapidamente quem é o cliente e o nível de intenção.", trigger_terms: ["primeiro contato", "interesse", "informação"], preferred_cta: "Perguntar detalhes sobre necessidade e urgência." } },
      { icon: "target", label: "Triagem de intenção", description: "Classifica o pedido em trilhas (venda, suporte, agenda).", defaults: { label: "Triagem de intenção", objective: "Classificar a conversa em trilha operacional apropriada.", trigger_terms: ["dúvida", "quero saber"], preferred_cta: "Direcionar para a trilha adequada após confirmar tema." } },
    ],
  },
  {
    category: "Agenda",
    items: [
      { icon: "calendar", label: "Agendamento", description: "Apresenta disponibilidade e confirma reserva.", defaults: { label: "Agendamento", objective: "Conduzir disponibilidade, confirmação e reserva.", trigger_terms: ["agendar", "marcar", "horário"], preferred_cta: "Apresentar janela disponível e pedir confirmação." } },
      { icon: "refresh", label: "Reagendamento", description: "Reorganiza horário existente sem atrito.", defaults: { label: "Reagendamento", objective: "Mover reserva existente para nova janela viável.", trigger_terms: ["remarcar", "trocar horário", "mudar"], preferred_cta: "Oferecer nova janela compatível com a agenda atual." } },
      { icon: "trash", label: "Cancelamento", description: "Libera vaga e ativa fila de remanejamento.", defaults: { label: "Cancelamento", objective: "Cancelar reserva com clareza e acionar fila.", trigger_terms: ["cancelar", "desmarcar"], preferred_cta: "Confirmar cancelamento e liberar vaga na agenda." } },
    ],
  },
  {
    category: "Conversão",
    items: [
      { icon: "send", label: "Follow-up", description: "Retoma lead morno com próximo passo claro.", defaults: { label: "Conversão e follow-up", objective: "Mover leads mornos para próxima ação comercial.", trigger_terms: ["vou pensar", "depois", "me chama"], preferred_cta: "Propor próximo passo concreto com prazo." } },
      { icon: "check", label: "Confirmação final", description: "Fecha a conversa com compromisso confirmado.", defaults: { label: "Confirmação final", objective: "Registrar próxima ação e encerrar conversa de forma positiva.", trigger_terms: ["ok", "combinado", "fechado"], preferred_cta: "Confirmar próximo passo e se despedir de forma calorosa." } },
    ],
  },
  {
    category: "Escalonamento",
    items: [
      { icon: "handoff", label: "Handoff humano", description: "Transfere para operador em casos de exceção.", defaults: { label: "Handoff para operador", objective: "Encaminhar caso complexo para operador humano.", trigger_terms: ["falar com humano", "atendente", "pessoa real"], handoff_when: "Sempre que o cliente pedir operador ou a política não cobrir.", preferred_cta: "Confirmar o encaminhamento e avisar o operador." } },
    ],
  },
]

const NAV_ITEMS = [
  { key: "dashboard", label: "Control Tower", caption: "Readiness e postura operacional", icon: "dashboard" },
  { key: "analytics", label: "Analytics", caption: "No-show, lembretes e funil", icon: "target" },
  { key: "studio", label: "Bot Studio", caption: "Teste conversas antes de publicar", icon: "studio" },
  { key: "flows", label: "Flow Studio", caption: "Pipelines, playbooks e respostas", icon: "flow" },
  { key: "agenda", label: "Agenda Console", caption: "Calendário, bookings e fila", icon: "calendar" },
  { key: "contacts", label: "Contacts Desk", caption: "Perfil, busca e notas", icon: "contacts" },
  { key: "training", label: "Training Ops", caption: "Importação, curadoria, fine-tune", icon: "training" },
  { key: "team", label: "Equipe", caption: "Acessos, papéis e convites", icon: "contacts" },
  { key: "billing", label: "Billing", caption: "Planos, uso e assinatura", icon: "tag" },
  { key: "onboarding", label: "Setup Wizard", caption: "Configurar nova empresa", icon: "spark" },
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
  mobileMenuOpen: false,
  manualBookingOpen: false,
  connected: false,
  session: null,
  authCapabilities: {
    self_service_signup: false,
    password_reset: false,
  },
  authMode: "login",
  authAction: (() => {
    const params = new URLSearchParams(window.location.search)
    return {
      invitationToken: params.get("invitation_token") || "",
      passwordResetToken: params.get("password_reset_token") || "",
    }
  })(),
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
  analyticsDashboard: null,
  analyticsPeriod: "30d",
  teamMembers: [],
  teamInvitation: null,
  onboardingPresets: null,
  onboardingWizard: {
    step: 0,
    completed: false,
    result: null,
    draft: {
      company_name: "",
      business_type_key: "",
      segment: "",
      company_description: "",
      phone: "",
      email: "",
      instagram: "",
      website: "",
      timezone: "America/Sao_Paulo",
      weekday_hours: "08:00-18:00",
      saturday_hours: "09:00-12:00",
      sunday_hours: "fechado",
      slot_duration_minutes: 60,
      minimum_notice_minutes: 120,
      reminder_offsets_days: "2,1",
      brand_style: "",
      custom_rules: "",
      services: [],
    },
  },
  billingPlans: null,
  billingUsage: null,
  billingCycle: "monthly",
  trainingImportResult: null,
  availabilityResult: null,
  simulator: {
    transcript: [],
    lastResult: null,
  },
  studio: {
    sessionsByCompany: loadStudioSessions(),
    activeSessionId: null,
    draftMessage: "",
    newSession: { label: "", contactId: "", contactName: "" },
    showNewForm: false,
  },
  flowEditor: {
    selectedPipelineKey: "lead_capture",
    zoom: 1,
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
    manualBooking: {
      contactId: "",
      title: "",
      startsAt: "",
      endsAt: "",
      description: "",
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
      email: "",
      password: "",
      displayName: "",
      companyName: "",
      companySlug: "",
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

const API_ERROR_TRANSLATIONS = {
  "A company must keep at least one active owner.": "A empresa precisa manter ao menos um proprietário ativo.",
  "Only a company owner or admin can invite members.": "Somente proprietários e administradores podem convidar membros.",
  "Only a company owner or admin can list members.": "Somente proprietários e administradores podem listar membros.",
  "Only a company owner or admin can manage members.": "Somente proprietários e administradores podem gerenciar membros.",
  "Only an owner can invite another admin.": "Somente um proprietário pode convidar outro administrador.",
  "Only an owner can grant owner or admin access.": "Somente um proprietário pode conceder acesso de proprietário ou administrador.",
  "Owner access cannot be granted by invitation.": "O papel de proprietário não pode ser concedido por convite.",
  "Membership not found.": "Vínculo de acesso não encontrado.",
  "CSRF validation failed.": "A validação de segurança da sessão falhou. Atualize a página e tente novamente.",
}

function translateApiError(message) {
  return API_ERROR_TRANSLATIONS[String(message || "")] || String(message || "")
}

function summarizeError(error) {
  if (!error) {
    return "Falha inesperada."
  }
  if (typeof error === "string") {
    return translateApiError(error)
  }
  if (error.detail) {
    return typeof error.detail === "string"
      ? translateApiError(error.detail)
      : JSON.stringify(error.detail)
  }
  if (error.message) {
    return translateApiError(error.message)
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
    headers: customHeaders = {},
  } = options

  const url = new URL(path, window.location.origin)
  if (params) {
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== "") {
        url.searchParams.set(key, value)
      }
    })
  }

  const headers = { ...customHeaders }
  if (body !== null) {
    headers["Content-Type"] = "application/json"
  }
  if (admin && state.config.adminActor) {
    headers["X-Admin-Actor"] = state.config.adminActor
  }
  if (!["GET", "HEAD", "OPTIONS"].includes(method.toUpperCase())) {
    const csrfCookie = document.cookie
      .split("; ")
      .find((item) => item.startsWith("project_wapp_csrf="))
    if (csrfCookie) {
      headers["X-CSRF-Token"] = decodeURIComponent(csrfCookie.split("=", 2)[1] || "")
    }
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

async function fetchAuthCapabilities() {
  state.authCapabilities = await apiRequest("/platform/auth/capabilities", {
    admin: false,
  })
  return state.authCapabilities
}

function clearAuthActionFromUrl() {
  state.authAction = {
    invitationToken: "",
    passwordResetToken: "",
  }
  const url = new URL(window.location.href)
  url.searchParams.delete("invitation_token")
  url.searchParams.delete("password_reset_token")
  window.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`)
}

async function loginPlatformSession() {
  const session = await apiRequest("/platform/auth/session", {
    method: "POST",
    admin: false,
    body: {
      admin_token: state.drafts.auth.adminToken || null,
      admin_actor: state.drafts.auth.adminActor || state.config.adminActor || null,
      email: state.drafts.auth.email || null,
      password: state.drafts.auth.password || null,
    },
  })
  state.session = session
  state.connected = true
  state.config.adminActor = session.actor || state.config.adminActor
  state.drafts.auth.adminToken = ""
  state.drafts.auth.password = ""
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
  disconnectSSE()
}

let _companiesLoaded = false

async function loadCompanies(force = false) {
  if (_companiesLoaded && state.companies.length > 0 && !force) return
  const companies = await apiRequest("/admin/companies")
  state.companies = Array.isArray(companies) ? companies : []
  _companiesLoaded = true

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
    apiRequest("/admin/observability/summary", { params: getAdminCompanyParams() }),
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

async function loadAnalyticsData() {
  await loadCompanies()
  const params = { ...getCompanyParams(), period: state.analyticsPeriod }
  const dashboard = await apiRequest("/admin/analytics/dashboard", { params })
  state.analyticsDashboard = dashboard
}

async function loadOnboardingData() {
  if (!state.onboardingPresets) {
    state.onboardingPresets = await apiRequest("/admin/onboarding/presets")
  }
}

async function loadBillingData() {
  await loadCompanies()
  const companyId = state.config.companyId
  if (!companyId) {
    state.billingPlans = null
    state.billingUsage = null
    return
  }
  const [plans, usage] = await Promise.all([
    apiRequest("/admin/billing/plans"),
    apiRequest("/admin/billing/usage", { params: { company_id: companyId } }),
  ])
  state.billingPlans = plans
  state.billingUsage = usage
}

function getCurrentIdentityMembership() {
  if (state.session?.auth_mode !== "identity") return null
  return (state.session.memberships || []).find(
    (membership) => String(membership.company_id) === String(state.config.companyId)
  ) || null
}

function canManageCurrentTeam() {
  return ["owner", "admin"].includes(getCurrentIdentityMembership()?.role)
}

async function loadTeamData() {
  await loadCompanies()
  state.teamInvitation = null
  if (!state.config.companyId || !canManageCurrentTeam()) {
    state.teamMembers = []
    return
  }
  const members = await apiRequest("/platform/identity/members", {
    admin: false,
    params: { company_id: state.config.companyId },
  })
  state.teamMembers = Array.isArray(members) ? members : []
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
      params: {
        ...getAdminCompanyParams(),
        limit: 10,
      },
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

let _refreshInFlight = null

async function refreshCurrentView({ silent = false } = {}) {
  // Deduplicate: if a refresh is already running, return the same promise
  if (_refreshInFlight) return _refreshInFlight
  _refreshInFlight = _doRefreshCurrentView({ silent })
  try { return await _refreshInFlight } finally { _refreshInFlight = null }
}

async function _doRefreshCurrentView({ silent = false } = {}) {
  if (!silent) {
    state.loading = true
    state.loadingMessage = viewLoadingLabel(state.view)
    // Don't re-render the whole page — just inject the loading banner
    const existingBanner = document.querySelector(".loading-banner")
    if (!existingBanner) {
      const bannerHtml = renderLoadingBanner()
      if (bannerHtml) root.insertAdjacentHTML("beforeend", bannerHtml)
    }
  }

  try {
    if (state.view === "dashboard") {
      await loadDashboardData()
    } else if (state.view === "analytics") {
      await loadAnalyticsData()
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
    } else if (state.view === "team") {
      await loadTeamData()
    } else if (state.view === "billing") {
      await loadBillingData()
    } else if (state.view === "onboarding") {
      await loadOnboardingData()
    }
    state.connected = true
    state.errorMessage = ""
  } catch (error) {
    state.errorMessage = summarizeError(error)
    if (error.status === 401) {
      state.connected = false
      state.session = null
      state.settingsOpen = true
      disconnectSSE()
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
  if (view === "analytics") return "Calculando métricas de no-show e agendamento..."
  if (view === "billing") return "Carregando planos e uso..."
  if (view === "onboarding") return "Preparando wizard de configuração..."
  if (view === "studio") return "Abrindo ambiente de simulação..."
  if (view === "flows") return "Montando playbooks e respostas aprovadas..."
  if (view === "agenda") return "Buscando agenda e fila de prioridade..."
  if (view === "contacts") return "Buscando contatos..."
  if (view === "training") return "Montando operação de curadoria..."
  if (view === "team") return "Carregando equipe e permissões..."
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
      <div class="topbar-left">
        <button class="button-icon mobile-menu-toggle" id="mobile-menu-toggle" aria-label="Abrir menu">${ICONS.menu}</button>
        <div>
          <h1 class="topbar-title">${renderViewTitle()}</h1>
          <p class="topbar-copy topbar-copy-desktop">${renderViewCopy()}</p>
        </div>
      </div>
      <div class="topbar-controls">
        ${renderReadinessPill()}
        <label class="pill pill-desktop">
          <span>Empresa</span>
          <select id="company-select" style="background: transparent; border: 0; color: inherit;">
            ${scopeOptions}
          </select>
        </label>
        <span class="pill pill-desktop"><strong>Sessão</strong>${escapeHtml(sessionRole)}</span>
        <span class="pill pill-desktop"><strong>Escopo</strong>${escapeHtml(getCompanyScopeLabel())}</span>
        <button class="button-ghost" id="open-settings">${state.session ? "Sessão" : "Entrar"}</button>
        ${state.session ? '<button class="button-danger" id="logout-session">Sair</button>' : ""}
      </div>
    </header>
  `
}

function renderViewTitle() {
  if (state.view === "dashboard") return "Uma camada premium para operar o bot como produto"
  if (state.view === "analytics") return "Analytics de no-show, lembretes e funil de agendamento"
  if (state.view === "billing") return "Billing — planos, assinatura e consumo"
  if (state.view === "onboarding") return "Setup Wizard — configure uma nova empresa passo a passo"
  if (state.view === "studio") return "Bot Studio para testar conversa real antes de publicar"
  if (state.view === "flows") return "Flow Studio para lapidar regras, playbooks e respostas"
  if (state.view === "agenda") return "Agenda Console com controle manual e visão operacional"
  if (state.view === "contacts") return "Contacts Desk para decidir com contexto e precisão"
  if (state.view === "training") return "Training Ops para curar base histórica com segurança"
  if (state.view === "team") return "Equipe — acessos, papéis e convites"
  return "Project WAPP Platform"
}

function renderViewCopy() {
  if (state.view === "dashboard") return "Readiness, observabilidade, onboarding e comando central da operação."
  if (state.view === "analytics") return "Meça impacto real: taxa de no-show, eficácia dos lembretes e recuperação via fila."
  if (state.view === "billing") return "Gerencie plano, acompanhe limites de uso e controle sua assinatura."
  if (state.view === "onboarding") return "Preencha os dados, configure horários e ative o bot em minutos."
  if (state.view === "studio") return "Simule cliente, operador e fluxos de agendamento na mesma interface."
  if (state.view === "flows") return "Desenhe a experiência do bot, organize pipelines e promova mensagens aprovadas."
  if (state.view === "agenda") return "Monitore disponibilidade, bookings, remanejamento e ajustes manuais."
  if (state.view === "contacts") return "Enriqueça customer_profile, organize notas e desambiguação de atendimento."
  if (state.view === "training") return "Importe CRM, faça dry-run, acompanhe o dataset e acione fine-tuning."
  if (state.view === "team") return "Convide colaboradores e controle permissões por empresa com segurança."
  return "Painel premium do Project WAPP."
}

function renderSidebar() {
  const openClass = state.mobileMenuOpen ? "sidebar-open" : ""
  return `
    ${state.mobileMenuOpen ? '<div class="sidebar-overlay" id="sidebar-overlay"></div>' : ''}
    <aside class="sidebar ${openClass}">
      <div class="sidebar-mobile-header">
        <div class="brand-logo">
          <span class="brand-logo-mark">${ICONS.spark}</span>
          <span>WAPP</span>
        </div>
        <button class="button-icon sidebar-close-btn" id="sidebar-close" aria-label="Fechar menu">${ICONS.close}</button>
      </div>

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
        v1.0 · Superfície principal <span class="mono">/platform</span>
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

// ── Onboarding Wizard ──────────────────────────────────────────────────

// ── Billing View ────────────────────────────────────────────────────────

function billingUsageBar(metric, label, used, limit) {
  if (limit <= 0) return ""
  const pct = Math.min(Math.round((used / limit) * 100), 100)
  const tone = pct >= 100 ? "danger" : pct >= 80 ? "warn" : "success"
  return `
    <div class="billing-usage-row">
      <div class="billing-usage-header">
        <span class="billing-usage-label">${escapeHtml(label)}</span>
        <span class="billing-usage-numbers">${formatNumber(used)} / ${formatNumber(limit)}</span>
      </div>
      <div class="billing-usage-track">
        <div class="billing-usage-fill billing-usage-${tone}" style="width:${pct}%"></div>
      </div>
      <span class="billing-usage-pct billing-usage-${tone}">${pct}%</span>
    </div>
  `
}

function renderBillingPlanCards(plans, currentPlanKey, billingCycle, managedByStripe = false) {
  if (!plans || plans.length === 0) return '<div class="empty-state">Nenhum plano disponível.</div>'

  const sorted = [...plans].sort((a, b) => (a.display_order || 0) - (b.display_order || 0))

  return `
    <div class="billing-plans-grid">
      ${sorted.map(p => {
        const isCurrent = p.plan_key === currentPlanKey
        const price = billingCycle === "annual" ? p.price_annual_monthly : p.price_monthly
        const limits = p.limits || {}
        return `
          <div class="billing-plan-card ${isCurrent ? 'billing-plan-current' : ''}">
            ${isCurrent ? '<span class="billing-plan-badge">Plano atual</span>' : ''}
            <h4 class="billing-plan-name">${escapeHtml(p.name)}</h4>
            <div class="billing-plan-price">
              <span class="billing-plan-amount">R$ ${price.toLocaleString("pt-BR", { minimumFractionDigits: 2 })}</span>
              <span class="billing-plan-period">/mês</span>
            </div>
            <ul class="billing-plan-features">
              <li>${formatNumber(limits.messages || 0)} mensagens</li>
              <li>${formatNumber(limits.bookings || 0)} agendamentos</li>
              <li>${formatNumber(limits.outbound_campaigns || 0)} campanhas</li>
              <li>${formatNumber(limits.classified_contacts || 0)} contatos classificados</li>
              <li>${formatNumber(limits.handoffs || 0)} handoffs</li>
              <li>${limits.reminders_per_booking || 0} lembretes/agendamento</li>
              <li>${limits.finetuning_jobs || 0} fine-tuning/mês</li>
              ${p.features?.priority_support ? '<li>Suporte prioritário</li>' : ''}
              ${p.features?.dedicated_support ? '<li>Suporte dedicado</li>' : ''}
            </ul>
            ${!isCurrent ? `
              <button
                class="button billing-checkout-btn"
                data-plan-key="${escapeHtml(p.plan_key)}"
                ${p.stripe_checkout_ready ? "" : "disabled"}
                title="${p.stripe_checkout_ready ? "Continuar para pagamento seguro" : "Preço Stripe ainda não configurado"}"
              >${currentPlanKey ? "Gerenciar no portal" : "Assinar"}</button>
            ` : (
              managedByStripe
                ? '<button class="button-ghost billing-portal-btn">Gerenciar assinatura</button>'
                : '<span class="tag">Plano atual</span>'
            )}
          </div>
        `
      }).join("")}
    </div>
  `
}

function renderBillingView() {
  const usage = state.billingUsage
  const plans = state.billingPlans

  if (!state.config.companyId || !getSelectedCompany()) {
    return `
      <section class="hero">
        <span class="brand-eyebrow">Billing</span>
        <h2 class="hero-title">Selecione uma empresa para consultar assinatura e consumo.</h2>
        <p class="hero-copy">Cobrança e limites são sempre isolados por tenant; não existe operação financeira em escopo global.</p>
      </section>
    `
  }

  if (!usage) {
    return `
      <section class="hero">
        <span class="brand-eyebrow">Billing</span>
        <h2 class="hero-title">Carregando planos e uso...</h2>
        <div class="hero-actions">
          <button class="button-ghost" data-refresh-view>Atualizar</button>
        </div>
      </section>
      ${renderSkeletonCards(2)}
    `
  }

  if (!usage.has_subscription) {
    return `
      <section class="hero">
        <span class="brand-eyebrow">Billing</span>
        <h2 class="hero-title">Nenhuma assinatura ativa.</h2>
        <p class="hero-copy">Escolha um plano e conclua a assinatura no checkout seguro do Stripe.</p>
        <div class="hero-actions">
          <label>
            Ciclo
            <select id="billing-cycle-select">
              <option value="monthly" ${state.billingCycle === "monthly" ? "selected" : ""}>Mensal</option>
              <option value="annual" ${state.billingCycle === "annual" ? "selected" : ""}>Anual</option>
            </select>
          </label>
        </div>
      </section>

      <section class="grid-one">
        <article class="card">
          <div class="card-header">
            <div>
              <h3 class="card-title">Planos disponíveis</h3>
              <p class="card-copy">Escolha o plano ideal para o volume do seu negócio.</p>
            </div>
          </div>
          ${renderBillingPlanCards(plans, null, state.billingCycle)}
        </article>
      </section>
    `
  }

  const sub = usage.subscription || {}
  const usageData = usage.usage || {}
  const alerts = usage.alerts || []
  const features = usage.features || {}
  const billingCycle = sub.billing_cycle || "monthly"

  const METRIC_LABELS = {
    messages: "Mensagens",
    bookings: "Agendamentos",
    outbound_campaigns: "Campanhas",
    handoffs: "Handoffs",
    finetuning_jobs: "Fine-tuning",
  }

  const statusLabels = {
    trial: "Trial",
    active: "Ativa",
    incomplete: "Pagamento pendente",
    past_due: "Pagamento em atraso",
    cancelled: "Cancelada",
  }
  const statusTone = {
    trial: "tag-warn",
    active: "tag-success",
    incomplete: "tag-warn",
    past_due: "tag-danger",
    cancelled: "tag-danger",
  }

  return `
    <section class="hero">
      <span class="brand-eyebrow">Billing</span>
      <h2 class="hero-title">Gerencie sua assinatura e acompanhe o consumo.</h2>
      <p class="hero-copy">Plano, ciclo de cobrança, limites de uso e alertas em um só lugar.</p>
      <div class="hero-actions">
        <button class="button-ghost" data-refresh-view>Atualizar</button>
      </div>
    </section>

    ${alerts.length > 0 ? `
      <section class="grid-one">
        <article class="card billing-alerts-card">
          <div class="card-header">
            <div>
              <h3 class="card-title">Alertas de uso</h3>
            </div>
          </div>
          <div class="billing-alerts">
            ${alerts.map(a => `
              <div class="billing-alert billing-alert-${a.type === 'exceeded' ? 'danger' : 'warn'}">
                <span>${escapeHtml(a.message)}</span>
              </div>
            `).join("")}
          </div>
        </article>
      </section>
    ` : ''}

    <section class="grid-two">
      <article class="card">
        <div class="card-header">
          <div>
            <h3 class="card-title">Assinatura</h3>
            <p class="card-copy">Detalhes do plano e ciclo atual.</p>
          </div>
          <span class="tag ${statusTone[sub.status] || ''}">${escapeHtml(statusLabels[sub.status] || sub.status)}</span>
        </div>
        <div class="billing-sub-details">
          <div class="billing-detail-row">
            <span>Plano</span>
            <strong>${escapeHtml(sub.plan_name || sub.plan_key)}</strong>
          </div>
          <div class="billing-detail-row">
            <span>Ciclo</span>
            <strong>${billingCycle === "annual" ? "Anual (fidelidade)" : "Mensal"}</strong>
          </div>
          <div class="billing-detail-row">
            <span>Valor</span>
            <strong>R$ ${(sub.price_monthly || 0).toLocaleString("pt-BR", { minimumFractionDigits: 2 })}/mês</strong>
          </div>
          ${sub.trial_days_remaining !== null && sub.trial_days_remaining !== undefined ? `
            <div class="billing-detail-row">
              <span>Trial expira em</span>
              <strong>${sub.trial_days_remaining} dia${sub.trial_days_remaining !== 1 ? 's' : ''}</strong>
            </div>
          ` : ''}
          <div class="billing-detail-row">
            <span>Período atual até</span>
            <strong>${sub.current_period_end ? new Date(sub.current_period_end).toLocaleDateString("pt-BR") : '-'}</strong>
          </div>
        </div>
        <div class="billing-sub-actions">
          ${sub.gateway === "stripe" ? '<button class="button billing-portal-btn">Gerenciar no Stripe</button>' : ''}
          ${sub.cancel_at_period_end ? '<span class="tag tag-warn">Cancelamento agendado</span>' : ''}
          ${sub.expired ? `<span class="tag tag-danger">Expirada</span>` : ''}
        </div>
      </article>

      <article class="card">
        <div class="card-header">
          <div>
            <h3 class="card-title">Consumo do período</h3>
            <p class="card-copy">Uso acumulado dentro do ciclo atual.</p>
          </div>
        </div>
        <div class="billing-usage-list">
          ${Object.entries(usageData).map(([metric, data]) => {
            const label = METRIC_LABELS[metric] || metric
            return billingUsageBar(metric, label, data.used, data.limit)
          }).join("")}
        </div>
      </article>
    </section>

    <section class="grid-one">
      <article class="card">
        <div class="card-header">
          <div>
            <h3 class="card-title">Planos disponíveis</h3>
            <p class="card-copy">Compare os planos e faça upgrade ou downgrade quando quiser.</p>
          </div>
        </div>
        ${renderBillingPlanCards(plans, sub.plan_key, billingCycle, sub.gateway === "stripe")}
      </article>
    </section>
  `
}

const ONBOARDING_STEPS = [
  { key: "business", label: "Dados do negócio", icon: "tag" },
  { key: "hours", label: "Horário de atendimento", icon: "clock" },
  { key: "scheduling", label: "Agenda e lembretes", icon: "calendar" },
  { key: "services", label: "Serviços", icon: "studio" },
  { key: "review", label: "Revisar e ativar", icon: "check" },
]

function renderOnboardingStepIndicator() {
  const current = state.onboardingWizard.step
  return `
    <div class="wizard-steps">
      ${ONBOARDING_STEPS.map((s, i) => `
        <div class="wizard-step ${i === current ? 'active' : ''} ${i < current ? 'done' : ''}">
          <span class="wizard-step-num">${i < current ? ICONS.check : i + 1}</span>
          <span class="wizard-step-label">${s.label}</span>
        </div>
        ${i < ONBOARDING_STEPS.length - 1 ? '<div class="wizard-step-line"></div>' : ''}
      `).join("")}
    </div>
  `
}

function renderOnboardingStep0() {
  const d = state.onboardingWizard.draft
  const presets = state.onboardingPresets?.business_types || {}
  const typeEntries = Object.entries(presets)

  return `
    <div class="wizard-section">
      <h3 class="wizard-section-title">Tipo de negócio</h3>
      <p class="wizard-section-copy">Selecione o que mais se aproxima. Isso pré-configura módulos e catálogo.</p>
      <div class="wizard-type-grid">
        ${typeEntries.map(([key, info]) => `
          <button class="wizard-type-card ${d.business_type_key === key ? 'selected' : ''}" data-onboarding-type="${key}">
            <span class="wizard-type-icon">${ICONS[key === "clinica" ? "target" : key === "salao" ? "spark" : key === "escritorio" ? "tag" : key === "academia" ? "refresh" : "dashboard"]}</span>
            <span class="wizard-type-label">${escapeHtml(info.label)}</span>
          </button>
        `).join("")}
      </div>
    </div>
    <div class="wizard-section">
      <h3 class="wizard-section-title">Dados da empresa</h3>
      <div class="form-grid-two">
        <div class="field">
          <label>Nome da empresa *</label>
          <input name="company_name" value="${escapeHtml(d.company_name)}" placeholder="Ex: Clínica Dra. Maria" data-onboarding-field />
        </div>
        <div class="field">
          <label>Segmento</label>
          <input name="segment" value="${escapeHtml(d.segment)}" placeholder="Ex: Odontologia, Estética" data-onboarding-field />
        </div>
        <div class="field" style="grid-column: 1 / -1">
          <label>Descrição breve</label>
          <input name="company_description" value="${escapeHtml(d.company_description)}" placeholder="Uma frase sobre o que sua empresa faz" data-onboarding-field />
        </div>
      </div>
    </div>
    <div class="wizard-section">
      <h3 class="wizard-section-title">Contato</h3>
      <div class="form-grid-two">
        <div class="field">
          <label>Telefone / WhatsApp</label>
          <input name="phone" value="${escapeHtml(d.phone)}" placeholder="+55 62 99999-9999" data-onboarding-field />
        </div>
        <div class="field">
          <label>Email</label>
          <input name="email" value="${escapeHtml(d.email)}" placeholder="contato@empresa.com" data-onboarding-field />
        </div>
        <div class="field">
          <label>Instagram</label>
          <input name="instagram" value="${escapeHtml(d.instagram)}" placeholder="@empresa" data-onboarding-field />
        </div>
        <div class="field">
          <label>Website</label>
          <input name="website" value="${escapeHtml(d.website)}" placeholder="https://www.empresa.com" data-onboarding-field />
        </div>
      </div>
    </div>
  `
}

function renderOnboardingStep1() {
  const d = state.onboardingWizard.draft
  const timezones = state.onboardingPresets?.timezones || ["America/Sao_Paulo"]

  return `
    <div class="wizard-section">
      <h3 class="wizard-section-title">Fuso horário</h3>
      <div class="field">
        <label>Timezone</label>
        <select name="timezone" data-onboarding-field>
          ${timezones.map(tz => `<option value="${tz}" ${d.timezone === tz ? "selected" : ""}>${tz}</option>`).join("")}
        </select>
      </div>
    </div>
    <div class="wizard-section">
      <h3 class="wizard-section-title">Horários de funcionamento</h3>
      <p class="wizard-section-copy">Use o formato HH:MM-HH:MM ou "fechado".</p>
      <div class="form-grid-three">
        <div class="field">
          <label>Segunda a sexta</label>
          <input name="weekday_hours" value="${escapeHtml(d.weekday_hours)}" placeholder="08:00-18:00" data-onboarding-field />
        </div>
        <div class="field">
          <label>Sábado</label>
          <input name="saturday_hours" value="${escapeHtml(d.saturday_hours)}" placeholder="09:00-12:00" data-onboarding-field />
        </div>
        <div class="field">
          <label>Domingo</label>
          <input name="sunday_hours" value="${escapeHtml(d.sunday_hours)}" placeholder="fechado" data-onboarding-field />
        </div>
      </div>
    </div>
    <div class="wizard-section">
      <h3 class="wizard-section-title">Tom da marca</h3>
      <div class="field">
        <label>Estilo de comunicação</label>
        <input name="brand_style" value="${escapeHtml(d.brand_style)}" placeholder="Ex: profissional e acolhedor" data-onboarding-field />
      </div>
    </div>
  `
}

function renderOnboardingStep2() {
  const d = state.onboardingWizard.draft

  return `
    <div class="wizard-section">
      <h3 class="wizard-section-title">Configuração de agenda</h3>
      <p class="wizard-section-copy">Defina a duração padrão dos slots e a antecedência mínima para agendamento.</p>
      <div class="form-grid-two">
        <div class="field">
          <label>Duração padrão do slot (minutos)</label>
          <input name="slot_duration_minutes" type="number" min="10" max="480" value="${d.slot_duration_minutes}" data-onboarding-field />
        </div>
        <div class="field">
          <label>Antecedência mínima (minutos)</label>
          <input name="minimum_notice_minutes" type="number" min="0" max="10080" value="${d.minimum_notice_minutes}" data-onboarding-field />
        </div>
      </div>
    </div>
    <div class="wizard-section">
      <h3 class="wizard-section-title">Lembretes automáticos</h3>
      <p class="wizard-section-copy">O bot envia lembretes por WhatsApp antes de cada consulta. Defina quantos dias antes.</p>
      <div class="field">
        <label>Dias antes do agendamento (separados por vírgula)</label>
        <input name="reminder_offsets_days" value="${escapeHtml(d.reminder_offsets_days)}" placeholder="2,1" data-onboarding-field />
      </div>
    </div>
    <div class="wizard-section">
      <h3 class="wizard-section-title">Regras do bot</h3>
      <p class="wizard-section-copy">Instruções que o bot deve seguir. Uma por linha.</p>
      <div class="field">
        <textarea name="custom_rules" rows="4" placeholder="Não inventar preços&#10;Sempre confirmar horário antes de agendar" data-onboarding-field>${escapeHtml(d.custom_rules)}</textarea>
      </div>
    </div>
  `
}

function renderOnboardingStep3() {
  const d = state.onboardingWizard.draft
  const services = d.services || []

  return `
    <div class="wizard-section">
      <h3 class="wizard-section-title">Serviços oferecidos</h3>
      <p class="wizard-section-copy">Cadastre os serviços que o bot vai oferecer para agendamento. Você pode adicionar mais depois.</p>
      <div class="wizard-services-list">
        ${services.map((svc, i) => `
          <div class="wizard-service-row" data-service-index="${i}">
            <div class="form-grid-two">
              <div class="field">
                <label>Nome do serviço</label>
                <input name="svc_name" value="${escapeHtml(svc.name || "")}" data-service-field="${i}" />
              </div>
              <div class="field">
                <label>Duração (min)</label>
                <input name="svc_duration" type="number" value="${svc.duration_minutes || ""}" data-service-field="${i}" />
              </div>
              <div class="field">
                <label>Preço (R$)</label>
                <input name="svc_price" type="number" step="0.01" value="${svc.price || ""}" data-service-field="${i}" />
              </div>
              <div class="field" style="display:flex;align-items:flex-end">
                <button class="button-ghost" data-remove-service="${i}">Remover</button>
              </div>
            </div>
          </div>
        `).join("")}
        ${services.length === 0 ? '<p class="wizard-section-copy">Nenhum serviço adicionado ainda. Clique abaixo para começar.</p>' : ''}
      </div>
      <button class="button-ghost" id="add-onboarding-service" style="margin-top:8px">${ICONS.plus} Adicionar serviço</button>
    </div>
  `
}

function renderOnboardingStep4() {
  const d = state.onboardingWizard.draft
  const preset = state.onboardingPresets?.business_types?.[d.business_type_key]
  const services = d.services || []
  const offsets = d.reminder_offsets_days || "2,1"

  return `
    <div class="wizard-section">
      <h3 class="wizard-section-title">Revisão final</h3>
      <p class="wizard-section-copy">Confira tudo antes de ativar. Você pode voltar e editar qualquer passo.</p>

      <div class="wizard-review-grid">
        <div class="wizard-review-item">
          <span class="wizard-review-label">Empresa</span>
          <span class="wizard-review-value">${escapeHtml(d.company_name || "—")}</span>
        </div>
        <div class="wizard-review-item">
          <span class="wizard-review-label">Tipo</span>
          <span class="wizard-review-value">${escapeHtml(preset?.label || d.business_type_key || "—")}</span>
        </div>
        <div class="wizard-review-item">
          <span class="wizard-review-label">Segmento</span>
          <span class="wizard-review-value">${escapeHtml(d.segment || "Padrão do tipo")}</span>
        </div>
        <div class="wizard-review-item">
          <span class="wizard-review-label">Telefone</span>
          <span class="wizard-review-value">${escapeHtml(d.phone || "—")}</span>
        </div>
        <div class="wizard-review-item">
          <span class="wizard-review-label">Timezone</span>
          <span class="wizard-review-value">${escapeHtml(d.timezone)}</span>
        </div>
        <div class="wizard-review-item">
          <span class="wizard-review-label">Seg-Sex</span>
          <span class="wizard-review-value">${escapeHtml(d.weekday_hours)}</span>
        </div>
        <div class="wizard-review-item">
          <span class="wizard-review-label">Sábado</span>
          <span class="wizard-review-value">${escapeHtml(d.saturday_hours)}</span>
        </div>
        <div class="wizard-review-item">
          <span class="wizard-review-label">Domingo</span>
          <span class="wizard-review-value">${escapeHtml(d.sunday_hours)}</span>
        </div>
        <div class="wizard-review-item">
          <span class="wizard-review-label">Slot padrão</span>
          <span class="wizard-review-value">${d.slot_duration_minutes} min</span>
        </div>
        <div class="wizard-review-item">
          <span class="wizard-review-label">Antecedência</span>
          <span class="wizard-review-value">${d.minimum_notice_minutes} min</span>
        </div>
        <div class="wizard-review-item">
          <span class="wizard-review-label">Lembretes</span>
          <span class="wizard-review-value">${escapeHtml(offsets)} dias antes</span>
        </div>
        <div class="wizard-review-item">
          <span class="wizard-review-label">Serviços</span>
          <span class="wizard-review-value">${services.length} cadastrado(s)</span>
        </div>
      </div>

      ${services.length > 0 ? `
        <div class="wizard-review-services">
          <h4 style="font-size:13px;font-weight:700;color:var(--text-muted);margin-bottom:8px">Serviços cadastrados</h4>
          ${services.map(s => `
            <div class="wizard-review-service-chip">
              ${escapeHtml(s.name)}${s.duration_minutes ? ` · ${s.duration_minutes}min` : ""}${s.price ? ` · R$${s.price}` : ""}
            </div>
          `).join("")}
        </div>
      ` : ""}
    </div>
  `
}

function renderOnboardingSuccess() {
  const result = state.onboardingWizard.result
  const company = result?.company || {}
  const nextSteps = result?.next_steps || []

  return `
    <section class="hero" style="text-align:center">
      <span class="brand-eyebrow" style="color:var(--brand)">Setup completo</span>
      <h2 class="hero-title">${escapeHtml(company.name || "Empresa")} está pronta para operar.</h2>
      <p class="hero-copy">O contexto foi gerado, os módulos ativados e a empresa já está na plataforma.</p>
      <div class="hero-actions" style="justify-content:center">
        <button class="button" data-jump-view="studio">Testar o bot agora</button>
        <button class="button-ghost" data-jump-view="agenda">Abrir agenda</button>
        <button class="button-ghost" id="onboarding-reset">Configurar outra empresa</button>
      </div>
    </section>

    ${nextSteps.length > 0 ? `
      <section class="grid-one">
        <article class="card">
          <div class="card-header">
            <div>
              <h3 class="card-title">Próximos passos recomendados</h3>
              <p class="card-copy">Ações para completar a ativação e ir ao vivo.</p>
            </div>
          </div>
          <div class="timeline">
            ${nextSteps.map(s => `
              <div class="timeline-item">
                <strong>${escapeHtml(s.title)}</strong>
                <p>${escapeHtml(s.description)}</p>
              </div>
            `).join("")}
          </div>
        </article>
      </section>
    ` : ""}
  `
}

function renderOnboardingView() {
  if (state.onboardingWizard.completed && state.onboardingWizard.result) {
    return renderOnboardingSuccess()
  }

  const step = state.onboardingWizard.step
  const totalSteps = ONBOARDING_STEPS.length
  const isFirst = step === 0
  const isLast = step === totalSteps - 1

  let stepContent = ""
  if (step === 0) stepContent = renderOnboardingStep0()
  else if (step === 1) stepContent = renderOnboardingStep1()
  else if (step === 2) stepContent = renderOnboardingStep2()
  else if (step === 3) stepContent = renderOnboardingStep3()
  else if (step === 4) stepContent = renderOnboardingStep4()

  return `
    <section class="hero">
      <span class="brand-eyebrow">Setup Wizard</span>
      <h2 class="hero-title">Configure uma nova empresa em minutos.</h2>
      <p class="hero-copy">Preencha os dados e o bot será configurado automaticamente com contexto, agenda, lembretes e catálogo.</p>
    </section>

    <section class="grid-one">
      <article class="card">
        ${renderOnboardingStepIndicator()}

        <div class="wizard-body">
          ${stepContent}
        </div>

        <div class="wizard-nav">
          ${!isFirst ? '<button class="button-ghost" id="wizard-prev">Voltar</button>' : '<span></span>'}
          ${isLast
            ? '<button class="button" id="wizard-submit">Ativar empresa</button>'
            : '<button class="button" id="wizard-next">Próximo</button>'
          }
        </div>
      </article>
    </section>
  `
}

// ── Analytics View ──────────────────────────────────────────────────────

function analyticsKpiCard(label, value, subtitle, tone = "default") {
  const toneClass = tone === "success" ? "analytics-kpi-success" : tone === "danger" ? "analytics-kpi-danger" : tone === "warn" ? "analytics-kpi-warn" : ""
  return `
    <div class="analytics-kpi ${toneClass}">
      <span class="analytics-kpi-value">${escapeHtml(value)}</span>
      <span class="analytics-kpi-label">${escapeHtml(label)}</span>
      <span class="analytics-kpi-subtitle">${escapeHtml(subtitle)}</span>
    </div>
  `
}

function renderAnalyticsBar(segments, height = 32) {
  const total = segments.reduce((sum, s) => sum + s.value, 0) || 1
  return `
    <div class="analytics-bar" style="height:${height}px">
      ${segments.map(s => {
        const pct = Math.max((s.value / total) * 100, 0)
        return pct > 0 ? `<div class="analytics-bar-segment analytics-bar-${s.tone}" style="width:${pct}%" title="${escapeHtml(s.label)}: ${formatNumber(s.value)} (${pct.toFixed(1)}%)"></div>` : ""
      }).join("")}
    </div>
    <div class="analytics-bar-legend">
      ${segments.map(s => `<span class="analytics-legend-item"><span class="analytics-legend-dot analytics-bar-${s.tone}"></span>${escapeHtml(s.label)}: ${formatNumber(s.value)}</span>`).join("")}
    </div>
  `
}

function renderAnalyticsTrendChart(trend) {
  if (!trend || trend.length === 0) {
    return '<div class="empty-state">Sem dados de tendência para o período selecionado.</div>'
  }

  const maxVal = Math.max(...trend.map(m => m.total), 1)
  const chartHeight = 180
  const barWidth = Math.max(Math.min(Math.floor(600 / trend.length) - 8, 56), 20)

  return `
    <div class="analytics-chart-container">
      <div class="analytics-chart" style="height:${chartHeight + 40}px">
        ${trend.map((m, i) => {
          const totalH = Math.round((m.total / maxVal) * chartHeight)
          const attendedH = m.total > 0 ? Math.round((m.attended / m.total) * totalH) : 0
          const noShowH = m.total > 0 ? Math.round((m.no_show / m.total) * totalH) : 0
          const cancelledH = m.total > 0 ? Math.round((m.cancelled / m.total) * totalH) : 0
          const otherH = Math.max(totalH - attendedH - noShowH - cancelledH, 0)

          return `
            <div class="analytics-chart-col" style="width:${barWidth}px">
              <div class="analytics-chart-bar" style="height:${chartHeight}px">
                <div class="analytics-chart-stack">
                  ${otherH > 0 ? `<div class="analytics-bar-neutral" style="height:${otherH}px" title="Pendente: ${m.total - m.attended - m.no_show - m.cancelled}"></div>` : ""}
                  ${cancelledH > 0 ? `<div class="analytics-bar-warn" style="height:${cancelledH}px" title="Cancelados: ${m.cancelled}"></div>` : ""}
                  ${noShowH > 0 ? `<div class="analytics-bar-danger" style="height:${noShowH}px" title="No-show: ${m.no_show}"></div>` : ""}
                  ${attendedH > 0 ? `<div class="analytics-bar-success" style="height:${attendedH}px" title="Compareceu: ${m.attended}"></div>` : ""}
                </div>
              </div>
              <span class="analytics-chart-label">${escapeHtml(m.month)}</span>
              <span class="analytics-chart-sublabel">${m.no_show_rate}%</span>
            </div>
          `
        }).join("")}
      </div>
      <div class="analytics-bar-legend" style="margin-top:8px">
        <span class="analytics-legend-item"><span class="analytics-legend-dot analytics-bar-success"></span>Compareceu</span>
        <span class="analytics-legend-item"><span class="analytics-legend-dot analytics-bar-danger"></span>No-show</span>
        <span class="analytics-legend-item"><span class="analytics-legend-dot analytics-bar-warn"></span>Cancelado</span>
        <span class="analytics-legend-item"><span class="analytics-legend-dot analytics-bar-neutral"></span>Pendente</span>
      </div>
    </div>
  `
}

function renderAnalyticsFunnel(funnel) {
  const steps = [
    { label: "Agendamentos criados", value: funnel.bookings_created, tone: "neutral" },
    { label: "Ainda confirmados", value: funnel.still_confirmed, tone: "neutral" },
    { label: "Outcome registrado", value: funnel.outcome_recorded, tone: "neutral" },
    { label: "Compareceu", value: funnel.attended, tone: "success" },
    { label: "No-show", value: funnel.no_show, tone: "danger" },
  ]
  const maxVal = Math.max(...steps.map(s => s.value), 1)

  return `
    <div class="analytics-funnel">
      ${steps.map(s => {
        const pct = Math.round((s.value / maxVal) * 100)
        return `
          <div class="analytics-funnel-step">
            <div class="analytics-funnel-bar-wrap">
              <div class="analytics-funnel-bar analytics-bar-${s.tone}" style="width:${Math.max(pct, 4)}%"></div>
            </div>
            <span class="analytics-funnel-label">${escapeHtml(s.label)}</span>
            <span class="analytics-funnel-value">${formatNumber(s.value)}</span>
          </div>
        `
      }).join("")}
    </div>
  `
}

function renderReminderComparison(effectiveness) {
  const withR = effectiveness.with_reminder || {}
  const withoutR = effectiveness.without_reminder || {}
  const impact = effectiveness.reminder_impact || {}

  return `
    <div class="analytics-comparison">
      <div class="analytics-comparison-col">
        <h4 class="analytics-comparison-title">Com lembrete</h4>
        <div class="analytics-comparison-stat">
          <span class="analytics-comparison-big analytics-text-success">${withR.attendance_rate ?? 0}%</span>
          <span class="analytics-comparison-caption">comparecimento</span>
        </div>
        <div class="analytics-comparison-stat">
          <span class="analytics-comparison-big analytics-text-danger">${withR.no_show_rate ?? 0}%</span>
          <span class="analytics-comparison-caption">no-show</span>
        </div>
        <span class="analytics-comparison-meta">${formatNumber(withR.total || 0)} agendamentos</span>
      </div>
      <div class="analytics-comparison-divider"></div>
      <div class="analytics-comparison-col">
        <h4 class="analytics-comparison-title">Sem lembrete</h4>
        <div class="analytics-comparison-stat">
          <span class="analytics-comparison-big">${withoutR.attendance_rate ?? 0}%</span>
          <span class="analytics-comparison-caption">comparecimento</span>
        </div>
        <div class="analytics-comparison-stat">
          <span class="analytics-comparison-big analytics-text-danger">${withoutR.no_show_rate ?? 0}%</span>
          <span class="analytics-comparison-caption">no-show</span>
        </div>
        <span class="analytics-comparison-meta">${formatNumber(withoutR.total || 0)} agendamentos</span>
      </div>
    </div>
    ${impact.no_show_reduction_pp !== undefined ? `
      <div class="analytics-impact-banner ${impact.no_show_reduction_pp > 0 ? 'analytics-impact-positive' : 'analytics-impact-neutral'}">
        <strong>${impact.no_show_reduction_pp > 0 ? '-' : ''}${Math.abs(impact.no_show_reduction_pp)}pp</strong>
        <span>${impact.no_show_reduction_pp > 0 ? 'de redução no no-show com lembretes ativos' : 'Sem diferença significativa ainda — acumule dados'}</span>
      </div>
    ` : ""}
  `
}

function renderCancellationReasons(reasons) {
  const entries = Object.entries(reasons || {})
  if (entries.length === 0) {
    return '<div class="empty-state">Nenhum motivo de cancelamento registrado ainda.</div>'
  }
  const maxVal = Math.max(...entries.map(([, v]) => v), 1)
  return `
    <div class="analytics-reasons">
      ${entries.slice(0, 8).map(([reason, count]) => {
        const pct = Math.round((count / maxVal) * 100)
        return `
          <div class="analytics-reason-row">
            <span class="analytics-reason-label">${escapeHtml(reason)}</span>
            <div class="analytics-reason-bar-wrap">
              <div class="analytics-reason-bar" style="width:${Math.max(pct, 4)}%"></div>
            </div>
            <span class="analytics-reason-count">${formatNumber(count)}</span>
          </div>
        `
      }).join("")}
    </div>
  `
}

function renderQueueRecovery(queue) {
  const offers = queue?.offers || {}
  const entries = queue?.entries || {}
  return `
    <div class="analytics-queue-grid">
      <div class="analytics-queue-stat">
        <span class="analytics-queue-num">${formatNumber(entries.total || 0)}</span>
        <span class="analytics-queue-label">Na fila</span>
      </div>
      <div class="analytics-queue-stat">
        <span class="analytics-queue-num">${formatNumber(offers.total || 0)}</span>
        <span class="analytics-queue-label">Ofertas feitas</span>
      </div>
      <div class="analytics-queue-stat">
        <span class="analytics-queue-num analytics-text-success">${formatNumber(offers.accepted || 0)}</span>
        <span class="analytics-queue-label">Aceitas</span>
      </div>
      <div class="analytics-queue-stat">
        <span class="analytics-queue-num">${formatNumber(offers.expired || 0)}</span>
        <span class="analytics-queue-label">Expiradas</span>
      </div>
    </div>
    <div class="analytics-recovery-rate">
      <div class="analytics-recovery-bar-wrap">
        <div class="analytics-recovery-bar" style="width:${Math.min(queue?.recovery_rate || 0, 100)}%"></div>
      </div>
      <span class="analytics-recovery-label">${queue?.recovery_rate ?? 0}% taxa de recuperação</span>
    </div>
  `
}

function renderAnalyticsView() {
  const d = state.analyticsDashboard
  const periods = [
    { key: "7d", label: "7 dias" },
    { key: "30d", label: "30 dias" },
    { key: "90d", label: "90 dias" },
    { key: "6m", label: "6 meses" },
    { key: "12m", label: "12 meses" },
    { key: "all", label: "Tudo" },
  ]

  if (!d) {
    return `
      <section class="hero">
        <span class="brand-eyebrow">No-Show Analytics</span>
        <h2 class="hero-title">Calculando métricas...</h2>
        <div class="hero-actions">
          <div class="analytics-period-selector">
            ${periods.map(p => `
              <button class="analytics-period-btn ${state.analyticsPeriod === p.key ? 'active' : ''}" data-analytics-period="${p.key}">
                ${p.label}
              </button>
            `).join("")}
          </div>
          <button class="button-ghost" data-refresh-view>Atualizar</button>
        </div>
      </section>
      <section class="grid-four">
        ${Array.from({ length: 4 }, () => '<article class="card"><div class="skeleton-group"><div class="skeleton-line skeleton-header" style="width:50%"></div><div class="skeleton-line" style="width:70%"></div></div></article>').join("")}
      </section>
      ${renderSkeletonCards(2)}
    `
  }

  const kpis = d.kpis || {}
  const funnel = d.funnel || {}
  const reminders = d.reminders || {}
  const queue = d.queue_recovery || {}
  const trend = d.monthly_trend || []

  return `
    <section class="hero">
      <span class="brand-eyebrow">No-Show Analytics</span>
      <h2 class="hero-title">Reduza faltas com dados, não com achismo.</h2>
      <p class="hero-copy">
        Visualize taxas de comparecimento, impacto dos lembretes e recuperação de vagas canceladas. Cada métrica aqui é prova de valor para o negócio.
      </p>
      <div class="hero-actions">
        <div class="analytics-period-selector">
          ${periods.map(p => `
            <button class="analytics-period-btn ${state.analyticsPeriod === p.key ? 'active' : ''}" data-analytics-period="${p.key}">
              ${p.label}
            </button>
          `).join("")}
        </div>
        <button class="button-ghost" data-refresh-view>Atualizar</button>
      </div>
    </section>

    <section class="grid-four">
      ${analyticsKpiCard("Total agendamentos", formatNumber(kpis.total_bookings), "No período selecionado")}
      ${analyticsKpiCard("Taxa de comparecimento", kpis.attendance_rate + "%", "Dos que tiveram outcome", "success")}
      ${analyticsKpiCard("Taxa de no-show", kpis.no_show_rate + "%", "Dos que tiveram outcome", kpis.no_show_rate > 15 ? "danger" : kpis.no_show_rate > 5 ? "warn" : "success")}
      ${analyticsKpiCard("Taxa de cancelamento", kpis.cancellation_rate + "%", "Do total de agendamentos", kpis.cancellation_rate > 30 ? "danger" : kpis.cancellation_rate > 15 ? "warn" : "success")}
    </section>

    <section class="grid-two">
      <article class="card">
        <div class="card-header">
          <div>
            <h3 class="card-title">Distribuição de outcomes</h3>
            <p class="card-copy">Como os agendamentos se resolveram no período.</p>
          </div>
        </div>
        ${renderAnalyticsBar([
          { label: "Compareceu", value: kpis.attended || 0, tone: "success" },
          { label: "No-show", value: kpis.no_show || 0, tone: "danger" },
          { label: "Atrasado", value: kpis.late_arrival || 0, tone: "warn" },
          { label: "Cancelado", value: kpis.cancelled || 0, tone: "neutral" },
          { label: "Pendente", value: kpis.pending_outcome || 0, tone: "faint" },
        ], 36)}
      </article>

      <article class="card">
        <div class="card-header">
          <div>
            <h3 class="card-title">Funil de agendamento</h3>
            <p class="card-copy">Da criação ao comparecimento.</p>
          </div>
        </div>
        ${renderAnalyticsFunnel(funnel)}
      </article>
    </section>

    <section class="grid-one">
      <article class="card">
        <div class="card-header">
          <div>
            <h3 class="card-title">Tendência mensal</h3>
            <p class="card-copy">Evolução da taxa de no-show (%) ao longo do tempo. Barras mostram volume absoluto.</p>
          </div>
        </div>
        ${renderAnalyticsTrendChart(trend)}
      </article>
    </section>

    <section class="grid-two">
      <article class="card">
        <div class="card-header">
          <div>
            <h3 class="card-title">Impacto dos lembretes</h3>
            <p class="card-copy">Comparação direta: agendamentos com vs. sem lembrete enviado.</p>
          </div>
          <span class="tag tag-success">${formatNumber(reminders.total_sent || 0)} enviados</span>
        </div>
        ${renderReminderComparison(reminders.effectiveness || {})}
      </article>

      <article class="card">
        <div class="card-header">
          <div>
            <h3 class="card-title">Recuperação via fila</h3>
            <p class="card-copy">Vagas canceladas que foram reaproveitadas pela fila de espera.</p>
          </div>
        </div>
        ${renderQueueRecovery(queue)}
      </article>
    </section>

    <section class="grid-two">
      <article class="card">
        <div class="card-header">
          <div>
            <h3 class="card-title">Motivos de cancelamento</h3>
            <p class="card-copy">Os motivos mais recorrentes registrados nos cancelamentos.</p>
          </div>
        </div>
        ${renderCancellationReasons(d.cancellation_reasons)}
      </article>

      <article class="card">
        <div class="card-header">
          <div>
            <h3 class="card-title">Resumo operacional</h3>
            <p class="card-copy">KPIs complementares do período.</p>
          </div>
        </div>
        <div class="analytics-summary-grid">
          <div class="analytics-summary-item">
            <span class="analytics-summary-label">Confirmados ativos</span>
            <span class="analytics-summary-value">${formatNumber(kpis.confirmed)}</span>
          </div>
          <div class="analytics-summary-item">
            <span class="analytics-summary-label">Cancelados</span>
            <span class="analytics-summary-value">${formatNumber(kpis.cancelled)}</span>
          </div>
          <div class="analytics-summary-item">
            <span class="analytics-summary-label">Compareceram</span>
            <span class="analytics-summary-value analytics-text-success">${formatNumber(kpis.attended)}</span>
          </div>
          <div class="analytics-summary-item">
            <span class="analytics-summary-label">No-show</span>
            <span class="analytics-summary-value analytics-text-danger">${formatNumber(kpis.no_show)}</span>
          </div>
          <div class="analytics-summary-item">
            <span class="analytics-summary-label">Chegada tardia</span>
            <span class="analytics-summary-value">${formatNumber(kpis.late_arrival)}</span>
          </div>
          <div class="analytics-summary-item">
            <span class="analytics-summary-label">Lembretes enviados</span>
            <span class="analytics-summary-value">${formatNumber(reminders.total_sent || 0)}</span>
          </div>
          <div class="analytics-summary-item">
            <span class="analytics-summary-label">Lembretes pendentes</span>
            <span class="analytics-summary-value">${formatNumber(reminders.total_pending || 0)}</span>
          </div>
          <div class="analytics-summary-item">
            <span class="analytics-summary-label">Período</span>
            <span class="analytics-summary-value">${escapeHtml(d.period)}</span>
          </div>
        </div>
      </article>
    </section>
  `
}

function formatSessionTimestamp(iso) {
  if (!iso) return ""
  try {
    const date = new Date(iso)
    if (Number.isNaN(date.getTime())) return ""
    return date.toLocaleString("pt-BR", { hour: "2-digit", minute: "2-digit", day: "2-digit", month: "2-digit" })
  } catch {
    return ""
  }
}

function renderStudioView() {
  const context = state.companyContext
  const operatorContacts = Array.isArray(context?.internal_operation?.operator_contact_ids)
    ? context.internal_operation.operator_contact_ids
    : []
  const sessions = getStudioSessions()
  const activeSession = getActiveStudioSession()
  const showNewForm = state.studio.showNewForm || sessions.length === 0
  const draftNew = state.studio.newSession

  const sessionItems = sessions
    .slice()
    .sort((a, b) => String(b.updated_at || b.created_at).localeCompare(String(a.updated_at || a.created_at)))
    .map((session) => {
      const isActive = activeSession && activeSession.id === session.id
      const lastMsg = session.messages[session.messages.length - 1]
      const preview = lastMsg ? lastMsg.text : "Nenhuma mensagem ainda."
      return `
        <button class="card card-muted studio-session-item ${isActive ? "is-active" : ""}" data-studio-session="${escapeHtml(session.id)}" style="display:block;text-align:left;cursor:pointer;margin-bottom:10px;padding:12px;${isActive ? "border-color: var(--brand);" : ""}">
          <div style="display:flex;justify-content:space-between;gap:8px;align-items:flex-start;">
            <strong style="font-size:13px;">${escapeHtml(session.label || session.contact_id)}</strong>
            <span class="meta-chip">${escapeHtml(formatSessionTimestamp(session.updated_at || session.created_at))}</span>
          </div>
          <div class="card-copy" style="font-size:12px;margin-top:4px;">${escapeHtml(session.contact_id)}</div>
          <div class="card-copy" style="font-size:11px;margin-top:6px;opacity:0.75;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${escapeHtml(preview.slice(0, 80))}</div>
          <div style="margin-top:8px;display:flex;gap:6px;">
            <span class="chip" style="font-size:10px;">${session.messages.length} msgs</span>
            <span class="chip chip-danger" data-studio-delete-session="${escapeHtml(session.id)}" style="font-size:10px;cursor:pointer;">Remover</span>
          </div>
        </button>
      `
    })
    .join("")

  const newSessionForm = showNewForm
    ? `
        <form id="studio-new-session-form" class="form-grid" style="margin-top:12px;gap:10px;">
          <div class="field">
            <label for="studio-new-label">Rótulo da sessão</label>
            <input id="studio-new-label" name="label" value="${escapeHtml(draftNew.label)}" placeholder="Ex.: Cliente Premium" />
          </div>
          <div class="field">
            <label for="studio-new-contact-id">Contact ID</label>
            <input id="studio-new-contact-id" name="contactId" value="${escapeHtml(draftNew.contactId)}" placeholder="5511999999999" />
          </div>
          <div class="field">
            <label for="studio-new-contact-name">Nome exibido</label>
            <input id="studio-new-contact-name" name="contactName" value="${escapeHtml(draftNew.contactName)}" placeholder="Opcional" />
          </div>
          <div class="toolbar-row">
            <button class="button" type="submit">Criar sessão</button>
            ${sessions.length ? '<button class="button-ghost" type="button" id="studio-cancel-new">Cancelar</button>' : ""}
            ${
              operatorContacts.length
                ? `<button class="button-ghost" type="button" id="studio-use-operator">Usar operador ${escapeHtml(operatorContacts[0])}</button>`
                : ""
            }
          </div>
        </form>
      `
    : ""

  const chatPane = activeSession
    ? `
        <article class="card chat-shell">
          <div class="section-header">
            <div>
              <h3 class="section-title">${escapeHtml(activeSession.label || activeSession.contact_id)}</h3>
              <p class="section-copy">Contact ID: ${escapeHtml(activeSession.contact_id)}${activeSession.contact_name ? " · " + escapeHtml(activeSession.contact_name) : ""}</p>
            </div>
            ${
              activeSession.last_result
                ? `<span class="tag ${getReadinessTone(activeSession.last_result.action === "auto_reply" ? "ready" : "warning")}">${escapeHtml(activeSession.last_result.action)}</span>`
                : ""
            }
          </div>
          <div class="chip-row">
            <button class="chip" data-studio-prompt="Quero agendar uma consultoria inicial para amanhã às 10h">Agendamento</button>
            <button class="chip" data-studio-prompt="Preciso remarcar meu horário de amanhã para sexta à tarde">Reagendamento</button>
            <button class="chip" data-studio-prompt="Vou cancelar meu atendimento de amanhã">Cancelamento</button>
            <button class="chip" data-studio-prompt="Marque a Maria Silva amanhã às 10h para consultoria inicial">Operação interna</button>
          </div>
          <div class="chat-log" id="studio-chat-log">
            ${
              activeSession.messages.length
                ? activeSession.messages
                    .map((item) => {
                      const ts = formatSessionTimestamp(item.at)
                      if (item.role === "user") {
                        return `
                          <div class="bubble bubble-user">
                            ${escapeHtml(item.text)}
                            ${ts ? `<div class="bubble-meta"><span class="meta-chip">${escapeHtml(ts)}</span></div>` : ""}
                          </div>
                        `
                      }
                      const meta = item.meta || {}
                      return `
                        <div class="bubble bubble-assistant">
                          ${escapeHtml(item.text)}
                          <div class="bubble-meta">
                            ${ts ? `<span class="meta-chip">${escapeHtml(ts)}</span>` : ""}
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
          <form id="studio-form" class="form-grid" style="margin-top:12px;">
            <div class="field">
              <label for="studio-message">Mensagem como ${escapeHtml(activeSession.label || activeSession.contact_id)}</label>
              <textarea id="studio-message" name="message" placeholder="Ex.: Quero remarcar meu atendimento para sexta às 15h">${escapeHtml(state.studio.draftMessage)}</textarea>
            </div>
            <div class="toolbar-row">
              <button class="button" type="submit">${ICONS.send}<span>Enviar para o runtime</span></button>
              <button class="button-ghost" type="button" id="clear-studio-log">Limpar histórico</button>
            </div>
          </form>
        </article>
      `
    : `
        <article class="card chat-shell">
          <div class="empty-state">Selecione uma sessão na lista ou crie uma nova para começar a testar.</div>
        </article>
      `

  return `
    <section class="hero">
      <span class="brand-eyebrow">Bot Studio</span>
      <h2 class="hero-title">Teste conversa real antes de colocar para rodar.</h2>
      <p class="hero-copy">
        Crie sessões de teste por contato, mantenha o histórico e valide o fluxo natural sem reinformar telefone a cada mensagem. Toda troca passa pelo mesmo runtime, através da rota autenticada /admin/simulator/messages.
      </p>
    </section>

    <section class="two-pane">
      <article class="card">
        <div class="section-header">
          <div>
            <h3 class="section-title">Sessões de teste</h3>
            <p class="section-copy">${sessions.length} ${sessions.length === 1 ? "sessão salva" : "sessões salvas"} neste navegador.</p>
          </div>
          ${!showNewForm ? '<button class="button" type="button" id="studio-start-new">Nova sessão</button>' : ""}
        </div>
        <div class="studio-sessions-list">
          ${sessionItems || '<div class="empty-state" style="padding:12px;">Nenhuma sessão ainda. Crie a primeira abaixo.</div>'}
        </div>
        ${newSessionForm}
      </article>

      ${chatPane}
    </section>
  `
}

function iconForPipeline(pipeline) {
  const key = (pipeline.key || "").toLowerCase()
  const label = (pipeline.label || "").toLowerCase()
  const haystack = `${key} ${label}`
  if (haystack.includes("reagend") || haystack.includes("remarc")) return ICONS.refresh
  if (haystack.includes("cancel")) return ICONS.trash
  if (haystack.includes("agend") || haystack.includes("book")) return ICONS.calendar
  if (haystack.includes("handoff") || haystack.includes("operador") || haystack.includes("humano")) return ICONS.handoff
  if (haystack.includes("follow") || haystack.includes("convers")) return ICONS.send
  if (haystack.includes("triagem") || haystack.includes("intencao")) return ICONS.target
  if (haystack.includes("confirma")) return ICONS.check
  return ICONS.spark
}

function renderFlowCanvas(pipelines) {
  const canvasWidth = 2400
  const canvasHeight = 1600

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

  const zoom = state.flowEditor.zoom || 1
  const zoomPct = Math.round(zoom * 100)

  return `
    <article class="card flow-canvas-card">
      <div class="flow-canvas-toolbar">
        <div class="flow-canvas-toolbar-group">
          <span class="tag">${escapeHtml(String(pipelines.length))} etapas</span>
          <span class="subtle" style="font-size:12px;">Arraste nós ou clique em um template para adicionar.</span>
        </div>
        <div class="flow-canvas-toolbar-group">
          <button class="button-icon" data-flow-zoom="out" title="Zoom out">${ICONS.zoomOut}</button>
          <span class="flow-zoom-readout">${zoomPct}%</span>
          <button class="button-icon" data-flow-zoom="in" title="Zoom in">${ICONS.zoomIn}</button>
          <button class="button-icon" data-flow-zoom="fit" title="Ajustar à tela">${ICONS.fit}</button>
        </div>
      </div>
      <div class="flow-canvas-wrapper" id="flow-canvas-wrapper">
        <div class="flow-canvas-inner" id="flow-canvas-inner" style="transform: scale(${zoom});">
          <div class="flow-canvas-grid"></div>
          <svg class="flow-svg" viewBox="0 0 ${canvasWidth} ${canvasHeight}" preserveAspectRatio="none">
            <defs>
              <marker id="flow-arrow" markerWidth="12" markerHeight="12" refX="8" refY="6" orient="auto">
                <path d="M 0 0 L 12 6 L 0 12 z" fill="rgba(37, 211, 102, 0.75)"></path>
              </marker>
            </defs>
            ${edges.join("")}
          </svg>
          ${pipelines
            .map((pipeline) => {
              const isSelected = state.flowEditor.selectedPipelineKey === pipeline.key
              const nextCount = (pipeline.next_keys || []).length
              const triggers = (pipeline.trigger_terms || []).length
              return `
                <button
                  type="button"
                  class="flow-node ${isSelected ? "selected" : ""}"
                  data-select-pipeline="${escapeHtml(pipeline.key)}"
                  data-pipeline-key="${escapeHtml(pipeline.key)}"
                  style="left:${Number(pipeline.canvas_position?.x || 0)}px; top:${Number(pipeline.canvas_position?.y || 0)}px;"
                >
                  <div class="flow-node-head">
                    <span class="flow-node-icon">${iconForPipeline(pipeline)}</span>
                    <span class="flow-node-kicker">${escapeHtml(pipeline.key)}</span>
                  </div>
                  <strong>${escapeHtml(pipeline.label || pipeline.key)}</strong>
                  <p>${escapeHtml(pipeline.objective || "Sem objetivo descrito ainda.")}</p>
                  <div class="flow-node-footer">
                    <span class="footer-chip">${nextCount} transições</span>
                    <span class="footer-chip">${triggers} triggers</span>
                  </div>
                </button>
              `
            })
            .join("")}
        </div>
      </div>
    </article>
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
      <div class="field">
        <label>Próximas etapas</label>
        <input name="next_keys" value="${escapeHtml(nextKeysValue)}" placeholder="Ex.: appointment_management, conversion_followup" />
        <small>Keys disponíveis: ${escapeHtml(allPipelines.map((item) => item.key).join(", "))}</small>
      </div>
      <input type="hidden" name="canvas_x" value="${escapeHtml(String(Number(pipeline.canvas_position?.x || 0)))}" />
      <input type="hidden" name="canvas_y" value="${escapeHtml(String(Number(pipeline.canvas_position?.y || 0)))}" />
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
        <button type="button" class="button-icon" data-month-nav="prev" aria-label="Mês anterior">${ICONS.chevronLeft}</button>
        <strong class="calendar-month-label">${MONTH_NAMES_PT[month]} de ${year}</strong>
        <button type="button" class="button-icon" data-month-nav="next" aria-label="Próximo mês">${ICONS.chevronRight}</button>
        <button type="button" class="button-ghost" data-month-nav="today">Hoje</button>
      </div>
      <div class="segmented">
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

function renderFlowPalette() {
  return `
    <aside class="card flow-palette">
      <h4>Templates</h4>
      ${FLOW_NODE_TEMPLATES.map((group) => `
        <div class="flow-palette-group">
          <h4>${escapeHtml(group.category)}</h4>
          ${group.items.map((item, idx) => `
            <button type="button" class="flow-palette-item" data-flow-template="${escapeHtml(group.category)}:${idx}">
              <span class="palette-icon">${ICONS[item.icon] || ICONS.spark}</span>
              <span>
                <strong>${escapeHtml(item.label)}</strong>
                <span>${escapeHtml(item.description)}</span>
              </span>
            </button>
          `).join("")}
        </div>
      `).join("")}
    </aside>
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
      <h2 class="hero-title">Desenhe o fluxo conversacional em um canvas visual.</h2>
      <p class="hero-copy">
        Clique num template à esquerda para adicionar, arraste os nós para posicionar, e ajuste comportamento no inspector à direita. Tudo persiste no company context e altera o comportamento real do bot.
      </p>
      <div class="hero-actions">
        <button class="button" id="save-pipelines">${ICONS.check}<span>Salvar no company context</span></button>
        <button class="button-ghost" id="add-pipeline-stage">${ICONS.plus}<span>Etapa em branco</span></button>
      </div>
      <div class="chip-row" style="margin-top:6px;">
        <span class="chip" style="cursor:default;">1 · Escolha um template à esquerda</span>
        <span class="chip" style="cursor:default;">2 · Arraste os nós para organizar o mapa</span>
        <span class="chip" style="cursor:default;">3 · Edite objetivo e transições no inspector</span>
        <span class="chip" style="cursor:default;">4 · Salvar aplica no runtime do bot</span>
      </div>
    </section>

    <section class="flow-layout">
      ${renderFlowPalette()}
      ${pipelines.length ? renderFlowCanvas(pipelines) : '<article class="card"><div class="empty-state">Nenhum pipeline configurado. Clique em um template à esquerda para começar.</div></article>'}
      <aside class="card flow-inspector">
        <div class="flow-inspector-header">
          <strong>${selectedPipeline ? escapeHtml(selectedPipeline.label || selectedPipeline.key) : "Inspector"}</strong>
          ${selectedPipeline ? `<span class="tag">${escapeHtml(selectedPipeline.key)}</span>` : ""}
        </div>
        ${renderFlowInspector(selectedPipeline, pipelines)}
      </aside>
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

const WEEKDAY_DEFS = [
  { key: "monday", label: "Seg" },
  { key: "tuesday", label: "Ter" },
  { key: "wednesday", label: "Qua" },
  { key: "thursday", label: "Qui" },
  { key: "friday", label: "Sex" },
  { key: "saturday", label: "Sáb" },
  { key: "sunday", label: "Dom" },
]

function ensureSchedulingPolicy(context) {
  if (!context.scheduling_policy || typeof context.scheduling_policy !== "object") {
    context.scheduling_policy = {}
  }
  const policy = context.scheduling_policy
  if (typeof policy.enabled !== "boolean") {
    policy.enabled = Array.isArray(policy.profiles) && policy.profiles.length > 0
  }
  if (!Array.isArray(policy.profiles)) {
    policy.profiles = []
  }
  policy.profiles.forEach((profile) => {
    if (!profile || typeof profile !== "object") return
    if (!profile.day_windows || typeof profile.day_windows !== "object") {
      profile.day_windows = {}
    }
    WEEKDAY_DEFS.forEach(({ key }) => {
      const value = profile.day_windows[key]
      if (value == null) {
        profile.day_windows[key] = []
        return
      }
      if (Array.isArray(value)) {
        profile.day_windows[key] = value
          .map((item) => normalizeRangeString(item))
          .filter(Boolean)
        return
      }
      if (typeof value === "string") {
        profile.day_windows[key] = value
          .split(/[,;]/)
          .map((part) => normalizeRangeString(part))
          .filter(Boolean)
        return
      }
      profile.day_windows[key] = []
    })
    if (!Array.isArray(profile.allowed_weekdays)) {
      profile.allowed_weekdays = WEEKDAY_DEFS
        .filter(({ key }) => (profile.day_windows[key] || []).length > 0)
        .map(({ key }) => key)
    }
  })
  return context
}

function normalizeRangeString(value) {
  if (!value || typeof value !== "string") return null
  const match = value.trim().match(/^(\d{1,2}):?(\d{2})\s*[-→–—]\s*(\d{1,2}):?(\d{2})$/)
  if (match) {
    const h1 = String(match[1]).padStart(2, "0")
    const h2 = String(match[3]).padStart(2, "0")
    return `${h1}:${match[2]}-${h2}:${match[4]}`
  }
  const loose = value.trim().match(/^(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})$/)
  if (loose) {
    const h1 = String(loose[1]).padStart(2, "0")
    const h2 = String(loose[3]).padStart(2, "0")
    return `${h1}:${loose[2]}-${h2}:${loose[4]}`
  }
  return null
}

function parseRangeString(value) {
  const match = String(value || "").match(/^(\d{2}):(\d{2})-(\d{2}):(\d{2})$/)
  if (!match) return { startMin: 0, endMin: 60 }
  const startMin = Number(match[1]) * 60 + Number(match[2])
  const endMin = Number(match[3]) * 60 + Number(match[4])
  return { startMin, endMin }
}

function minutesToClock(minutes) {
  const clamped = Math.max(0, Math.min(24 * 60, Math.round(minutes / 15) * 15))
  const h = Math.floor(clamped / 60)
  const m = clamped % 60
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`
}

function rangeToString(startMin, endMin) {
  return `${minutesToClock(startMin)}-${minutesToClock(endMin)}`
}

function renderSchedulingPolicyEditor(context) {
  const policy = context.scheduling_policy || { enabled: false, profiles: [] }
  const enabled = !!policy.enabled
  const profiles = Array.isArray(policy.profiles) ? policy.profiles : []

  const toggleCard = `
    <section class="card">
      <div class="section-header">
        <div>
          <h3 class="section-title">Política de agendamento</h3>
          <p class="section-copy">Mestre do módulo de agenda. Quando desligado, o bot não aceita nem sugere horários.</p>
        </div>
        <label class="meta-chip" style="gap: 10px;">
          <input id="scheduling-policy-enabled" type="checkbox" ${enabled ? "checked" : ""} />
          <strong>${enabled ? "Agendamento ativo" : "Agendamento desligado"}</strong>
        </label>
      </div>
      ${enabled ? "" : '<div class="empty-state">Ative a política para editar perfis, janelas semanais e antecedência mínima.</div>'}
    </section>
  `

  if (!enabled) {
    return toggleCard
  }

  const profilesHtml = profiles.length
    ? profiles
        .map((profile, profileIndex) => renderSchedulingProfileCard(profile, profileIndex))
        .join("")
    : '<div class="empty-state">Nenhum perfil de agendamento configurado. Use o Flow Studio ou edite o company context diretamente para criar um.</div>'

  return `
    ${toggleCard}
    <section class="card">
      <div class="section-header">
        <div>
          <h3 class="section-title">Perfis de agendamento</h3>
          <p class="section-copy">Cada perfil define duração padrão, antecedência mínima e janelas semanais. Arraste as bordas das barras para ajustar em 15 minutos.</p>
        </div>
        <button class="button" id="save-scheduling-policy" type="button">${ICONS.check}<span>Salvar política</span></button>
      </div>
      <div class="booking-stack">
        ${profilesHtml}
      </div>
    </section>
  `
}

function renderSchedulingProfileCard(profile, profileIndex) {
  const key = profile.key || `profile_${profileIndex}`
  const label = profile.label || key
  const defaultDuration = Number(profile.default_duration_minutes || profile.slot_increment_minutes || 60)
  const minNotice = Number(profile.minimum_notice_minutes || 0)
  const allowedSet = new Set((profile.allowed_weekdays || []).map((item) => String(item).toLowerCase()))

  const weekdaysHtml = WEEKDAY_DEFS.map(({ key: wday, label: wlabel }) => {
    const ranges = Array.isArray(profile.day_windows?.[wday]) ? profile.day_windows[wday] : []
    const accepted = allowedSet.has(wday)
    const rangesHtml = ranges
      .map((range, rangeIndex) => renderRangeRow(profileIndex, wday, rangeIndex, range))
      .join("")
    return `
      <div class="queue-item" data-scheduling-weekday-row="${escapeHtml(key)}:${wday}">
        <div class="inline-actions" style="justify-content: space-between; align-items: center;">
          <label class="meta-chip">
            <input type="checkbox" data-scheduling-weekday-toggle="${profileIndex}:${wday}" ${accepted ? "checked" : ""} />
            <strong>${wlabel}</strong>
            <span class="subtle">aceita agendamento</span>
          </label>
          <button class="button-ghost" type="button" data-scheduling-add-range="${profileIndex}:${wday}">${ICONS.plus}<span>janela</span></button>
        </div>
        <div class="scheduling-range-list" style="display:flex;flex-direction:column;gap:10px;margin-top:10px;">
          ${rangesHtml || '<p class="subtle">Sem janelas cadastradas neste dia.</p>'}
        </div>
      </div>
    `
  }).join("")

  return `
    <article class="booking-card" data-scheduling-profile-card="${profileIndex}">
      <div class="inline-actions" style="justify-content: space-between; align-items: center;">
        <div>
          <strong>${escapeHtml(label)}</strong>
          <p class="subtle">key: <span class="tag">${escapeHtml(key)}</span></p>
        </div>
      </div>
      <div class="form-grid-two" style="margin-top:12px;">
        <div class="field">
          <label>Duração padrão (min)</label>
          <input type="number" min="5" step="5" data-scheduling-field="${profileIndex}:default_duration_minutes" value="${escapeHtml(String(defaultDuration))}" />
        </div>
        <div class="field">
          <label>Antecedência mínima (min)</label>
          <input type="number" min="0" step="5" data-scheduling-field="${profileIndex}:minimum_notice_minutes" value="${escapeHtml(String(minNotice))}" />
        </div>
      </div>
      <div class="queue-stack" style="margin-top:14px;display:flex;flex-direction:column;gap:10px;">
        ${weekdaysHtml}
      </div>
    </article>
  `
}

function renderRangeRow(profileIndex, weekday, rangeIndex, rangeValue) {
  const { startMin, endMin } = parseRangeString(rangeValue)
  const dayMinutes = 24 * 60
  const leftPct = (startMin / dayMinutes) * 100
  const widthPct = Math.max(1, ((endMin - startMin) / dayMinutes) * 100)
  const id = `${profileIndex}:${weekday}:${rangeIndex}`
  return `
    <div class="scheduling-range-row" data-scheduling-range="${id}" style="display:flex;align-items:center;gap:10px;">
      <span class="tag" data-scheduling-range-label="${id}">${minutesToClock(startMin)} → ${minutesToClock(endMin)}</span>
      <div class="scheduling-range-bar" data-scheduling-range-bar="${id}" style="position:relative;flex:1;height:28px;background:rgba(37,211,102,0.08);border:1px solid var(--border);border-radius:8px;touch-action:none;">
        <div class="scheduling-range-fill" data-scheduling-range-fill="${id}" style="position:absolute;top:0;bottom:0;left:${leftPct}%;width:${widthPct}%;background:rgba(37,211,102,0.45);border-radius:6px;">
          <div data-scheduling-range-handle="${id}:start" style="position:absolute;left:-6px;top:0;bottom:0;width:12px;cursor:ew-resize;background:rgba(0,0,0,0.2);border-radius:4px;"></div>
          <div data-scheduling-range-handle="${id}:end" style="position:absolute;right:-6px;top:0;bottom:0;width:12px;cursor:ew-resize;background:rgba(0,0,0,0.2);border-radius:4px;"></div>
        </div>
      </div>
      <button class="button-danger" type="button" data-scheduling-remove-range="${id}" title="Remover janela">${ICONS.trash}</button>
    </div>
  `
}

function renderManualBookingModal() {
  const draft = state.drafts.manualBooking
  return `
    <div class="modal-backdrop" data-manual-booking-backdrop>
      <div class="modal">
        <div class="section-header">
          <div>
            <h3 class="section-title">Agendamento manual</h3>
            <p class="section-copy">Registre manualmente ou peça ao bot para confirmar com o cliente antes de gravar como firme.</p>
          </div>
          <button class="button-ghost" type="button" id="close-manual-booking">Fechar</button>
        </div>
        <form id="manual-booking-form" class="form-grid">
          <div class="form-grid-two">
            <div class="field">
              <label>Contact ID</label>
              <input name="contact_id" value="${escapeHtml(draft.contactId)}" placeholder="Ex.: 5511999999999" />
            </div>
            <div class="field">
              <label>Título</label>
              <input name="title" value="${escapeHtml(draft.title)}" />
            </div>
          </div>
          <div class="form-grid-two">
            <div class="field">
              <label>Início</label>
              <input name="starts_at" type="datetime-local" value="${escapeHtml(draft.startsAt)}" />
            </div>
            <div class="field">
              <label>Fim</label>
              <input name="ends_at" type="datetime-local" value="${escapeHtml(draft.endsAt)}" />
            </div>
          </div>
          <div class="field">
            <label>Descrição</label>
            <textarea name="description">${escapeHtml(draft.description)}</textarea>
          </div>
          <div class="inline-actions" style="justify-content:flex-end;">
            <button class="button-ghost" type="button" data-manual-booking-submit="direct">Agendar direto</button>
            <button class="button" type="button" data-manual-booking-submit="bot">${ICONS.send}<span>Bot confirma com cliente</span></button>
          </div>
        </form>
      </div>
    </div>
  `
}

function renderAgendaView() {
  const context = ensureSchedulingPolicy(state.companyContext || {})
  state.companyContext = context
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
      <div class="inline-actions" style="margin-top:16px;">
        <button class="button" type="button" id="open-manual-booking">${ICONS.plus}<span>Agendamento manual</span></button>
      </div>
    </section>

    ${renderSchedulingPolicyEditor(context)}

    <section class="grid-three">
      ${renderMetricCard("Provider", integration.provider || "—", "Integração ativa no escopo atual.", "default", "spark")}
      ${renderMetricCard("Bookings", formatNumber(bookings.length), "Reservas visíveis no escopo selecionado.", "default", "calendar")}
      ${renderMetricCard("Fila ativa", formatNumber(queueEntries.length), "Clientes esperando encaixe ou antecipação.", "warm", "clock")}
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
             <div class="segmented">
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

function contactInitials(contact) {
  const name = (contact.contact_name || contact.contact_id || "?").trim()
  const parts = name.split(/\s+/)
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase()
  return name.slice(0, 2).toUpperCase()
}

function renderContactsView() {
  return `
    <section class="hero">
      <span class="brand-eyebrow">Contacts Desk</span>
      <h2 class="hero-title">Base de contatos limpa deixa o bot preciso e a operação menos cega.</h2>
      <p class="hero-copy">
        Pesquise, corrija identificação, defina customer_profile e registre notas úteis para decisão humana e automação.
      </p>
    </section>

    <section class="card">
      <div class="section-header">
        <div>
          <h3 class="section-title">Busca e atualização</h3>
          <p class="section-copy">Nome, número ou contato completo — o filtro é tolerante.</p>
        </div>
        <span class="tag">${formatNumber(state.contacts.length)} contatos</span>
      </div>
      <form id="contact-search-form" class="form-grid-two">
        <div class="field">
          <label>Buscar contato</label>
          <input name="search" value="${escapeHtml(state.drafts.contactSearch)}" placeholder="Ex.: Maria, 5511..." />
        </div>
        <div class="inline-actions" style="align-self: end;">
          <button class="button" type="submit">${ICONS.search}<span>Buscar</span></button>
          <button class="button-ghost" type="button" id="clear-contact-search">${ICONS.refresh}<span>Limpar</span></button>
        </div>
      </form>
    </section>

    <section class="card">
      <div class="section-header">
        <div>
          <h3 class="section-title">Base de contatos</h3>
          <p class="section-copy">Clique num contato para expandir edição inline.</p>
        </div>
      </div>
      ${
        state.contacts.length
          ? `<div class="contact-grid">${state.contacts.map((contact) => `
              <details class="contact-row" data-contact-details="${escapeHtml(contact.contact_id)}">
                <summary class="contact-row" style="display:grid;grid-template-columns:44px minmax(0,2fr) repeat(2, minmax(0,1fr)) 20px;gap:16px;align-items:center;cursor:pointer;list-style:none;padding:0;border:0;background:transparent;">
                  <div class="contact-avatar">${escapeHtml(contactInitials(contact))}</div>
                  <div class="contact-name">
                    <strong>${escapeHtml(contact.contact_name || "Sem nome")}</strong>
                    <span>${escapeHtml(contact.contact_id || "—")}</span>
                  </div>
                  <div class="contact-meta-cell">
                    <span>Pipeline</span>
                    <strong>${escapeHtml(contact.pipeline_stage || "—")}</strong>
                  </div>
                  <div class="contact-meta-cell">
                    <span>Perfil</span>
                    <strong>${escapeHtml(contact.customer_profile || "—")}</strong>
                  </div>
                  <span class="subtle">${ICONS.chevronRight}</span>
                </summary>
                <form class="form-grid" data-contact-update="${escapeHtml(contact.contact_id)}" style="margin-top:14px;padding-top:14px;border-top:1px dashed var(--border);">
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
                    <button class="button-ghost" type="submit">${ICONS.check}<span>Atualizar contato</span></button>
                  </div>
                </form>
              </details>
            `).join("")}</div>`
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
      ${renderMetricCard("Curados", formatNumber(curation.total_curated_examples), "Exemplos aprovados e elegíveis no escopo atual.", "default", "check")}
      ${renderMetricCard("Train", formatNumber(split.train_examples), "Volume atual de treino após split.", "default", "training")}
      ${renderMetricCard("Ready", readiness.is_ready_for_finetuning ? "Sim" : "Não", "Situação atual de prontidão para fine-tuning.", "warm", "spark")}
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
            <button class="button" type="submit">${ICONS.send}<span>Executar importação</span></button>
            <button class="button-ghost" type="button" id="refresh-training-data">${ICONS.refresh}<span>Atualizar relatório</span></button>
          </div>
        </form>
      </article>

      <article class="card">
        <div class="section-header">
          <div>
            <h3 class="section-title">Dataset review e jobs</h3>
            <p class="section-copy">Leitura rápida do que está pronto e do histórico recente de fine-tuning.</p>
          </div>
          <button class="button-ghost" id="create-finetuning-job">${ICONS.plus}<span>Criar job</span></button>
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

const TEAM_ROLE_LABELS = {
  owner: "Proprietário",
  admin: "Administrador",
  operator: "Operador",
  viewer: "Visualizador",
}

function renderTeamRoleOptions(currentRole, requesterRole) {
  const allowedRoles = requesterRole === "owner"
    ? ["viewer", "operator", "admin", "owner"]
    : ["viewer", "operator"]
  return allowedRoles
    .map(
      (role) =>
        `<option value="${role}" ${role === currentRole ? "selected" : ""}>${TEAM_ROLE_LABELS[role]}</option>`
    )
    .join("")
}

function renderTeamView() {
  const membership = getCurrentIdentityMembership()
  const requesterRole = membership?.role || ""
  const selectedCompany = getSelectedCompany()
  const canManage = canManageCurrentTeam()

  if (state.session?.auth_mode !== "identity") {
    return `
      <section class="hero">
        <span class="brand-eyebrow">Identity & Access</span>
        <h2 class="hero-title">Gestão de equipe requer uma conta individual.</h2>
        <p class="hero-copy">
          Esta sessão usa o acesso administrativo legado. Entre com email e senha para administrar
          membros, papéis e convites com rastreabilidade por usuário.
        </p>
        <div class="hero-actions">
          <button class="button" id="team-open-session">Abrir sessão</button>
        </div>
      </section>
    `
  }

  if (!selectedCompany || !state.config.companyId) {
    return `
      <section class="hero">
        <span class="brand-eyebrow">Identity & Access</span>
        <h2 class="hero-title">Selecione uma empresa para administrar a equipe.</h2>
        <p class="hero-copy">Os acessos são sempre isolados por tenant e nunca alterados em escopo global.</p>
      </section>
    `
  }

  if (!canManage) {
    return `
      <section class="hero">
        <span class="brand-eyebrow">Identity & Access</span>
        <h2 class="hero-title">Seu acesso a ${escapeHtml(selectedCompany.name)} é ${escapeHtml(TEAM_ROLE_LABELS[requesterRole] || requesterRole)}.</h2>
        <p class="hero-copy">
          Apenas proprietários e administradores podem listar membros ou alterar permissões.
          Solicite a um responsável da empresa quando precisar de uma mudança de acesso.
        </p>
      </section>
    `
  }

  const activeMembers = state.teamMembers.filter((member) => member.active).length
  const invitationUrl = state.teamInvitation?.action_token
    ? `${window.location.origin}/platform?invitation_token=${encodeURIComponent(state.teamInvitation.action_token)}`
    : ""

  return `
    <section class="hero">
      <span class="brand-eyebrow">Identity & Access</span>
      <h2 class="hero-title">Controle quem pode operar ${escapeHtml(selectedCompany.name)}.</h2>
      <p class="hero-copy">
        Convites expiram, sessões podem ser revogadas e cada alteração respeita o papel do usuário
        dentro desta empresa.
      </p>
      <div class="hero-actions">
        <button class="button-ghost" data-refresh-view>Atualizar equipe</button>
      </div>
    </section>

    <section class="grid-three">
      ${renderMetricCard("Membros", formatNumber(state.teamMembers.length), "Cadastros vinculados ao tenant.", "default", "contacts")}
      ${renderMetricCard("Ativos", formatNumber(activeMembers), "Pessoas com acesso liberado.", "default", "check")}
      ${renderMetricCard("Seu papel", TEAM_ROLE_LABELS[requesterRole] || requesterRole, "Permissão aplicada nesta empresa.", "warm", "handoff")}
    </section>

    <section class="grid-two team-layout">
      <article class="card">
        <div class="card-header">
          <div>
            <h3 class="card-title">Convidar colaborador</h3>
            <p class="card-copy">O convite é enviado por email e só pode ser usado uma vez.</p>
          </div>
        </div>
        <form id="team-invitation-form" class="form-grid">
          <div class="field">
            <label>Email</label>
            <input name="email" type="email" required autocomplete="email" placeholder="pessoa@empresa.com" />
          </div>
          <div class="field">
            <label>Papel inicial</label>
            <select name="role" required>
              <option value="operator">Operador — atua na operação</option>
              <option value="viewer">Visualizador — somente leitura</option>
              ${requesterRole === "owner" ? '<option value="admin">Administrador — gerencia operação e equipe</option>' : ""}
            </select>
            <small>O papel de proprietário só pode ser concedido depois que a pessoa aceitar o convite.</small>
          </div>
          <div class="toolbar-row">
            <button class="button" type="submit">Enviar convite</button>
          </div>
        </form>
        ${
          invitationUrl
            ? `
              <div class="message-card team-development-token">
                <strong>Link de desenvolvimento</strong>
                <p>O provedor de email está desativado neste ambiente. Copie o link abaixo para concluir o teste.</p>
                <div class="inline-actions">
                  <input id="team-invitation-link" value="${escapeHtml(invitationUrl)}" readonly aria-label="Link do convite" />
                  <button class="button-ghost" type="button" id="copy-team-invitation">Copiar</button>
                </div>
              </div>
            `
            : ""
        }
      </article>

      <article class="card">
        <div class="card-header">
          <div>
            <h3 class="card-title">Política de acesso</h3>
            <p class="card-copy">Privilégio mínimo por papel, isolado por empresa.</p>
          </div>
        </div>
        <div class="team-role-guide">
          <p><strong>Proprietário</strong><span>Controle total, inclusive promoção de outros proprietários.</span></p>
          <p><strong>Administrador</strong><span>Gerencia operação e membros não administrativos.</span></p>
          <p><strong>Operador</strong><span>Executa rotinas de operação permitidas.</span></p>
          <p><strong>Visualizador</strong><span>Acesso de leitura aos dados autorizados.</span></p>
        </div>
      </article>
    </section>

    <section class="card">
      <div class="card-header">
        <div>
          <h3 class="card-title">Membros da empresa</h3>
          <p class="card-copy">A plataforma impede a remoção do último proprietário ativo.</p>
        </div>
      </div>
      <div class="team-member-list">
        ${
          state.teamMembers.length
            ? state.teamMembers
                .map((member) => {
                  const isSelf = Number(member.user_id) === Number(state.session?.user_id)
                  const canEditMember = requesterRole === "owner" || !["owner", "admin"].includes(member.role)
                  return `
                    <form class="team-member-row" data-team-member-form="${member.id}">
                      <div class="contact-avatar">${escapeHtml((member.display_name || member.email || "?").slice(0, 2).toUpperCase())}</div>
                      <div class="team-member-identity">
                        <strong>${escapeHtml(member.display_name || member.email)}${isSelf ? " (você)" : ""}</strong>
                        <span>${escapeHtml(member.email)}</span>
                        <small>Desde ${escapeHtml(formatDateTime(member.created_at))}</small>
                      </div>
                      ${
                        canEditMember
                          ? `
                            <div class="field">
                              <label>Papel</label>
                              <select name="role">${renderTeamRoleOptions(member.role, requesterRole)}</select>
                            </div>
                            <label class="team-active-toggle">
                              <input name="active" type="checkbox" ${member.active ? "checked" : ""} />
                              <span>Acesso ativo</span>
                            </label>
                            <button class="button-ghost" type="submit">Salvar</button>
                          `
                          : `
                            <span class="tag">${escapeHtml(TEAM_ROLE_LABELS[member.role] || member.role)}</span>
                            <span class="tag ${member.active ? "tag-success" : "tag-danger"}">${member.active ? "Ativo" : "Inativo"}</span>
                            <span class="team-member-locked">Gerenciado por proprietário</span>
                          `
                      }
                    </form>
                  `
                })
                .join("")
            : renderEmptyState("contacts", "Nenhum membro encontrado", "Convide a primeira pessoa para colaborar com este workspace.")
        }
      </div>
    </section>
  `
}

function renderCurrentView() {
  if (state.view === "dashboard") return renderDashboardView()
  if (state.view === "analytics") return renderAnalyticsView()
  if (state.view === "studio") return renderStudioView()
  if (state.view === "flows") return renderFlowsView()
  if (state.view === "agenda") return renderAgendaView()
  if (state.view === "contacts") return renderContactsView()
  if (state.view === "training") return renderTrainingView()
  if (state.view === "team") return renderTeamView()
  if (state.view === "billing") return renderBillingView()
  if (state.view === "onboarding") return renderOnboardingView()
  return ""
}

function renderSettingsModal() {
  if (state.authAction.invitationToken) {
    return `
      <div class="modal-backdrop">
        <div class="modal">
          <div class="section-header">
            <div>
              <h3 class="section-title">Aceitar convite</h3>
              <p class="section-copy">Defina seu nome e uma senha forte para acessar a empresa.</p>
            </div>
          </div>
          <form id="invitation-accept-form" class="form-grid">
            <div class="field">
              <label>Nome</label>
              <input name="displayName" required autocomplete="name" />
            </div>
            <div class="field">
              <label>Senha</label>
              <input name="password" type="password" required minlength="12" autocomplete="new-password" />
            </div>
            <div class="field">
              <label>Confirmar senha</label>
              <input name="passwordConfirm" type="password" required minlength="12" autocomplete="new-password" />
            </div>
            <div class="toolbar-row">
              <button class="button" type="submit">Aceitar e entrar</button>
            </div>
          </form>
        </div>
      </div>
    `
  }

  if (state.authAction.passwordResetToken) {
    return `
      <div class="modal-backdrop">
        <div class="modal">
          <div class="section-header">
            <div>
              <h3 class="section-title">Criar nova senha</h3>
              <p class="section-copy">Use pelo menos 12 caracteres e combine letras, números e símbolos.</p>
            </div>
          </div>
          <form id="password-reset-confirm-form" class="form-grid">
            <div class="field">
              <label>Nova senha</label>
              <input name="password" type="password" required minlength="12" autocomplete="new-password" />
            </div>
            <div class="field">
              <label>Confirmar senha</label>
              <input name="passwordConfirm" type="password" required minlength="12" autocomplete="new-password" />
            </div>
            <div class="toolbar-row">
              <button class="button" type="submit">Atualizar senha</button>
            </div>
          </form>
        </div>
      </div>
    `
  }

  if (!state.session && state.authMode === "register") {
    return `
      <div class="modal-backdrop">
        <div class="modal">
          <div class="section-header">
            <div>
              <h3 class="section-title">Criar conta</h3>
              <p class="section-copy">Crie o primeiro usuário proprietário e o workspace da empresa.</p>
            </div>
          </div>
          <form id="registration-form" class="form-grid">
            <div class="field">
              <label>Seu nome</label>
              <input name="displayName" required autocomplete="name" />
            </div>
            <div class="field">
              <label>Email</label>
              <input name="email" type="email" required autocomplete="email" />
            </div>
            <div class="field">
              <label>Nome da empresa</label>
              <input name="companyName" required autocomplete="organization" />
            </div>
            <div class="field">
              <label>Slug da empresa</label>
              <input name="companySlug" required pattern="[a-z0-9][a-z0-9-]{1,61}[a-z0-9]" placeholder="minha-empresa" />
            </div>
            <div class="field">
              <label>Senha</label>
              <input name="password" type="password" required minlength="12" autocomplete="new-password" />
            </div>
            <div class="toolbar-row">
              <button class="button" type="submit">Criar workspace</button>
              <button class="button-ghost" type="button" id="show-login">Já tenho conta</button>
            </div>
          </form>
        </div>
      </div>
    `
  }

  if (state.session) {
    return `
      <div class="modal-backdrop">
        <div class="modal">
          <div class="section-header">
            <div>
              <h3 class="section-title">Sessão ativa</h3>
              <p class="section-copy">Seu acesso está protegido por cookie HTTP-only e pode ser encerrado a qualquer momento.</p>
            </div>
            <button class="button-ghost" id="close-settings">Fechar</button>
          </div>
          <div class="message-card">
            <strong>${escapeHtml(state.session.email || state.session.actor)}</strong>
            <p>Papel: ${escapeHtml(state.session.role)} · Expira em: ${escapeHtml(formatDateTime(state.session.expires_at * 1000))}</p>
          </div>
          <div class="toolbar-row">
            <button class="button-danger" type="button" id="logout-session-modal">Encerrar sessão</button>
          </div>
        </div>
      </div>
    `
  }

  return `
    <div class="modal-backdrop">
      <div class="modal">
        <div class="section-header">
          <div>
            <h3 class="section-title">Entrar na plataforma</h3>
            <p class="section-copy">Use o email e a senha da sua conta. O acesso administrativo legado fica isolado nas opções avançadas.</p>
          </div>
          <button class="button-ghost" id="close-settings">Fechar</button>
        </div>
        <form id="settings-form" class="form-grid">
          <div class="field">
            <label>Email</label>
            <input name="email" type="email" autocomplete="email" value="${escapeHtml(state.drafts.auth.email)}" placeholder="voce@empresa.com" />
          </div>
          <div class="field">
            <label>Senha</label>
            <input name="password" type="password" autocomplete="current-password" value="" placeholder="Acesso SaaS por usuário" />
          </div>
          <details class="auth-advanced">
            <summary>Acesso administrativo de emergência</summary>
            <div class="form-grid">
              <div class="field">
                <label>Admin token</label>
                <input name="adminToken" type="password" value="${escapeHtml(state.drafts.auth.adminToken)}" placeholder="Credencial break-glass / legado" />
              </div>
              <div class="field">
                <label>Admin actor</label>
                <input name="adminActor" value="${escapeHtml(state.drafts.auth.adminActor || state.config.adminActor)}" />
              </div>
              <div class="field">
                <label>Company slug preferido</label>
                <input name="companySlug" value="${escapeHtml(state.config.companySlug)}" placeholder="Ex.: empresa-x" />
              </div>
            </div>
          </details>
          ${
            state.errorMessage
              ? `<div class="message-card"><strong>Último erro</strong><p>${escapeHtml(state.errorMessage)}</p></div>`
              : ""
          }
          <div class="toolbar-row">
            <button class="button" type="submit">Entrar</button>
            ${state.authCapabilities.password_reset ? '<button class="button-ghost" type="button" id="request-password-reset">Esqueci a senha</button>' : ""}
            ${state.authCapabilities.self_service_signup ? '<button class="button-ghost" type="button" id="show-registration">Criar conta</button>' : ""}
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
  if (!state.loading) return ""
  return `
    <div class="loading-banner">
      <span class="loader"></span>
      <strong>${escapeHtml(state.loadingMessage || "Carregando")}</strong>
    </div>
  `
}

function renderSkeleton(lines = 3, withHeader = true) {
  return `
    <div class="skeleton-group">
      ${withHeader ? '<div class="skeleton-line skeleton-header"></div>' : ''}
      ${Array.from({ length: lines }, (_, i) => `<div class="skeleton-line" style="width:${85 - i * 12}%"></div>`).join("")}
    </div>
  `
}

function renderSkeletonCards(count = 3) {
  return `
    <section class="grid-${count <= 2 ? 'two' : 'three'}">
      ${Array.from({ length: count }, () => `
        <article class="card">
          ${renderSkeleton(2)}
        </article>
      `).join("")}
    </section>
  `
}

function renderEmptyState(icon, title, description, ctaLabel, ctaAction) {
  return `
    <div class="empty-state-box">
      <span class="empty-state-icon">${ICONS[icon] || ICONS.search}</span>
      <h4 class="empty-state-title">${escapeHtml(title)}</h4>
      <p class="empty-state-description">${escapeHtml(description)}</p>
      ${ctaLabel ? `<button class="button" ${ctaAction ? `data-jump-view="${escapeHtml(ctaAction)}"` : ''}>${escapeHtml(ctaLabel)}</button>` : ''}
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
    ${state.manualBookingOpen ? renderManualBookingModal() : ""}
    ${renderToasts()}
    ${renderLoadingBanner()}
  `
  bindEvents()
}

function bindEvents() {
  // Analytics period buttons (re-bound each render because DOM is replaced)
  document.querySelectorAll("[data-analytics-period]").forEach((button) => {
    button.addEventListener("click", async () => {
      state.analyticsPeriod = button.dataset.analyticsPeriod
      state.analyticsDashboard = null
      renderApp()
      await refreshCurrentView()
    })
  })

  // ── Onboarding wizard events ──
  document.querySelectorAll("[data-onboarding-type]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.onboardingWizard.draft.business_type_key = btn.dataset.onboardingType
      renderApp()
    })
  })

  document.querySelectorAll("[data-onboarding-field]").forEach((input) => {
    input.addEventListener("input", () => {
      const key = input.name
      if (key === "slot_duration_minutes" || key === "minimum_notice_minutes") {
        state.onboardingWizard.draft[key] = parseInt(input.value) || 0
      } else {
        state.onboardingWizard.draft[key] = input.value
      }
    })
  })

  document.querySelectorAll("[data-service-field]").forEach((input) => {
    input.addEventListener("input", () => {
      const idx = parseInt(input.dataset.serviceField)
      const svc = state.onboardingWizard.draft.services[idx]
      if (!svc) return
      if (input.name === "svc_name") svc.name = input.value
      else if (input.name === "svc_duration") svc.duration_minutes = parseInt(input.value) || null
      else if (input.name === "svc_price") svc.price = parseFloat(input.value) || null
    })
  })

  document.querySelectorAll("[data-remove-service]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const idx = parseInt(btn.dataset.removeService)
      state.onboardingWizard.draft.services.splice(idx, 1)
      renderApp()
    })
  })

  const addService = document.getElementById("add-onboarding-service")
  if (addService) {
    addService.addEventListener("click", () => {
      state.onboardingWizard.draft.services.push({ name: "", duration_minutes: null, price: null })
      renderApp()
    })
  }

  const wizardPrev = document.getElementById("wizard-prev")
  if (wizardPrev) {
    wizardPrev.addEventListener("click", () => {
      if (state.onboardingWizard.step > 0) {
        state.onboardingWizard.step--
        renderApp()
      }
    })
  }

  const wizardNext = document.getElementById("wizard-next")
  if (wizardNext) {
    wizardNext.addEventListener("click", () => {
      const d = state.onboardingWizard.draft
      if (state.onboardingWizard.step === 0) {
        if (!d.company_name.trim()) { pushToast("Informe o nome da empresa.", "error"); return }
        if (!d.business_type_key) { pushToast("Selecione o tipo de negócio.", "error"); return }
      }
      if (state.onboardingWizard.step < ONBOARDING_STEPS.length - 1) {
        state.onboardingWizard.step++
        renderApp()
      }
    })
  }

  const wizardSubmit = document.getElementById("wizard-submit")
  if (wizardSubmit) {
    wizardSubmit.addEventListener("click", async () => {
      const d = state.onboardingWizard.draft
      if (!d.company_name.trim()) { pushToast("Informe o nome da empresa.", "error"); return }
      if (!d.business_type_key) { pushToast("Selecione o tipo de negócio.", "error"); return }

      state.loading = true
      state.loadingMessage = "Configurando empresa..."
      renderApp()

      try {
        const offsets = (d.reminder_offsets_days || "2,1")
          .split(",")
          .map(s => parseInt(s.trim()))
          .filter(n => !isNaN(n) && n > 0)

        const rules = (d.custom_rules || "")
          .split("\n")
          .map(s => s.trim())
          .filter(s => s.length > 0)

        const services = d.services.filter(s => s.name && s.name.trim())

        const payload = {
          company_name: d.company_name.trim(),
          business_type_key: d.business_type_key,
          segment: d.segment || undefined,
          company_description: d.company_description || undefined,
          phone: d.phone || undefined,
          email: d.email || undefined,
          instagram: d.instagram || undefined,
          website: d.website || undefined,
          timezone: d.timezone,
          weekday_hours: d.weekday_hours,
          saturday_hours: d.saturday_hours,
          sunday_hours: d.sunday_hours,
          slot_duration_minutes: d.slot_duration_minutes,
          minimum_notice_minutes: d.minimum_notice_minutes,
          reminder_offsets_days: offsets.length > 0 ? offsets : undefined,
          brand_style: d.brand_style || undefined,
          custom_rules: rules.length > 0 ? rules : undefined,
          services: services.length > 0 ? services.map(s => ({
            name: s.name.trim(),
            duration_minutes: s.duration_minutes || undefined,
            price: s.price || undefined,
          })) : undefined,
        }

        const result = await apiRequest("/admin/onboarding/setup", {
          method: "POST",
          body: payload,
        })
        state.onboardingWizard.result = result
        state.onboardingWizard.completed = true
        pushToast("Empresa configurada com sucesso!")
        await loadCompanies()
      } catch (error) {
        pushToast(summarizeError(error), "error")
      } finally {
        state.loading = false
        state.loadingMessage = ""
        renderApp()
      }
    })
  }

  const onboardingReset = document.getElementById("onboarding-reset")
  if (onboardingReset) {
    onboardingReset.addEventListener("click", () => {
      state.onboardingWizard = {
        step: 0,
        completed: false,
        result: null,
        draft: {
          company_name: "",
          business_type_key: "",
          segment: "",
          company_description: "",
          phone: "",
          email: "",
          instagram: "",
          website: "",
          timezone: "America/Sao_Paulo",
          weekday_hours: "08:00-18:00",
          saturday_hours: "09:00-12:00",
          sunday_hours: "fechado",
          slot_duration_minutes: 60,
          minimum_notice_minutes: 120,
          reminder_offsets_days: "2,1",
          brand_style: "",
          custom_rules: "",
          services: [],
        },
      }
      renderApp()
    })
  }

  // ── Billing bindings ──
  const billingCycleSelect = document.getElementById("billing-cycle-select")
  if (billingCycleSelect) {
    billingCycleSelect.addEventListener("change", () => {
      state.billingCycle = billingCycleSelect.value === "annual" ? "annual" : "monthly"
      renderApp()
    })
  }

  document.querySelectorAll(".billing-portal-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const companyId = state.config.companyId
      if (!companyId) {
        pushToast("Selecione uma empresa antes de gerenciar a assinatura.", "error")
        return
      }
      state.loading = true
      state.loadingMessage = "Abrindo portal de cobrança..."
      renderApp()
      try {
        const result = await apiRequest("/admin/billing/portal", {
          method: "POST",
          params: { company_id: companyId },
          headers: { "X-Idempotency-Key": window.crypto.randomUUID() },
        })
        window.location.assign(result.portal_url)
      } catch (error) {
        pushToast(summarizeError(error), "error")
        state.loading = false
        state.loadingMessage = ""
        renderApp()
      }
    })
  })

  document.querySelectorAll(".billing-checkout-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const planKey = btn.dataset.planKey
      const companyId = state.config.companyId
      if (!companyId) {
        pushToast("Selecione uma empresa antes de escolher um plano.", "error")
        return
      }
      const hasSub = state.billingUsage?.has_subscription
      const subscription = state.billingUsage?.subscription || {}
      const managedInPortal = (
        hasSub
        && subscription.gateway === "stripe"
        && !["incomplete", "incomplete_expired", "cancelled"].includes(subscription.status)
      )
      if (!confirm(`Deseja continuar com o plano ${planKey}?`)) return
      state.loading = true
      state.loadingMessage = managedInPortal
        ? "Abrindo portal de cobrança..."
        : "Criando checkout seguro..."
      renderApp()
      try {
        if (managedInPortal) {
          const portal = await apiRequest("/admin/billing/portal", {
            method: "POST",
            params: { company_id: companyId },
            headers: { "X-Idempotency-Key": window.crypto.randomUUID() },
          })
          window.location.assign(portal.portal_url)
        } else {
          const checkout = await apiRequest("/admin/billing/checkout", {
            method: "POST",
            params: { company_id: companyId },
            body: {
              plan_key: planKey,
              billing_cycle: state.billingCycle,
            },
            headers: { "X-Idempotency-Key": window.crypto.randomUUID() },
          })
          window.location.assign(checkout.checkout_url)
        }
      } catch (error) {
        pushToast(summarizeError(error), "error")
        state.loading = false
        state.loadingMessage = ""
        renderApp()
      }
    })
  })

  const teamOpenSession = document.getElementById("team-open-session")
  if (teamOpenSession) {
    teamOpenSession.addEventListener("click", async () => {
      state.loading = true
      state.loadingMessage = "Preparando acesso individual..."
      renderApp()
      try {
        await logoutPlatformSession()
        state.settingsOpen = true
      } catch (error) {
        pushToast(summarizeError(error), "error")
      } finally {
        state.loading = false
        state.loadingMessage = ""
        renderApp()
      }
    })
  }

  const teamInvitationForm = document.getElementById("team-invitation-form")
  if (teamInvitationForm) {
    teamInvitationForm.addEventListener("submit", async (event) => {
      event.preventDefault()
      const formData = new FormData(teamInvitationForm)
      state.loading = true
      state.loadingMessage = "Criando e enviando convite..."
      renderApp()
      try {
        const invitation = await apiRequest("/platform/identity/invitations", {
          method: "POST",
          admin: false,
          body: {
            company_id: Number(state.config.companyId),
            email: String(formData.get("email") || "").trim(),
            role: String(formData.get("role") || "operator"),
          },
        })
        state.teamInvitation = invitation
        pushToast(
          invitation.delivery_status === "delivered"
            ? `Convite enviado para ${invitation.email}.`
            : "Convite criado com link de desenvolvimento."
        )
      } catch (error) {
        pushToast(summarizeError(error), "error")
      } finally {
        state.loading = false
        state.loadingMessage = ""
        renderApp()
      }
    })
  }

  const copyTeamInvitation = document.getElementById("copy-team-invitation")
  if (copyTeamInvitation) {
    copyTeamInvitation.addEventListener("click", async () => {
      const link = document.getElementById("team-invitation-link")?.value
      if (!link) return
      try {
        await navigator.clipboard.writeText(link)
        pushToast("Link do convite copiado.")
      } catch {
        pushToast("Não foi possível copiar automaticamente. Selecione o link manualmente.", "error")
      }
    })
  }

  document.querySelectorAll("[data-team-member-form]").forEach((form) => {
    form.addEventListener("submit", async (event) => {
      event.preventDefault()
      const membershipId = Number(form.dataset.teamMemberForm)
      const formData = new FormData(form)
      state.loading = true
      state.loadingMessage = "Atualizando permissão..."
      renderApp()
      try {
        const updated = await apiRequest(`/platform/identity/members/${membershipId}`, {
          method: "PATCH",
          admin: false,
          params: { company_id: state.config.companyId },
          body: {
            role: String(formData.get("role") || ""),
            active: formData.get("active") === "on",
          },
        })
        await fetchPlatformSession()
        await loadTeamData()
        pushToast(`Acesso de ${updated.display_name || updated.email} atualizado.`)
      } catch (error) {
        pushToast(summarizeError(error), "error")
      } finally {
        state.loading = false
        state.loadingMessage = ""
        renderApp()
      }
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

  const showRegistration = document.getElementById("show-registration")
  if (showRegistration) {
    showRegistration.addEventListener("click", () => {
      state.authMode = "register"
      renderApp()
    })
  }

  const showLogin = document.getElementById("show-login")
  if (showLogin) {
    showLogin.addEventListener("click", () => {
      state.authMode = "login"
      renderApp()
    })
  }

  const invitationAcceptForm = document.getElementById("invitation-accept-form")
  if (invitationAcceptForm) {
    invitationAcceptForm.addEventListener("submit", async (event) => {
      event.preventDefault()
      const formData = new FormData(invitationAcceptForm)
      const password = String(formData.get("password") || "")
      if (password !== String(formData.get("passwordConfirm") || "")) {
        pushToast("As senhas não conferem.", "error")
        return
      }
      state.loading = true
      state.loadingMessage = "Aceitando convite..."
      renderApp()
      try {
        const session = await apiRequest("/platform/auth/invitations/accept", {
          method: "POST",
          admin: false,
          body: {
            token: state.authAction.invitationToken,
            password,
            display_name: String(formData.get("displayName") || "").trim(),
          },
        })
        state.session = session
        state.connected = true
        state.settingsOpen = false
        const membership = session.memberships?.[0]
        if (membership) {
          state.config.companyId = membership.company_id
          state.config.companySlug = membership.company_slug
        }
        clearAuthActionFromUrl()
        persistConfig()
        connectSSE()
        await refreshCurrentView()
        pushToast("Convite aceito com sucesso.")
      } catch (error) {
        pushToast(summarizeError(error), "error")
      } finally {
        state.loading = false
        state.loadingMessage = ""
        renderApp()
      }
    })
  }

  const passwordResetConfirmForm = document.getElementById("password-reset-confirm-form")
  if (passwordResetConfirmForm) {
    passwordResetConfirmForm.addEventListener("submit", async (event) => {
      event.preventDefault()
      const formData = new FormData(passwordResetConfirmForm)
      const password = String(formData.get("password") || "")
      if (password !== String(formData.get("passwordConfirm") || "")) {
        pushToast("As senhas não conferem.", "error")
        return
      }
      state.loading = true
      state.loadingMessage = "Atualizando senha..."
      renderApp()
      try {
        await apiRequest("/platform/auth/password-reset/confirm", {
          method: "POST",
          admin: false,
          body: {
            token: state.authAction.passwordResetToken,
            password,
          },
        })
        clearAuthActionFromUrl()
        state.authMode = "login"
        state.settingsOpen = true
        pushToast("Senha atualizada. Entre novamente.")
      } catch (error) {
        pushToast(summarizeError(error), "error")
      } finally {
        state.loading = false
        state.loadingMessage = ""
        renderApp()
      }
    })
  }

  const registrationForm = document.getElementById("registration-form")
  if (registrationForm) {
    registrationForm.addEventListener("submit", async (event) => {
      event.preventDefault()
      const formData = new FormData(registrationForm)
      state.loading = true
      state.loadingMessage = "Criando workspace..."
      renderApp()
      try {
        const session = await apiRequest("/platform/auth/register", {
          method: "POST",
          admin: false,
          body: {
            email: String(formData.get("email") || "").trim(),
            password: String(formData.get("password") || ""),
            display_name: String(formData.get("displayName") || "").trim(),
            company_name: String(formData.get("companyName") || "").trim(),
            company_slug: String(formData.get("companySlug") || "").trim().toLowerCase(),
          },
        })
        state.session = session
        state.connected = true
        state.settingsOpen = false
        state.authMode = "login"
        const membership = session.memberships?.[0]
        if (membership) {
          state.config.companyId = membership.company_id
          state.config.companySlug = membership.company_slug
        }
        persistConfig()
        connectSSE()
        await refreshCurrentView()
        pushToast("Workspace criado com sucesso.")
      } catch (error) {
        pushToast(summarizeError(error), "error")
      } finally {
        state.loading = false
        state.loadingMessage = ""
        renderApp()
      }
    })
  }

  const requestPasswordReset = document.getElementById("request-password-reset")
  if (requestPasswordReset) {
    requestPasswordReset.addEventListener("click", async () => {
      const emailInput = document.querySelector('#settings-form input[name="email"]')
      const email = String(emailInput?.value || "").trim()
      if (!email) {
        pushToast("Informe seu email primeiro.", "error")
        return
      }
      try {
        await apiRequest("/platform/auth/password-reset/request", {
          method: "POST",
          admin: false,
          body: { email },
        })
        pushToast("Se a conta existir, enviaremos um link de redefinição.")
      } catch (error) {
        pushToast(summarizeError(error), "error")
      }
    })
  }

  const settingsForm = document.getElementById("settings-form")
  if (settingsForm) {
    settingsForm.addEventListener("submit", async (event) => {
      event.preventDefault()
      const formData = new FormData(settingsForm)
      state.drafts.auth.adminToken = String(formData.get("adminToken") || "").trim()
      state.drafts.auth.adminActor = String(formData.get("adminActor") || "").trim()
      state.drafts.auth.email = String(formData.get("email") || "").trim()
      state.drafts.auth.password = String(formData.get("password") || "")
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
        connectSSE()
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
  const messageField = document.getElementById("studio-message")
  if (messageField) {
    messageField.addEventListener("input", (event) => {
      state.studio.draftMessage = event.target.value
    })
  }

  const chatLog = document.getElementById("studio-chat-log")
  if (chatLog) {
    chatLog.scrollTop = chatLog.scrollHeight
  }

  const studioForm = document.getElementById("studio-form")
  if (studioForm) {
    studioForm.addEventListener("submit", async (event) => {
      event.preventDefault()
      const session = getActiveStudioSession()
      if (!session) {
        pushToast("Selecione uma sessão antes de enviar mensagens.", "error")
        return
      }
      const message = String(state.studio.draftMessage || "").trim()
      if (!message) {
        pushToast("Digite uma mensagem antes de enviar.", "error")
        return
      }

      const outgoing = { role: "user", text: message, at: new Date().toISOString() }
      session.messages.push(outgoing)
      session.updated_at = outgoing.at
      state.studio.draftMessage = ""
      persistStudioSessions()

      state.loading = true
      state.loadingMessage = "Enviando mensagem para o runtime..."
      renderApp()
      try {
        const result = await apiRequest("/admin/simulator/messages", {
          method: "POST",
          params: getCompanyParams(),
          body: {
            contact_id: session.contact_id,
            contact_name: session.contact_name || null,
            message,
          },
        })
        const incoming = {
          role: "assistant",
          text: result.reply || "(sem resposta)",
          at: new Date().toISOString(),
          meta: {
            classification: result.classification,
            action: result.action,
            pipeline_stage: result.pipeline_stage,
            source: result.source,
            next_action: result.next_action,
            lead_temperature: result.lead_temperature,
          },
        }
        session.messages.push(incoming)
        session.updated_at = incoming.at
        session.last_result = {
          action: result.action,
          classification: result.classification,
          pipeline_stage: result.pipeline_stage,
        }
        persistStudioSessions()
        pushToast("Mensagem processada pelo runtime real.")
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
      state.studio.draftMessage = button.dataset.studioPrompt || ""
      renderApp()
    })
  })

  document.querySelectorAll("[data-studio-session]").forEach((element) => {
    element.addEventListener("click", (event) => {
      if (event.target.closest("[data-studio-delete-session]")) return
      const id = element.dataset.studioSession
      state.studio.activeSessionId = id
      state.studio.draftMessage = ""
      state.studio.showNewForm = false
      renderApp()
    })
  })

  document.querySelectorAll("[data-studio-delete-session]").forEach((element) => {
    element.addEventListener("click", (event) => {
      event.preventDefault()
      event.stopPropagation()
      const id = element.dataset.studioDeleteSession
      if (!id) return
      if (!window.confirm("Remover esta sessão de teste?")) return
      const key = studioCompanyKey()
      state.studio.sessionsByCompany[key] = (state.studio.sessionsByCompany[key] || []).filter((s) => s.id !== id)
      if (state.studio.activeSessionId === id) {
        state.studio.activeSessionId = null
      }
      persistStudioSessions()
      renderApp()
    })
  })

  const startNewBtn = document.getElementById("studio-start-new")
  if (startNewBtn) {
    startNewBtn.addEventListener("click", () => {
      state.studio.showNewForm = true
      renderApp()
    })
  }

  const cancelNewBtn = document.getElementById("studio-cancel-new")
  if (cancelNewBtn) {
    cancelNewBtn.addEventListener("click", () => {
      state.studio.showNewForm = false
      renderApp()
    })
  }

  const newSessionForm = document.getElementById("studio-new-session-form")
  if (newSessionForm) {
    const labelInput = newSessionForm.querySelector("#studio-new-label")
    const contactIdInput = newSessionForm.querySelector("#studio-new-contact-id")
    const contactNameInput = newSessionForm.querySelector("#studio-new-contact-name")
    if (labelInput) labelInput.addEventListener("input", (e) => { state.studio.newSession.label = e.target.value })
    if (contactIdInput) contactIdInput.addEventListener("input", (e) => { state.studio.newSession.contactId = e.target.value })
    if (contactNameInput) contactNameInput.addEventListener("input", (e) => { state.studio.newSession.contactName = e.target.value })

    newSessionForm.addEventListener("submit", (event) => {
      event.preventDefault()
      const label = String(state.studio.newSession.label || "").trim()
      const contactId = String(state.studio.newSession.contactId || "").trim()
      const contactName = String(state.studio.newSession.contactName || "").trim()
      if (!contactId) {
        pushToast("Informe o contact_id para criar a sessão.", "error")
        return
      }
      const now = new Date().toISOString()
      const session = {
        id: window.crypto.randomUUID(),
        label: label || contactId,
        contact_id: contactId,
        contact_name: contactName,
        created_at: now,
        updated_at: now,
        messages: [],
        last_result: null,
      }
      const sessions = getStudioSessions()
      sessions.push(session)
      state.studio.activeSessionId = session.id
      state.studio.newSession = { label: "", contactId: "", contactName: "" }
      state.studio.showNewForm = false
      state.studio.draftMessage = ""
      persistStudioSessions()
      pushToast("Sessão criada.")
      renderApp()
    })
  }

  const useOperatorBtn = document.getElementById("studio-use-operator")
  if (useOperatorBtn) {
    useOperatorBtn.addEventListener("click", () => {
      const operatorId = state.companyContext?.internal_operation?.operator_contact_ids?.[0]
      if (operatorId) {
        state.studio.newSession.contactId = operatorId
        state.studio.newSession.contactName = "Operador interno"
        if (!state.studio.newSession.label) state.studio.newSession.label = "Operador interno"
        renderApp()
      }
    })
  }

  const clearStudioLog = document.getElementById("clear-studio-log")
  if (clearStudioLog) {
    clearStudioLog.addEventListener("click", () => {
      const session = getActiveStudioSession()
      if (!session) return
      if (!window.confirm("Limpar o histórico desta sessão?")) return
      session.messages = []
      session.last_result = null
      session.updated_at = new Date().toISOString()
      persistStudioSessions()
      renderApp()
    })
  }
}

function bindFlowEvents() {
  const addPipelineStage = document.getElementById("add-pipeline-stage")
  const pushStageFromTemplate = (defaults = {}) => {
    const context = ensureConversationPipelines(state.companyContext || {})
    const existing = new Set(context.conversation_pipelines.map((p) => p.key))
    const baseKey = (defaults.label || "stage").toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "") || "stage"
    let candidate = baseKey
    let suffix = 1
    while (existing.has(candidate)) {
      suffix += 1
      candidate = `${baseKey}_${suffix}`
    }
    const nextIndex = context.conversation_pipelines.length + 1
    context.conversation_pipelines.push({
      key: candidate,
      label: defaults.label || "Nova etapa",
      objective: defaults.objective || "",
      trigger_terms: defaults.trigger_terms || [],
      success_criteria: defaults.success_criteria || "",
      handoff_when: defaults.handoff_when || "",
      preferred_cta: defaults.preferred_cta || "",
      next_keys: [],
      canvas_position: {
        x: 140 + ((nextIndex - 1) % 3) * 320,
        y: 120 + Math.floor((nextIndex - 1) / 3) * 240,
      },
    })
    state.companyContext = context
    state.flowEditor.selectedPipelineKey = candidate
    renderApp()
  }

  if (addPipelineStage) {
    addPipelineStage.addEventListener("click", () => pushStageFromTemplate({}))
  }

  document.querySelectorAll("[data-flow-template]").forEach((button) => {
    button.addEventListener("click", () => {
      const [category, idxStr] = String(button.dataset.flowTemplate || "").split(":")
      const group = FLOW_NODE_TEMPLATES.find((g) => g.category === category)
      const item = group?.items?.[Number(idxStr)]
      if (!item) return
      pushStageFromTemplate(item.defaults || {})
      pushToast(`Etapa "${item.label}" adicionada ao canvas.`)
    })
  })

  document.querySelectorAll("[data-flow-zoom]").forEach((button) => {
    button.addEventListener("click", () => {
      const action = button.dataset.flowZoom
      const current = state.flowEditor.zoom || 1
      if (action === "in") state.flowEditor.zoom = Math.min(1.6, Math.round((current + 0.1) * 10) / 10)
      else if (action === "out") state.flowEditor.zoom = Math.max(0.4, Math.round((current - 0.1) * 10) / 10)
      else if (action === "fit") state.flowEditor.zoom = 0.7
      renderApp()
    })
  })

  const computeEdgeD = (source, target) => {
    const startX = Number(source.canvas_position?.x || 0) + 260
    const startY = Number(source.canvas_position?.y || 0) + 70
    const endX = Number(target.canvas_position?.x || 0)
    const endY = Number(target.canvas_position?.y || 0) + 70
    const midX = startX + (endX - startX) / 2
    return `M ${startX} ${startY} C ${midX} ${startY}, ${midX} ${endY}, ${endX} ${endY}`
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
      let rafPending = false

      const pipelineLookup = new Map((context.conversation_pipelines || []).map((p) => [p.key, p]))
      const incidentEdges = Array.from(
        document.querySelectorAll(
          `.flow-edge-path[data-edge-source="${CSS.escape(pipelineKey)}"], .flow-edge-path[data-edge-target="${CSS.escape(pipelineKey)}"]`
        )
      ).map((path) => ({
        path,
        source: pipelineLookup.get(path.dataset.edgeSource),
        target: pipelineLookup.get(path.dataset.edgeTarget),
      })).filter((e) => e.source && e.target)

      try {
        button.setPointerCapture(event.pointerId)
      } catch (_) {}
      event.preventDefault()

      const zoom = state.flowEditor.zoom || 1
      const flushFrame = () => {
        rafPending = false
        button.style.left = `${currentX}px`
        button.style.top = `${currentY}px`
        for (const edge of incidentEdges) {
          edge.path.setAttribute("d", computeEdgeD(edge.source, edge.target))
        }
      }
      const onMove = (moveEvent) => {
        const dx = (moveEvent.clientX - startX) / zoom
        const dy = (moveEvent.clientY - startY) / zoom
        if (!dragging && Math.hypot(dx, dy) < 4 / zoom) return
        if (!dragging) {
          dragging = true
          button.classList.add("dragging")
        }
        currentX = Math.max(20, originX + dx)
        currentY = Math.max(20, originY + dy)
        pipeline.canvas_position = { x: currentX, y: currentY }
        if (!rafPending) {
          rafPending = true
          requestAnimationFrame(flushFrame)
        }
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

function bindSchedulingPolicyEditorEvents() {
  const toggle = document.getElementById("scheduling-policy-enabled")
  if (toggle) {
    toggle.addEventListener("change", () => {
      const context = ensureSchedulingPolicy(state.companyContext || {})
      context.scheduling_policy.enabled = !!toggle.checked
      state.companyContext = context
      renderApp()
    })
  }

  const saveButton = document.getElementById("save-scheduling-policy")
  if (saveButton) {
    saveButton.addEventListener("click", async () => {
      const context = ensureSchedulingPolicy(state.companyContext || {})
      state.loading = true
      state.loadingMessage = "Salvando política de agendamento..."
      renderApp()
      try {
        const saved = await apiRequest("/admin/company-context", {
          method: "PUT",
          params: getCompanyContextParams(),
          body: { data: context },
        })
        state.companyContext = ensureSchedulingPolicy(saved || context)
        pushToast("Política de agendamento salva.")
      } catch (error) {
        pushToast(summarizeError(error), "error")
      } finally {
        state.loading = false
        state.loadingMessage = ""
        renderApp()
      }
    })
  }

  document.querySelectorAll("[data-scheduling-field]").forEach((input) => {
    input.addEventListener("change", () => {
      const [profileIndexStr, fieldName] = String(input.dataset.schedulingField || "").split(":")
      const profileIndex = Number(profileIndexStr)
      const context = ensureSchedulingPolicy(state.companyContext || {})
      const profile = context.scheduling_policy.profiles[profileIndex]
      if (!profile) return
      const value = Number(input.value || 0)
      if (Number.isFinite(value) && value >= 0) {
        profile[fieldName] = value
      }
      state.companyContext = context
    })
  })

  document.querySelectorAll("[data-scheduling-weekday-toggle]").forEach((checkbox) => {
    checkbox.addEventListener("change", () => {
      const [profileIndexStr, weekday] = String(checkbox.dataset.schedulingWeekdayToggle || "").split(":")
      const profileIndex = Number(profileIndexStr)
      const context = ensureSchedulingPolicy(state.companyContext || {})
      const profile = context.scheduling_policy.profiles[profileIndex]
      if (!profile) return
      const allowed = new Set((profile.allowed_weekdays || []).map((item) => String(item).toLowerCase()))
      if (checkbox.checked) {
        allowed.add(weekday)
      } else {
        allowed.delete(weekday)
      }
      profile.allowed_weekdays = WEEKDAY_DEFS.filter(({ key }) => allowed.has(key)).map(({ key }) => key)
      state.companyContext = context
    })
  })

  document.querySelectorAll("[data-scheduling-add-range]").forEach((button) => {
    button.addEventListener("click", () => {
      const [profileIndexStr, weekday] = String(button.dataset.schedulingAddRange || "").split(":")
      const profileIndex = Number(profileIndexStr)
      const context = ensureSchedulingPolicy(state.companyContext || {})
      const profile = context.scheduling_policy.profiles[profileIndex]
      if (!profile) return
      if (!Array.isArray(profile.day_windows[weekday])) {
        profile.day_windows[weekday] = []
      }
      profile.day_windows[weekday].push("09:00-12:00")
      const allowed = new Set((profile.allowed_weekdays || []).map((item) => String(item).toLowerCase()))
      allowed.add(weekday)
      profile.allowed_weekdays = WEEKDAY_DEFS.filter(({ key }) => allowed.has(key)).map(({ key }) => key)
      state.companyContext = context
      renderApp()
    })
  })

  document.querySelectorAll("[data-scheduling-remove-range]").forEach((button) => {
    button.addEventListener("click", () => {
      const [profileIndexStr, weekday, rangeIndexStr] = String(button.dataset.schedulingRemoveRange || "").split(":")
      const profileIndex = Number(profileIndexStr)
      const rangeIndex = Number(rangeIndexStr)
      const context = ensureSchedulingPolicy(state.companyContext || {})
      const profile = context.scheduling_policy.profiles[profileIndex]
      if (!profile) return
      const ranges = profile.day_windows[weekday]
      if (!Array.isArray(ranges)) return
      ranges.splice(rangeIndex, 1)
      state.companyContext = context
      renderApp()
    })
  })

  document.querySelectorAll("[data-scheduling-range-handle]").forEach((handle) => {
    handle.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return
      const [profileIndexStr, weekday, rangeIndexStr, edge] = String(handle.dataset.schedulingRangeHandle || "").split(":")
      const profileIndex = Number(profileIndexStr)
      const rangeIndex = Number(rangeIndexStr)
      const context = ensureSchedulingPolicy(state.companyContext || {})
      const profile = context.scheduling_policy.profiles[profileIndex]
      if (!profile) return
      const ranges = profile.day_windows[weekday]
      if (!Array.isArray(ranges) || !ranges[rangeIndex]) return
      const bar = document.querySelector(`[data-scheduling-range-bar="${profileIndex}:${weekday}:${rangeIndex}"]`)
      const fill = document.querySelector(`[data-scheduling-range-fill="${profileIndex}:${weekday}:${rangeIndex}"]`)
      const label = document.querySelector(`[data-scheduling-range-label="${profileIndex}:${weekday}:${rangeIndex}"]`)
      if (!bar || !fill) return
      const rect = bar.getBoundingClientRect()
      const dayMinutes = 24 * 60
      let { startMin, endMin } = parseRangeString(ranges[rangeIndex])

      try { handle.setPointerCapture(event.pointerId) } catch (_) {}
      event.preventDefault()
      event.stopPropagation()

      const onMove = (moveEvent) => {
        const ratio = Math.max(0, Math.min(1, (moveEvent.clientX - rect.left) / rect.width))
        const rawMin = Math.round((ratio * dayMinutes) / 15) * 15
        if (edge === "start") {
          startMin = Math.max(0, Math.min(endMin - 15, rawMin))
        } else {
          endMin = Math.max(startMin + 15, Math.min(dayMinutes, rawMin))
        }
        const leftPct = (startMin / dayMinutes) * 100
        const widthPct = Math.max(0.5, ((endMin - startMin) / dayMinutes) * 100)
        fill.style.left = `${leftPct}%`
        fill.style.width = `${widthPct}%`
        if (label) {
          label.textContent = `${minutesToClock(startMin)} → ${minutesToClock(endMin)}`
        }
      }

      const onUp = () => {
        handle.removeEventListener("pointermove", onMove)
        handle.removeEventListener("pointerup", onUp)
        handle.removeEventListener("pointercancel", onUp)
        try { handle.releasePointerCapture(event.pointerId) } catch (_) {}
        ranges[rangeIndex] = rangeToString(startMin, endMin)
        state.companyContext = context
      }

      handle.addEventListener("pointermove", onMove)
      handle.addEventListener("pointerup", onUp)
      handle.addEventListener("pointercancel", onUp)
    })
  })
}

function bindManualBookingEvents() {
  const openButton = document.getElementById("open-manual-booking")
  if (openButton) {
    openButton.addEventListener("click", () => {
      state.manualBookingOpen = true
      renderApp()
    })
  }
  const closeButton = document.getElementById("close-manual-booking")
  if (closeButton) {
    closeButton.addEventListener("click", () => {
      state.manualBookingOpen = false
      renderApp()
    })
  }
  const backdrop = document.querySelector("[data-manual-booking-backdrop]")
  if (backdrop) {
    backdrop.addEventListener("click", (event) => {
      if (event.target === backdrop) {
        state.manualBookingOpen = false
        renderApp()
      }
    })
  }

  document.querySelectorAll("[data-manual-booking-submit]").forEach((button) => {
    button.addEventListener("click", async (event) => {
      event.preventDefault()
      const mode = button.dataset.manualBookingSubmit
      const form = document.getElementById("manual-booking-form")
      if (!form) return
      const formData = new FormData(form)
      const draft = {
        contactId: String(formData.get("contact_id") || "").trim(),
        title: String(formData.get("title") || "").trim(),
        startsAt: String(formData.get("starts_at") || ""),
        endsAt: String(formData.get("ends_at") || ""),
        description: String(formData.get("description") || "").trim(),
      }
      state.drafts.manualBooking = draft
      if (!draft.title || !draft.startsAt || !draft.endsAt) {
        pushToast("Título, início e fim são obrigatórios.", "error")
        return
      }
      state.loading = true
      state.loadingMessage = mode === "bot" ? "Criando booking e enfileirando confirmação..." : "Criando booking manual..."
      renderApp()
      try {
        await apiRequest("/admin/calendar/bookings", {
          method: "POST",
          params: getAdminCompanyParams(),
          body: {
            contact_id: draft.contactId || null,
            title: draft.title,
            starts_at: normalizeDatetimeLocal(draft.startsAt),
            ends_at: normalizeDatetimeLocal(draft.endsAt),
            description: draft.description || null,
            request_bot_confirmation: mode === "bot",
          },
        })
        pushToast(mode === "bot" ? "Booking criado. Bot enviou confirmação." : "Booking criado com sucesso.")
        state.manualBookingOpen = false
        state.drafts.manualBooking = { contactId: "", title: "", startsAt: "", endsAt: "", description: "" }
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
}

function bindAgendaEvents() {
  bindSchedulingPolicyEditorEvents()
  bindManualBookingEvents()
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
          params: getAdminCompanyParams(),
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
          params: getAdminCompanyParams(),
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
      if (!state.config.companyId) {
        pushToast("Selecione uma empresa antes de criar um job de fine-tuning.", "error")
        return
      }
      state.loading = true
      state.loadingMessage = "Criando job de fine-tuning..."
      renderApp()
      try {
        await apiRequest("/admin/finetuning/jobs", {
          method: "POST",
          params: getAdminCompanyParams(),
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

// ── Global event delegation (registered ONCE, survives innerHTML swaps) ───

root.addEventListener("click", async (e) => {
  const viewBtn = e.target.closest("[data-view]")
  if (viewBtn) {
    state.view = viewBtn.dataset.view
    state.mobileMenuOpen = false
    renderApp()
    await refreshCurrentView()
    return
  }

  const jumpBtn = e.target.closest("[data-jump-view]")
  if (jumpBtn) {
    state.view = jumpBtn.dataset.jumpView
    state.mobileMenuOpen = false
    renderApp()
    await refreshCurrentView()
    return
  }

  const refreshBtn = e.target.closest("[data-refresh-view]")
  if (refreshBtn) {
    await refreshCurrentView()
    return
  }

  // Mobile menu
  if (e.target.closest("#mobile-menu-toggle")) {
    state.mobileMenuOpen = true
    renderApp()
    return
  }
  if (e.target.closest("#sidebar-close") || e.target.closest("#sidebar-overlay")) {
    state.mobileMenuOpen = false
    renderApp()
    return
  }
})

// ── Real-time SSE connection ──────────────────────────────────────────

let _eventSource = null
let _sseRetryDelayMs = 5000
const SSE_MAX_RETRY_DELAY_MS = 60000

function disconnectSSE() {
  if (!_eventSource) return
  _eventSource.close()
  _eventSource = null
}

// The stream requires an authenticated platform session; without one the server
// answers 401, so reconnecting is pointless until the user logs in again.
function connectSSE() {
  if (_eventSource) return
  if (!state.session) return
  try {
    _eventSource = new EventSource("/platform/events")

    _eventSource.addEventListener("connected", () => {
      state.connected = true
      _sseRetryDelayMs = 5000
    })

    _eventSource.onmessage = (ev) => {
      try {
        const event = JSON.parse(ev.data)
        handleRealtimeEvent(event)
      } catch {
        // ignore malformed events
      }
    }

    _eventSource.onerror = () => {
      disconnectSSE()
      if (!state.session) return
      const retryDelayMs = _sseRetryDelayMs
      _sseRetryDelayMs = Math.min(_sseRetryDelayMs * 2, SSE_MAX_RETRY_DELAY_MS)
      setTimeout(connectSSE, retryDelayMs)
    }
  } catch {
    // SSE not supported or connection failed
  }
}

const SSE_LABELS = {
  new_message: "Nova mensagem recebida",
  new_booking: "Novo agendamento criado",
  reminders_dispatched: "Lembretes enviados",
  booking_cancelled: "Agendamento cancelado",
  handoff_required: "Handoff solicitado",
}

function handleRealtimeEvent(event) {
  const label = SSE_LABELS[event.type]
  if (!label) return

  let detail = ""
  if (event.type === "new_message") {
    detail = event.data?.contact_id ? ` — ${event.data.contact_id}` : ""
    if (event.data?.handoff_required) {
      pushToast("Handoff solicitado" + detail, "warn")
    }
  }
  if (event.type === "new_booking") {
    detail = event.data?.title ? ` — ${event.data.title}` : ""
  }
  if (event.type === "reminders_dispatched") {
    detail = event.data?.count ? ` (${event.data.count})` : ""
  }

  pushToast(label + detail, "success")

  // Auto-refresh if viewing relevant data
  const refreshViews = {
    new_message: ["dashboard", "contacts"],
    new_booking: ["agenda", "dashboard", "analytics"],
    reminders_dispatched: ["agenda", "dashboard"],
  }
  if (refreshViews[event.type]?.includes(state.view)) {
    refreshCurrentView({ silent: true })
  }
}

async function bootstrap() {
  renderApp()
  try {
    await fetchAuthCapabilities()
    await fetchPlatformSession()
    await refreshCurrentView()
    state.settingsOpen = Boolean(
      state.authAction.invitationToken || state.authAction.passwordResetToken
    )
    connectSSE()
    renderApp()
  } catch {
    try {
      await fetchAuthCapabilities()
    } catch {
      // Keep conservative defaults when capability discovery is unavailable.
    }
    state.settingsOpen = true
    renderApp()
  }
}

bootstrap()
