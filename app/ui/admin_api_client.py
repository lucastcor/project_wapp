from typing import Any

import requests


class AdminApiClientError(Exception):
    pass


class AdminApiClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

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
        try:
            response = requests.request(
                method=method,
                url=self._build_url(path),
                params=params,
                json=json_body,
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
