import re
from typing import Any

from app.core.company_context import save_company_context
from app.services.calendar_service import (
    CalendarProviderError,
    create_calendar_integration,
)
from app.storage.repository import RuntimeRepository

repository = RuntimeRepository()


class OnboardingError(Exception):
    pass


BUSINESS_TYPE_PRESETS = {
    "clinica": {
        "label": "Clinica / Consultorio",
        "business_model": ["services", "scheduling"],
        "segment": "Saude e bem-estar",
        "modules": {
            "catalog": True,
            "services": True,
            "products": False,
            "scheduling": True,
            "staff_selection": True,
            "rentals": False,
            "inventory": False,
            "delivery": False,
            "pickup": False,
            "subscriptions": False,
            "custom_orders": False,
            "quotes": False,
            "support": True,
            "financial_handoff": True,
            "reminders": True,
        },
        "default_slot_minutes": 30,
        "catalog_categories": [
            {"name": "consultas", "type": "service"},
            {"name": "exames", "type": "service"},
            {"name": "procedimentos", "type": "service"},
        ],
    },
    "salao": {
        "label": "Salao de beleza / Barbearia",
        "business_model": ["services", "scheduling"],
        "segment": "Beleza e estetica",
        "modules": {
            "catalog": True,
            "services": True,
            "products": True,
            "scheduling": True,
            "staff_selection": True,
            "rentals": False,
            "inventory": True,
            "delivery": False,
            "pickup": False,
            "subscriptions": False,
            "custom_orders": False,
            "quotes": False,
            "support": True,
            "financial_handoff": True,
            "reminders": True,
        },
        "default_slot_minutes": 45,
        "catalog_categories": [
            {"name": "cortes", "type": "service"},
            {"name": "tratamentos", "type": "service"},
            {"name": "coloracao", "type": "service"},
            {"name": "produtos", "type": "product"},
        ],
    },
    "escritorio": {
        "label": "Escritorio / Consultoria profissional",
        "business_model": ["services", "scheduling", "custom_orders"],
        "segment": "Servicos profissionais",
        "modules": {
            "catalog": True,
            "services": True,
            "products": False,
            "scheduling": True,
            "staff_selection": True,
            "rentals": False,
            "inventory": False,
            "delivery": False,
            "pickup": False,
            "subscriptions": True,
            "custom_orders": True,
            "quotes": True,
            "support": True,
            "financial_handoff": True,
            "reminders": True,
        },
        "default_slot_minutes": 60,
        "catalog_categories": [
            {"name": "consultoria", "type": "service"},
            {"name": "assessoria", "type": "service"},
        ],
    },
    "academia": {
        "label": "Academia / Studio fitness",
        "business_model": ["services", "scheduling", "subscription"],
        "segment": "Fitness e saude",
        "modules": {
            "catalog": True,
            "services": True,
            "products": True,
            "scheduling": True,
            "staff_selection": True,
            "rentals": False,
            "inventory": True,
            "delivery": False,
            "pickup": False,
            "subscriptions": True,
            "custom_orders": False,
            "quotes": False,
            "support": True,
            "financial_handoff": True,
            "reminders": True,
        },
        "default_slot_minutes": 60,
        "catalog_categories": [
            {"name": "aulas", "type": "service"},
            {"name": "planos", "type": "subscription_service"},
            {"name": "avaliacoes", "type": "service"},
        ],
    },
    "outro": {
        "label": "Outro tipo de negocio",
        "business_model": ["services", "scheduling"],
        "segment": "Servicos gerais",
        "modules": {
            "catalog": True,
            "services": True,
            "products": True,
            "scheduling": True,
            "staff_selection": False,
            "rentals": False,
            "inventory": False,
            "delivery": False,
            "pickup": False,
            "subscriptions": False,
            "custom_orders": False,
            "quotes": True,
            "support": True,
            "financial_handoff": True,
            "reminders": True,
        },
        "default_slot_minutes": 60,
        "catalog_categories": [
            {"name": "servicos", "type": "service"},
        ],
    },
}

