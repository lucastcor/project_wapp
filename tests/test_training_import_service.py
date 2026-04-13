from app.services import training_import_service


class FakeRepository:
    def __init__(self) -> None:
        self.saved_contacts: list[dict] = []
        self.created_examples: list[dict] = []

    def save_contact(self, **kwargs) -> None:
        self.saved_contacts.append(kwargs)

    def create_message_example(self, **kwargs) -> int:
        self.created_examples.append(kwargs)
        return len(self.created_examples)


def test_import_training_records_csv_dry_run(monkeypatch) -> None:
    fake_repository = FakeRepository()
    monkeypatch.setattr(training_import_service, "repository", fake_repository)

    result = training_import_service.import_training_records(
        company_id=7,
        fmt="csv",
        raw_text=(
            "contact_id,contact_name,customer_profile,profile_name,scenario_type,customer_message,approved_reply,quality_score\n"
            "5511999999999,Maria Silva,returning_customer,retorno,reschedule,Quero remarcar,Claro,9\n"
            "5511888888888,Ana Souza,new_customer,novos,general,Tenho interesse,,9\n"
        ),
        records=[],
        dry_run=True,
        update_contacts=True,
        create_message_examples=True,
        auto_promote_eligible_examples=True,
    )

    assert result["dry_run"] is True
    assert result["parsed_records"] == 2
    assert result["contacts_to_update"] == 2
    assert result["message_examples_to_create"] == 1
    assert result["eligible_examples"] == 1
    assert result["skipped_examples"] == 1
    assert fake_repository.saved_contacts == []
    assert fake_repository.created_examples == []


def test_import_training_records_json_persists_contacts_and_examples(monkeypatch) -> None:
    fake_repository = FakeRepository()
    monkeypatch.setattr(training_import_service, "repository", fake_repository)

    result = training_import_service.import_training_records(
        company_id=4,
        fmt="json",
        raw_text=None,
        records=[
            {
                "contact_id": "5511777777777",
                "contact_name": "Carlos Lima",
                "customer_profile": "vip",
                "profile_name": "vip",
                "scenario_type": "general",
                "customer_message": "Consegue me atender amanhã?",
                "approved_reply": "Consigo sim, vou te mostrar os horários disponíveis.",
                "quality_score": 9,
            }
        ],
        dry_run=False,
        update_contacts=True,
        create_message_examples=True,
        auto_promote_eligible_examples=True,
    )

    assert result["dry_run"] is False
    assert result["contacts_to_update"] == 1
    assert result["message_examples_to_create"] == 1
    assert fake_repository.saved_contacts[0]["contact_id"] == "5511777777777"
    assert fake_repository.saved_contacts[0]["customer_profile"] == "vip"
    assert fake_repository.created_examples[0]["profile_name"] == "vip"
    assert fake_repository.created_examples[0]["used_for_training"] is True
