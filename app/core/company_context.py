import json
from pathlib import Path
from typing import Any
import re


DATA_DIR = Path(__file__).resolve().parent.parent / "data"
COMPANY_CONTEXT_PATH = DATA_DIR / "company_context.json"
COMPANY_CONTEXTS_DIR = DATA_DIR / "company_contexts"


class CompanyContextError(Exception):
    pass


REQUIRED_TOP_LEVEL_KEYS = {
    "company_name",
    "brand_tone",
    "enabled_modules",
    "business_identity",
    "contact_channels",
    "business_hours",
    "response_policy",
    "commercial_policy",
    "catalog",
    "customer_intake",
    "business_rules",
    "llm_guidelines",
}


def _normalize_company_slug(company_slug: str) -> str:
    normalized = re.sub(r"[^a-z0-9_-]+", "-", company_slug.strip().lower())
    normalized = normalized.strip("-")
    if not normalized:
        raise CompanyContextError("company_slug must resolve to a non-empty normalized value.")
    return normalized



def _get_company_context_path(company_slug: str | None = None) -> Path:
    if not company_slug:
        return COMPANY_CONTEXT_PATH

    normalized_slug = _normalize_company_slug(company_slug)
    return COMPANY_CONTEXTS_DIR / f"{normalized_slug}.json"


def _validate_company_context(data: dict[str, Any]) -> None:
    missing_keys = sorted(REQUIRED_TOP_LEVEL_KEYS - set(data.keys()))
    if missing_keys:
        raise CompanyContextError(
            f"Company context is missing required keys: {', '.join(missing_keys)}"
        )

    if not isinstance(data.get("company_name"), str) or not data["company_name"].strip():
        raise CompanyContextError("company_name must be a non-empty string.")

    if not isinstance(data.get("business_identity"), dict):
        raise CompanyContextError("business_identity must be an object.")

    if not isinstance(data.get("catalog"), dict):
        raise CompanyContextError("catalog must be an object.")

    catalog_items = data["catalog"].get("items")
    if not isinstance(catalog_items, list):
        raise CompanyContextError("catalog.items must be a list.")

    if not isinstance(data.get("business_rules"), list):
        raise CompanyContextError("business_rules must be a list.")

    if not isinstance(data.get("enabled_modules"), dict):
        raise CompanyContextError("enabled_modules must be an object.")

    scheduling_policy = data.get("scheduling_policy")
    if scheduling_policy is not None and not isinstance(scheduling_policy, dict):
        raise CompanyContextError("scheduling_policy must be an object when provided.")

    profiles = scheduling_policy.get("profiles") if isinstance(scheduling_policy, dict) else None
    if profiles is not None:
        if not isinstance(profiles, list):
            raise CompanyContextError("scheduling_policy.profiles must be a list when provided.")
        profile_keys: set[str] = set()
        for index, profile in enumerate(profiles):
            if not isinstance(profile, dict):
                raise CompanyContextError(f"scheduling_policy.profiles[{index}] must be an object.")
            profile_key = str(profile.get("key") or "").strip()
            if not profile_key:
                raise CompanyContextError(
                    f"scheduling_policy.profiles[{index}].key must be a non-empty string."
                )
            if profile_key in profile_keys:
                raise CompanyContextError(
                    f"scheduling_policy.profiles contains duplicate key: {profile_key}"
                )
            profile_keys.add(profile_key)

    conversation_pipelines = data.get("conversation_pipelines")
    if conversation_pipelines is not None:
        if not isinstance(conversation_pipelines, list):
            raise CompanyContextError("conversation_pipelines must be a list when provided.")

        pipeline_keys: set[str] = set()
        for index, pipeline in enumerate(conversation_pipelines):
            if not isinstance(pipeline, dict):
                raise CompanyContextError(f"conversation_pipelines[{index}] must be an object.")

            pipeline_key = str(pipeline.get("key") or "").strip()
            if not pipeline_key:
                raise CompanyContextError(f"conversation_pipelines[{index}].key must be a non-empty string.")
            if pipeline_key in pipeline_keys:
                raise CompanyContextError(f"conversation_pipelines contains duplicate key: {pipeline_key}")
            pipeline_keys.add(pipeline_key)

            label = pipeline.get("label")
            if label is not None and (not isinstance(label, str) or not label.strip()):
                raise CompanyContextError(f"conversation_pipelines[{index}].label must be a non-empty string when provided.")

            trigger_terms = pipeline.get("trigger_terms")
            if trigger_terms is not None and not isinstance(trigger_terms, list):
                raise CompanyContextError(f"conversation_pipelines[{index}].trigger_terms must be a list when provided.")

            next_keys = pipeline.get("next_keys")
            if next_keys is not None and not isinstance(next_keys, list):
                raise CompanyContextError(f"conversation_pipelines[{index}].next_keys must be a list when provided.")

            canvas_position = pipeline.get("canvas_position")
            if canvas_position is not None:
                if not isinstance(canvas_position, dict):
                    raise CompanyContextError(f"conversation_pipelines[{index}].canvas_position must be an object when provided.")
                x = canvas_position.get("x")
                y = canvas_position.get("y")
                if x is not None and not isinstance(x, (int, float)):
                    raise CompanyContextError(f"conversation_pipelines[{index}].canvas_position.x must be numeric when provided.")
                if y is not None and not isinstance(y, (int, float)):
                    raise CompanyContextError(f"conversation_pipelines[{index}].canvas_position.y must be numeric when provided.")

        for index, pipeline in enumerate(conversation_pipelines):
            next_keys = pipeline.get("next_keys") or []
            for next_key in next_keys:
                normalized_next_key = str(next_key or "").strip()
                if normalized_next_key and normalized_next_key not in pipeline_keys:
                    raise CompanyContextError(
                        f"conversation_pipelines[{index}].next_keys references unknown key: {normalized_next_key}"
                    )


def get_company_context(company_slug: str | None = None) -> dict[str, Any]:
    context_path = _get_company_context_path(company_slug)

    if not context_path.exists() and company_slug:
        context_path = COMPANY_CONTEXT_PATH

    with context_path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    _validate_company_context(data)
    return data


def get_company_context_status(company_slug: str | None = None) -> dict[str, Any]:
    requested_path = _get_company_context_path(company_slug)
    fallback_used = False
    resolved_path = requested_path

    if company_slug and not requested_path.exists():
        resolved_path = COMPANY_CONTEXT_PATH
        fallback_used = True

    context_exists = resolved_path.exists()
    status = {
        "company_slug": company_slug,
        "requested_path": str(requested_path),
        "resolved_path": str(resolved_path),
        "exists": context_exists,
        "fallback_used": fallback_used,
        "is_valid": False,
        "top_level_keys": [],
        "error": None,
    }

    if not context_exists:
        status["error"] = "Company context file does not exist."
        return status

    try:
        context = get_company_context(company_slug=company_slug)
    except (CompanyContextError, json.JSONDecodeError, FileNotFoundError) as exc:
        status["error"] = str(exc)
        return status

    status["is_valid"] = True
    status["top_level_keys"] = sorted(context.keys())
    return status


def save_company_context(
    data: dict[str, Any],
    company_slug: str | None = None,
) -> dict[str, Any]:
    _validate_company_context(data)

    context_path = _get_company_context_path(company_slug)
    context_path.parent.mkdir(parents=True, exist_ok=True)

    with context_path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
        file.write("\n")

    return data
