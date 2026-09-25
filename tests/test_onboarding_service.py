from app.services import onboarding_service
from app.services.onboarding_service import (
    BUSINESS_TYPE_PRESETS,
    OnboardingError,
    _build_company_context,
    _slugify,
    get_onboarding_presets,
    get_setup_status,
    setup_company,
)


class FakeRepository:
    def __init__(self):
        self.companies = {}
        self._next_id = 1
        self.calendar_integrations = []
        self.bookings = []

    def get_company_by_slug(self, slug):
        for c in self.companies.values():
            if c["slug"] == slug:
                return c
        return None

    def create_company(self, slug, name, business_type=None, active=True):
        cid = self._next_id
        self._next_id += 1
        self.companies[cid] = {
            "id": cid,
            "slug": slug,
            "name": name,
            "business_type": business_type,
            "active": 1 if active else 0,
            "created_at": "2026-04-18T10:00:00",
            "updated_at": "2026-04-18T10:00:00",
        }
        return cid

    def get_company(self, company_id):
        return self.companies.get(company_id)

    def get_appointment_bookings(self, company_id=None, status=None, contact_id=None):
        return self.bookings


def _fake_save_context(data, company_slug=None):
    return data


def _fake_create_calendar_integration(**kwargs):
    return {"id": 1, "provider": kwargs.get("provider", "mock")}


def _fake_get_calendar_integrations(company_id=None):
    return []


def _fake_list_reminders(company_id=None):
    return []


def _patch(monkeypatch, repo=None):
    from app.services import company_config_service

    fake = repo or FakeRepository()
    monkeypatch.setattr(onboarding_service, "repository", fake)
    monkeypatch.setattr(onboarding_service, "save_company_context", _fake_save_context)
    monkeypatch.setattr(onboarding_service, "create_calendar_integration", _fake_create_calendar_integration)
    # `setup_company` importa a publicação da versão dentro da função: sem este
    # patch ela consultava o banco real, e o arquivo só passava quando outro
    # teste já tinha criado a empresa com o mesmo id.
    monkeypatch.setattr(
        company_config_service,
        "save_company_config",
        lambda **kwargs: {"id": 1, "company_id": kwargs.get("company_id"), "version": 1, "status": "published"},
    )
    return fake


def test_slugify():
    assert _slugify("Clínica Dra Maria") == "cl-nica-dra-maria"
    assert _slugify("Salão & Barbearia Top") == "sal-o-barbearia-top"
    assert _slugify("simple") == "simple"


def test_get_onboarding_presets():
    presets = get_onboarding_presets()
    assert "business_types" in presets
    assert "timezones" in presets
    assert len(presets["business_types"]) == len(BUSINESS_TYPE_PRESETS)
    for info in presets["business_types"].values():
        assert "label" in info
        assert "key" in info


def test_build_company_context_clinica():
    context = _build_company_context(
        company_name="Clinica Teste",
        business_type_key="clinica",
        phone="+55 62 99999-0000",
        timezone="America/Sao_Paulo",
        weekday_hours="08:00-17:00",
        saturday_hours="08:00-12:00",
        sunday_hours="fechado",
        slot_duration_minutes=30,
        minimum_notice_minutes=60,
    )

    assert context["company_name"] == "Clinica Teste"
    assert context["business_identity"]["business_type"] == "clinica"
    assert context["scheduling_policy"]["default_slot_duration_minutes"] == 30
    assert context["scheduling_policy"]["minimum_notice_minutes"] == 60
    assert context["business_hours"]["weekdays"] == "08:00-17:00"
    assert context["contact_channels"]["phone"] == "+55 62 99999-0000"
    assert context["enabled_modules"]["scheduling"] is True
    assert context["enabled_modules"]["reminders"] is True
    assert context["reminder_policy"]["enabled"] is True
    assert "brand_tone" in context
    assert "business_rules" in context
    assert "llm_guidelines" in context
    assert "catalog" in context


def test_build_company_context_with_services():
    context = _build_company_context(
        company_name="Salao X",
        business_type_key="salao",
        services=[
            {"name": "Corte Masculino", "duration_minutes": 30, "price": 40.0},
            {"name": "Barba", "duration_minutes": 20, "price": 25.0},
        ],
    )

    items = context["catalog"]["items"]
    assert len(items) == 2
    assert items[0]["name"] == "Corte Masculino"
    assert items[0]["duration"] == "30 minutos"
    assert items[0]["price"] == 40.0
    assert items[1]["name"] == "Barba"


