from typing import Any

import requests


class AdminApiClientError(Exception):
    pass


class AdminApiClient:
    def __init__(
        self,
        base_url: str,
        admin_token: str | None = None,
        admin_actor: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.admin_token = (admin_token or "").strip() or None
        self.admin_actor = (admin_actor or "").strip() or None

    def _build_url(self, path: str) -> str:
        normalized_path = path if path.startswith("/") else f"/{path}"
        return f"{self.base_url}{normalized_path}"

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        headers: dict[str, str] = {}
        if self.admin_token:
            headers["Authorization"] = f"Bearer {self.admin_token}"
        if self.admin_actor:
            headers["X-Admin-Actor"] = self.admin_actor

        try:
            response = requests.request(
                method=method,
                url=self._build_url(path),
                params=params,
                json=json_body,
                headers=headers or None,
                timeout=20,
            )
        except requests.RequestException as exc:
            raise AdminApiClientError(f"Falha de conexão com a API: {exc}") from exc

        if response.status_code >= 400:
            try:
                error_payload = response.json()
            except ValueError:
                error_payload = response.text
            raise AdminApiClientError(
                f"API retornou status {response.status_code}: {error_payload}"
            )

        if not response.content:
            return None

        try:
            return response.json()
        except ValueError as exc:
            raise AdminApiClientError("A API não retornou JSON válido.") from exc

    def get_companies(self) -> list[dict[str, Any]]:
        return self._request("GET", "/admin/companies")

    def get_contacts(self) -> list[dict[str, Any]]:
        return self._request("GET", "/admin/contacts")

    def create_company(
        self,
        slug: str,
        name: str,
        business_type: str | None = None,
        active: bool = True,
    ) -> dict[str, Any]:
        payload = {
            "slug": slug,
            "name": name,
            "business_type": business_type,
            "active": active,
        }
        return self._request("POST", "/admin/companies", json_body=payload)

    def get_company_context(self, company_slug: str | None = None) -> dict[str, Any]:
        params = {"company_slug": company_slug} if company_slug else None
        return self._request("GET", "/admin/company-context", params=params)

    def update_company_context(
        self,
        data: dict[str, Any],
        company_slug: str | None = None,
    ) -> dict[str, Any]:
        params = {"company_slug": company_slug} if company_slug else None
        return self._request(
            "PUT",
            "/admin/company-context",
            params=params,
            json_body={"data": data},
        )

    def get_pending_questions(self) -> list[dict[str, Any]]:
        return self._request("GET", "/admin/pending-questions")

    def get_knowledge_entries(
        self,
        company_id: int | None = None,
    ) -> list[dict[str, Any]]:
        params = {"company_id": company_id} if company_id is not None else None
        return self._request("GET", "/admin/knowledge-entries", params=params)

    def get_message_examples(
        self,
        company_id: int | None = None,
    ) -> list[dict[str, Any]]:
        params = {"company_id": company_id} if company_id is not None else None
        return self._request("GET", "/admin/message-examples", params=params)

    def create_message_example(
        self,
        profile_name: str,
        scenario_type: str,
        customer_message: str,
        approved_reply: str,
        style_notes: str | None = None,
        quality_score: float = 0,
        used_for_training: bool = False,
        active: bool = True,
        company_id: int | None = None,
    ) -> dict[str, Any]:
        params = {"company_id": company_id} if company_id is not None else None
        payload = {
            "profile_name": profile_name,
            "scenario_type": scenario_type,
            "customer_message": customer_message,
            "approved_reply": approved_reply,
            "style_notes": style_notes,
            "quality_score": quality_score,
            "used_for_training": used_for_training,
            "active": active,
        }
        return self._request("POST", "/admin/message-examples", params=params, json_body=payload)

    def answer_pending_question(
        self,
        question_id: int,
        owner_answer: str,
        resolution_type: str,
        customer_followup_question: str | None = None,
        applies_globally: bool = False,
        knowledge_action: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "owner_answer": owner_answer,
            "resolution_type": resolution_type,
            "customer_followup_question": customer_followup_question,
            "applies_globally": applies_globally,
            "knowledge_action": knowledge_action,
        }
        return self._request(
            "POST",
            f"/admin/pending-questions/{question_id}/answer",
            json_body=payload,
        )

    def get_outbound_messages(self) -> list[dict[str, Any]]:
        return self._request("GET", "/admin/outbound-messages")

    def dispatch_outbound_message(
        self,
        outbound_message_id: int,
        channel: str = "whatsapp",
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/admin/outbound-messages/{outbound_message_id}/dispatch",
            params={"channel": channel},
        )

    def dispatch_pending_outbound_messages(
        self,
        channel: str = "whatsapp",
    ) -> list[dict[str, Any]]:
        return self._request(
            "POST",
            "/admin/outbound-messages/dispatch-pending",
            params={"channel": channel},
        )

    def get_training_dataset_review(
        self,
        company_id: int | None = None,
    ) -> dict[str, Any]:
        params = {"company_id": company_id} if company_id is not None else None
        return self._request("GET", "/admin/training-dataset/review", params=params)

    def get_observability_summary(self) -> dict[str, Any]:
        return self._request("GET", "/admin/observability/summary")

    def get_audit_events(
        self,
        limit: int = 100,
        action: str | None = None,
        resource_type: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit}
        if action:
            params["action"] = action
        if resource_type:
            params["resource_type"] = resource_type
        return self._request("GET", "/admin/audit-events", params=params)

    def cleanup_audit_events(self, older_than_days: int | None = None) -> dict[str, Any]:
        params = {"older_than_days": older_than_days} if older_than_days is not None else None
        return self._request("POST", "/admin/audit-events/cleanup", params=params)

    def get_schema_migrations(self) -> list[dict[str, Any]]:
        return self._request("GET", "/admin/system/migrations")

    def get_system_profile(self) -> dict[str, Any]:
        return self._request("GET", "/admin/system/profile")

    def get_system_readiness(self) -> dict[str, Any]:
        return self._request("GET", "/admin/system/readiness")

    def get_onboarding_status(
        self,
        company_id: int | None = None,
        company_slug: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if company_id is not None:
            params["company_id"] = company_id
        if company_slug:
            params["company_slug"] = company_slug
        return self._request("GET", "/admin/system/onboarding", params=params or None)

    def get_calendar_providers(self) -> list[dict[str, Any]]:
        return self._request("GET", "/admin/calendar/providers")

    def get_calendar_profile(self, company_id: int | None = None) -> dict[str, Any]:
        params = {"company_id": company_id} if company_id is not None else None
        return self._request("GET", "/admin/calendar/profile", params=params)

    def get_calendar_integrations(self, company_id: int | None = None) -> list[dict[str, Any]]:
        params = {"company_id": company_id} if company_id is not None else None
        return self._request("GET", "/admin/calendar/integrations", params=params)

    def create_calendar_integration(
        self,
        provider: str,
        config: dict[str, Any],
        company_id: int | None = None,
        external_calendar_id: str | None = None,
        active: bool = True,
    ) -> dict[str, Any]:
        params = {"company_id": company_id} if company_id is not None else None
        return self._request(
            "POST",
            "/admin/calendar/integrations",
            params=params,
            json_body={
                "provider": provider,
                "external_calendar_id": external_calendar_id,
                "config": config,
                "active": active,
            },
        )

    def query_calendar_availability(
        self,
        start_date: str,
        end_date: str,
        duration_minutes: int,
        limit: int = 12,
        company_id: int | None = None,
    ) -> dict[str, Any]:
        params = {"company_id": company_id} if company_id is not None else None
        return self._request(
            "POST",
            "/admin/calendar/availability/query",
            params=params,
            json_body={
                "start_date": start_date,
                "end_date": end_date,
                "duration_minutes": duration_minutes,
                "limit": limit,
            },
        )

    def get_calendar_bookings(self, company_id: int | None = None) -> list[dict[str, Any]]:
        params = {"company_id": company_id} if company_id is not None else None
        return self._request("GET", "/admin/calendar/bookings", params=params)

    def create_calendar_booking(
        self,
        title: str,
        starts_at: str,
        ends_at: str,
        company_id: int | None = None,
        contact_id: str | None = None,
        timezone: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        params = {"company_id": company_id} if company_id is not None else None
        return self._request(
            "POST",
            "/admin/calendar/bookings",
            params=params,
            json_body={
                "contact_id": contact_id,
                "title": title,
                "starts_at": starts_at,
                "ends_at": ends_at,
                "timezone": timezone,
                "description": description,
            },
        )

    def get_customer_reminders(
        self,
        company_id: int | None = None,
        status: str | None = None,
        audience: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if company_id is not None:
            params["company_id"] = company_id
        if status:
            params["status"] = status
        if audience:
            params["audience"] = audience
        return self._request("GET", "/admin/reminders", params=params or None)

    def create_internal_reminder(
        self,
        owner_contact_id: str,
        message_text: str,
        company_id: int | None = None,
        timezone: str | None = None,
    ) -> dict[str, Any]:
        params = {"company_id": company_id} if company_id is not None else None
        return self._request(
            "POST",
            "/admin/reminders",
            params=params,
            json_body={
                "owner_contact_id": owner_contact_id,
                "message_text": message_text,
                "timezone": timezone,
            },
        )

    def dispatch_due_reminders(
        self,
        dispatch_immediately: bool = False,
        channel: str | None = None,
        audience: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"dispatch_immediately": dispatch_immediately}
        if channel:
            params["channel"] = channel
        if audience:
            params["audience"] = audience
        return self._request("POST", "/admin/reminders/dispatch-due", params=params)

    def get_finetuning_jobs(self, limit: int = 10) -> list[dict[str, Any]]:
        return self._request("GET", "/admin/finetuning/jobs", params={"limit": limit})

    def create_finetuning_job(self) -> dict[str, Any]:
        return self._request("POST", "/admin/finetuning/jobs")

    def get_finetuning_train_jsonl(
        self,
        company_id: int | None = None,
    ) -> dict[str, Any]:
        params = {"company_id": company_id} if company_id is not None else None
        return self._request("GET", "/admin/finetuning-dataset/train-jsonl", params=params)

    def get_finetuning_validation_jsonl(
        self,
        company_id: int | None = None,
    ) -> dict[str, Any]:
        params = {"company_id": company_id} if company_id is not None else None
        return self._request("GET", "/admin/finetuning-dataset/validation-jsonl", params=params)
