from app.core.config import settings
from app.services import outbound_dispatch_service
from app.services.outbound_dispatch_service import (
    OutboundDispatchError,
    dispatch_outbound_message,
    dispatch_pending_outbound_messages,
)


class FakeRepository:
    def __init__(self) -> None:
        self.messages = {
            1: {
                "id": 1,
                "contact_id": "5511999991111",
                "company_id": 7,
                "pending_question_id": 10,
                "message": "Mensagem pendente 1",
                "source": "owner_resolution",
                "status": "pending",
                "created_at": "2026-03-16 10:00:00",
                "sent_at": None,
            },
            2: {
                "id": 2,
                "contact_id": "5511999992222",
                "company_id": 8,
                "pending_question_id": 11,
                "message": "Mensagem pendente 2",
                "source": "owner_resolution",
                "status": "pending",
                "created_at": "2026-03-16 10:05:00",
                "sent_at": None,
            },
            3: {
                "id": 3,
                "contact_id": "5511999993333",
                "company_id": 7,
                "pending_question_id": 12,
                "message": "Mensagem já enviada",
                "source": "owner_resolution",
                "status": "sent",
                "created_at": "2026-03-16 10:10:00",
                "sent_at": "2026-03-16 10:11:00",
            },
        }
        self.marked_as_sent: list[int] = []
        self.outbound_message_lookup_calls: list[dict] = []
        self.outbound_messages_list_calls: list[dict] = []

    def get_outbound_message(
        self,
        outbound_message_id: int,
        *,
        company_id: int | None = None,
    ) -> dict | None:
        self.outbound_message_lookup_calls.append(
            {
                "outbound_message_id": outbound_message_id,
                "company_id": company_id,
            }
        )
        message = self.messages.get(outbound_message_id)
        if message is None:
            return None
        if company_id is not None and message.get("company_id") != company_id:
            return None
        return message

    def get_outbound_messages(self, *, company_id: int | None = None) -> list[dict]:
        self.outbound_messages_list_calls.append({"company_id": company_id})
        messages = [self.messages[key] for key in sorted(self.messages)]
        if company_id is not None:
            messages = [message for message in messages if message.get("company_id") == company_id]
        return messages

    def mark_outbound_message_sent(
        self,
        outbound_message_id: int,
        *,
        company_id: int | None = None,
    ) -> None:
        self.marked_as_sent.append(outbound_message_id)
        message = self.messages[outbound_message_id]
        if company_id is not None and message.get("company_id") != company_id:
            return
        message["status"] = "sent"
        message["sent_at"] = "2026-03-16 11:00:00"


class FakeWhatsAppProvider:
    def __init__(self, *, company_id: int | None = None) -> None:
        self.company_id = company_id

    def send_message(self, contact_id: str, message: str) -> dict:
        return {
            "channel": "whatsapp",
            "contact_id": contact_id,
            "message": message,
            "provider_message_id": "wamid.test.123",
            "provider_response": {
                "messages": [{"id": "wamid.test.123"}],
            },
        }


class BrokenWhatsAppProvider:
    def __init__(self, *, company_id: int | None = None) -> None:
        self.company_id = company_id

    def send_message(self, contact_id: str, message: str) -> dict:
        raise outbound_dispatch_service.WhatsAppProviderError("Falha no provider WhatsApp")



def test_dispatch_outbound_message_with_mock_provider(monkeypatch) -> None:
    fake_repository = FakeRepository()
    monkeypatch.setattr(outbound_dispatch_service, "repository", fake_repository)
    monkeypatch.setattr(settings, "outbound_provider", "mock")
    monkeypatch.setattr(settings, "outbound_default_channel", "whatsapp")

    result = dispatch_outbound_message(outbound_message_id=1)

    assert result["outbound_message_id"] == 1
    assert result["delivery_status"] == "sent"
    assert result["channel"] == "whatsapp"
    assert result["provider_response"]["provider"] == "mock"
    assert fake_repository.marked_as_sent == [1]
    assert fake_repository.outbound_message_lookup_calls[0] == {
        "outbound_message_id": 1,
        "company_id": None,
    }