def test_build_company_context_with_custom_rules():
    context = _build_company_context(
        company_name="Escritorio",
        business_type_key="escritorio",
        custom_rules=["Nunca inventar prazo", "Sempre confirmar valor"],
    )

    assert len(context["business_rules"]) == 2
    assert "Nunca inventar prazo" in context["business_rules"]


def test_build_company_context_with_reminder_offsets():
    context = _build_company_context(
        company_name="Teste",
        business_type_key="outro",
        reminder_offsets_days=[5, 2, 1],
    )

    assert context["reminder_policy"]["customer_reminder_offsets_days"] == [5, 2, 1]


def test_setup_company_success(monkeypatch):
    _patch(monkeypatch)

    result = setup_company(
        company_name="Clinica Dra Maria",
        business_type_key="clinica",
        phone="+55 62 99999-0000",
    )

    assert result["onboarding_complete"] is True
    assert result["company"]["name"] == "Clinica Dra Maria"
    assert result["company_context_saved"] is True
    assert isinstance(result["next_steps"], list)


def test_setup_company_creates_calendar_when_provider_given(monkeypatch):
    _patch(monkeypatch)

    result = setup_company(
        company_name="Academia Alpha",
        business_type_key="academia",
        calendar_provider="mock",
    )

    assert result["calendar_integration"] is not None
    assert result["calendar_integration"]["provider"] == "mock"


def test_setup_company_rejects_empty_name(monkeypatch):
    _patch(monkeypatch)

    try:
        setup_company(company_name="", business_type_key="clinica")
        raise AssertionError("Should have raised OnboardingError")
    except OnboardingError as exc:
        assert "company_name" in str(exc)


def test_setup_company_rejects_invalid_business_type(monkeypatch):
    _patch(monkeypatch)

    try:
        setup_company(company_name="Teste", business_type_key="invalid_type")
        raise AssertionError("Should have raised OnboardingError")
    except OnboardingError as exc:
        assert "Invalid business_type_key" in str(exc)


def test_setup_company_rejects_duplicate_slug(monkeypatch):
    repo = FakeRepository()
    _patch(monkeypatch, repo)

    setup_company(company_name="Clinica Unica", business_type_key="clinica")

    try:
        setup_company(company_name="Clinica Unica", business_type_key="clinica")
        raise AssertionError("Should have raised OnboardingError")
    except OnboardingError as exc:
        assert "already exists" in str(exc)


def test_get_setup_status(monkeypatch):
    repo = FakeRepository()
    _patch(monkeypatch, repo)
    monkeypatch.setattr(
        "app.services.onboarding_service.get_setup_status.__module__",
        "app.services.onboarding_service",
        raising=False,
    )

    cid = repo.create_company(slug="test-co", name="Test Co", business_type="outro")

    from app.services import onboarding_service as mod
    monkeypatch.setattr(mod, "get_calendar_integrations", _fake_get_calendar_integrations, raising=False)

    # Patch the imports used inside get_setup_status
    import app.services.calendar_service as cal_mod
    import app.services.reminder_service as rem_mod
    monkeypatch.setattr(cal_mod, "get_calendar_integrations", _fake_get_calendar_integrations, raising=False)

    monkeypatch.setattr(rem_mod, "list_reminders", _fake_list_reminders, raising=False)

    status = get_setup_status(cid)

    assert status["company"]["slug"] == "test-co"
    assert status["progress"] >= 0
    assert "completed_steps" in status
    assert "pending_steps" in status
    assert "company_created" in status["completed_steps"]


def test_get_setup_status_not_found(monkeypatch):
    _patch(monkeypatch)

    try:
        get_setup_status(999)
        raise AssertionError("Should have raised OnboardingError")
    except OnboardingError as exc:
        assert "not found" in str(exc)


def test_all_business_types_produce_valid_context():
    for key in BUSINESS_TYPE_PRESETS:
        context = _build_company_context(
            company_name=f"Teste {key}",
            business_type_key=key,
        )
        assert context["company_name"] == f"Teste {key}"
        assert context["business_identity"]["business_type"] == key
        assert isinstance(context["enabled_modules"], dict)
        assert isinstance(context["catalog"]["items"], list)
        assert isinstance(context["business_rules"], list)
        assert context["scheduling_policy"]["enabled"] is True
        assert context["reminder_policy"]["enabled"] is True