SUPPORTED_TIMEZONES = [
    "America/Sao_Paulo",
    "America/Fortaleza",
    "America/Manaus",
    "America/Belem",
    "America/Cuiaba",
    "America/Recife",
    "America/Bahia",
    "America/Rio_Branco",
    "America/Porto_Velho",
    "America/Campo_Grande",
    "America/Boa_Vista",
    "America/Maceio",
    "America/Araguaina",
    "America/Noronha",
]


def _slugify(name: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", name.strip().lower())
    return normalized.strip("-")


def get_onboarding_presets() -> dict[str, Any]:
    return {
        "business_types": {
            key: {"key": key, "label": preset["label"]}
            for key, preset in BUSINESS_TYPE_PRESETS.items()
        },
        "timezones": SUPPORTED_TIMEZONES,
        "default_timezone": "America/Sao_Paulo",
    }


def _build_company_context(
    *,
    company_name: str,
    business_type_key: str,
    segment: str | None = None,
    company_description: str | None = None,
    phone: str | None = None,
    email: str | None = None,
    instagram: str | None = None,
    website: str | None = None,
    timezone: str = "America/Sao_Paulo",
    weekday_hours: str = "08:00-18:00",
    saturday_hours: str = "fechado",
    sunday_hours: str = "fechado",
    slot_duration_minutes: int = 60,
    minimum_notice_minutes: int = 120,
    reminder_offsets_days: list[int] | None = None,
    brand_style: str | None = None,
    custom_rules: list[str] | None = None,
    services: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    preset = BUSINESS_TYPE_PRESETS.get(business_type_key, BUSINESS_TYPE_PRESETS["outro"])

    context: dict[str, Any] = {
        "company_name": company_name,
        "brand_tone": {
            "style": brand_style or "profissional, cordial e objetiva",
            "language": "pt-BR",
            "formality_level": "media",
            "response_length": "curta a moderada",
            "emoji_policy": "usar com moderacao apenas se fizer sentido para a marca",
        },
        "enabled_modules": preset["modules"],
        "business_identity": {
            "business_type": business_type_key,
            "business_model": preset["business_model"],
            "segment": segment or preset["segment"],
            "company_description": company_description or f"{company_name} — atendimento via WhatsApp.",
            "service_area": "Atendimento conforme abrangencia do negocio",
            "has_physical_store": True,
            "store_type": "atendimento presencial",
            "attends_online": True,
            "attends_in_person": True,
            "onsite_service_available": False,
            "works_with_scheduling": True,
            "works_with_rentals": False,
            "works_with_custom_orders": "custom_orders" in preset["business_model"],
            "works_with_subscription": "subscription" in preset["business_model"],
            "works_with_sales": "products" in preset["business_model"],
            "works_with_after_sales": True,
        },
        "contact_channels": {
            "primary_channel": "whatsapp",
            "secondary_channels": [ch for ch in ["instagram", "email"] if (ch == "instagram" and instagram) or (ch == "email" and email)],
            "phone": phone or "",
            "email": email or "",
            "instagram": instagram or "",
            "website": website or "",
        },
        "business_hours": {
            "timezone": timezone,
            "weekdays": weekday_hours,
            "saturday": saturday_hours,
            "sunday": sunday_hours,
            "holiday_policy": "Atendimento sujeito a confirmacao em feriados.",
            "after_hours_intake_allowed": True,
            "scheduling_window": f"Agendamentos com antecedencia minima de {minimum_notice_minutes} minutos.",
        },
        "response_policy": {
            "response_time": "Ate 2 horas em horario comercial",
            "after_hours_behavior": "Registrar a mensagem e informar que o retorno ocorrera no proximo periodo de atendimento.",
            "auto_reply_allowed_for": ["lead_novo", "cliente_existente", "suporte"],
            "handoff_required_for": ["financeiro", "fornecedor"],
            "human_review_required_for": [],
        },
        "commercial_policy": {
            "currency": "BRL",
            "budget_policy": "Os valores finais dependem do servico e disponibilidade.",
            "payment_methods": ["pix", "cartao_credito"],
            "installments_available": False,
            "minimum_order_value": None,
            "proposal_required_for_custom_cases": True,
            "discount_policy": "Descontos mediante analise.",
            "cancellation_policy": "Cancelamentos devem ser feitos com antecedencia.",
            "refund_policy": "Reembolso sujeito a politica vigente.",
            "quote_validity": "Orcamentos validos por 7 dias.",
        },
        "catalog": {
            "categories": preset.get("catalog_categories", [{"name": "servicos", "type": "service"}]),
            "items": _build_catalog_items(services or [], preset),
        },
        "customer_intake": {
            "scheduling": {
                "required_fields": ["nome", "data desejada", "horario desejado", "servico"],
            },
            "support": {
                "required_fields": ["nome", "descricao do problema"],
            },
        },
        "scheduling_policy": {
            "enabled": True,
            "natural_booking_enabled": True,
            "minimum_notice_minutes": minimum_notice_minutes,
            "default_slot_duration_minutes": slot_duration_minutes,
            "max_suggestions": 3,
            "require_service_selection": True,
            "confirmation_required": True,
            "allow_customer_notes": True,
        },
        "reminder_policy": {
            "enabled": True,
            "internal_manual_reminders_enabled": True,
            "appointment_customer_reminders_enabled": True,
            "customer_reminder_offsets_days": reminder_offsets_days or [2, 1],
            "default_channel": "whatsapp",
            "customer_default_channel": "whatsapp",
            "internal_default_channel": "whatsapp",
        },
        "business_rules": custom_rules or [
            "Nao inventar precos ou disponibilidade.",
            "Quando faltar contexto, perguntar antes de confirmar.",
        ],
        "llm_guidelines": {
            "must_not_do": ["inventar estoque", "inventar disponibilidade", "inventar precos"],
            "should_do": ["responder com objetividade", "usar o contexto da empresa"],
        },
    }

    return context


def _build_catalog_items(
    services: list[dict[str, Any]],
    preset: dict[str, Any],
) -> list[dict[str, Any]]:
    if not services:
        return []

    items = []
    for i, svc in enumerate(services):
        name = str(svc.get("name") or f"Servico {i + 1}").strip()
        duration = int(svc.get("duration_minutes") or preset.get("default_slot_minutes", 60))
        price = svc.get("price")
        category = str(svc.get("category") or "servicos").strip()

        item: dict[str, Any] = {
            "sku": f"SVC-{i + 1:03d}",
            "name": name,
            "category": category,
            "type": "service",
            "transaction_modes": ["service", "scheduling"],
            "description": str(svc.get("description") or name),
            "price_mode": "fixed" if price else "under_consultation",
            "schedule_required": True,
            "professional_selection_allowed": False,
            "duration": f"{duration} minutos",
            "availability": "sob agendamento",
            "delivery_mode": "presencial",
            "customizable": False,
            "subscription_details": None,
            "rental_details": None,
            "stock_info": None,
            "variants": [],
        }
        if price:
            item["price"] = price
            item["starting_price"] = price

        items.append(item)

    return items


def setup_company(
    *,
    company_name: str,
    business_type_key: str,
    segment: str | None = None,
    company_description: str | None = None,
    phone: str | None = None,
    email: str | None = None,
    instagram: str | None = None,
    website: str | None = None,
    timezone: str = "America/Sao_Paulo",
    weekday_hours: str = "08:00-18:00",
    saturday_hours: str = "fechado",
    sunday_hours: str = "fechado",
    slot_duration_minutes: int = 60,
    minimum_notice_minutes: int = 120,
    reminder_offsets_days: list[int] | None = None,
    brand_style: str | None = None,
    custom_rules: list[str] | None = None,
    services: list[dict[str, Any]] | None = None,
    calendar_provider: str | None = None,
    calendar_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not company_name or not company_name.strip():
        raise OnboardingError("company_name is required.")
    if business_type_key not in BUSINESS_TYPE_PRESETS:
        raise OnboardingError(f"Invalid business_type_key. Must be one of: {', '.join(sorted(BUSINESS_TYPE_PRESETS))}.")

    slug = _slugify(company_name)
    if not slug:
        raise OnboardingError("Company name must produce a valid slug.")

    existing = repository.get_company_by_slug(slug)
    if existing:
        raise OnboardingError(f"A company with slug '{slug}' already exists.")

    company_id = repository.create_company(
        slug=slug,
        name=company_name.strip(),
        business_type=business_type_key,
        active=True,
    )

    context = _build_company_context(
        company_name=company_name.strip(),
        business_type_key=business_type_key,
        segment=segment,
        company_description=company_description,
        phone=phone,
        email=email,
        instagram=instagram,
        website=website,
        timezone=timezone,
        weekday_hours=weekday_hours,
        saturday_hours=saturday_hours,
        sunday_hours=sunday_hours,
        slot_duration_minutes=slot_duration_minutes,
        minimum_notice_minutes=minimum_notice_minutes,
        reminder_offsets_days=reminder_offsets_days,
        brand_style=brand_style,
        custom_rules=custom_rules,
        services=services,
    )

    save_company_context(context, company_slug=slug)

    calendar_integration = None
    if calendar_provider:
        try:
            calendar_integration = create_calendar_integration(
                provider=calendar_provider,
                company_id=company_id,
                config=calendar_config,
                active=True,
            )
        except CalendarProviderError:
            calendar_integration = {"error": "Failed to create calendar integration. You can configure it later."}

    company = repository.get_company(company_id)

    return {
        "company": company,
        "company_context_saved": True,
        "calendar_integration": calendar_integration,
        "onboarding_complete": True,
        "next_steps": _build_next_steps(context, calendar_integration),
    }


def _build_next_steps(
    context: dict[str, Any],
    calendar_integration: dict[str, Any] | None,
) -> list[dict[str, str]]:
    steps = []

    if not calendar_integration:
        steps.append({
            "key": "calendar",
            "title": "Configurar integracao de calendario",
            "description": "Conecte Google Calendar ou CalDAV para sincronizar agenda.",
        })

    catalog_items = context.get("catalog", {}).get("items", [])
    if not catalog_items:
        steps.append({
            "key": "catalog",
            "title": "Adicionar servicos ao catalogo",
            "description": "Cadastre os servicos que o bot oferecera para agendamento.",
        })

    steps.append({
        "key": "test",
        "title": "Testar o bot no Bot Studio",
        "description": "Simule uma conversa para validar tom, agendamento e respostas.",
    })

    steps.append({
        "key": "whatsapp",
        "title": "Conectar o WhatsApp Business",
        "description": "Configure o webhook e token da API do WhatsApp para ir ao vivo.",
    })

    return steps


def get_setup_status(company_id: int) -> dict[str, Any]:
    company = repository.get_company(company_id)
    if not company:
        raise OnboardingError(f"Company {company_id} not found.")

    slug = str(company.get("slug") or "")
    has_context = False
    try:
        from app.core.company_context import get_company_context
        get_company_context(company_slug=slug)
        has_context = True
    except Exception:
        has_context = False

    from app.services.calendar_service import get_calendar_integrations
    integrations = get_calendar_integrations(company_id=company_id)
    has_calendar = len(integrations) > 0

    from app.services.reminder_service import list_reminders
    list_reminders(company_id=company_id)

    bookings = repository.get_appointment_bookings(company_id=company_id)
    has_bookings = len(bookings) > 0

    completed_steps = []
    pending_steps = []

    completed_steps.append("company_created")
    if has_context:
        completed_steps.append("context_configured")
    else:
        pending_steps.append({
            "key": "context_configured",
            "title": "Configurar contexto da empresa",
        })

    if has_calendar:
        completed_steps.append("calendar_connected")
    else:
        pending_steps.append({
            "key": "calendar_connected",
            "title": "Conectar calendario",
        })

    if has_bookings:
        completed_steps.append("first_booking")
    else:
        pending_steps.append({
            "key": "first_booking",
            "title": "Realizar primeiro agendamento",
        })

    total = len(completed_steps) + len(pending_steps)
    progress = round(len(completed_steps) / max(total, 1) * 100)

    return {
        "company": company,
        "progress": progress,
        "completed_steps": completed_steps,
        "pending_steps": pending_steps,
        "is_fully_onboarded": len(pending_steps) == 0,
    }