def test_dispatch_outbound_message_returns_already_sent(monkeypatch) -> None:
    fake_repository = FakeRepository()
    monkeypatch.setattr(outbound_dispatch_service, "repository", fake_repository)
    monkeypatch.setattr(settings, "outbound_provider", "mock")
    monkeypatch.setattr(settings, "outbound_default_channel", "whatsapp")

    result = dispatch_outbound_message(outbound_message_id=3)

    assert result["outbound_message_id"] == 3
    assert result["delivery_status"] == "already_sent"
    assert fake_repository.marked_as_sent == []



def test_dispatch_pending_outbound_messages_dispatches_all_pending(monkeypatch) -> None:
    fake_repository = FakeRepository()
    monkeypatch.setattr(outbound_dispatch_service, "repository", fake_repository)
    monkeypatch.setattr(settings, "outbound_provider", "mock")
    monkeypatch.setattr(settings, "outbound_default_channel", "whatsapp")

    results = dispatch_pending_outbound_messages()

    assert len(results) == 2
    assert {result["outbound_message_id"] for result in results} == {1, 2}
    assert fake_repository.marked_as_sent == [1, 2]


def test_dispatch_pending_outbound_messages_respects_company_scope(monkeypatch) -> None:
    fake_repository = FakeRepository()
    monkeypatch.setattr(outbound_dispatch_service, "repository", fake_repository)
    monkeypatch.setattr(settings, "outbound_provider", "mock")
    monkeypatch.setattr(settings, "outbound_default_channel", "whatsapp")

    results = dispatch_pending_outbound_messages(company_id=7)

    assert {result["outbound_message_id"] for result in results} == {1}
    assert fake_repository.marked_as_sent == [1]
    assert fake_repository.outbound_messages_list_calls == [{"company_id": 7}]
    assert fake_repository.outbound_message_lookup_calls[0] == {
        "outbound_message_id": 1,
        "company_id": 7,
    }



def test_dispatch_outbound_message_with_whatsapp_provider(monkeypatch) -> None:
    fake_repository = FakeRepository()
    monkeypatch.setattr(outbound_dispatch_service, "repository", fake_repository)
    monkeypatch.setattr(settings, "outbound_provider", "whatsapp")
    monkeypatch.setattr(settings, "outbound_default_channel", "whatsapp")
    monkeypatch.setattr(outbound_dispatch_service, "WhatsAppProvider", FakeWhatsAppProvider)

    result = dispatch_outbound_message(outbound_message_id=1, channel="whatsapp")

    assert result["delivery_status"] == "sent"
    assert result["provider_message_id"] == "wamid.test.123"
    assert fake_repository.marked_as_sent == [1]



def test_dispatch_outbound_message_raises_for_unsupported_channel(monkeypatch) -> None:
    fake_repository = FakeRepository()
    monkeypatch.setattr(outbound_dispatch_service, "repository", fake_repository)
    monkeypatch.setattr(settings, "outbound_provider", "mock")

    try:
        dispatch_outbound_message(outbound_message_id=1, channel="email")
        raise AssertionError("Expected OutboundDispatchError to be raised")
    except OutboundDispatchError as exc:
        assert "unsupported outbound channel" in str(exc).lower()



def test_dispatch_outbound_message_raises_when_whatsapp_provider_fails(monkeypatch) -> None:
    fake_repository = FakeRepository()
    monkeypatch.setattr(outbound_dispatch_service, "repository", fake_repository)
    monkeypatch.setattr(settings, "outbound_provider", "whatsapp")
    monkeypatch.setattr(settings, "outbound_default_channel", "whatsapp")
    monkeypatch.setattr(outbound_dispatch_service, "WhatsAppProvider", BrokenWhatsAppProvider)

    try:
        dispatch_outbound_message(outbound_message_id=1, channel="whatsapp")
        raise AssertionError("Expected OutboundDispatchError to be raised")
    except OutboundDispatchError as exc:
        assert "falha no provider whatsapp" in str(exc).lower()
        assert fake_repository.marked_as_sent == []
