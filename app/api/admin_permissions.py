"""Explicit endpoint-to-permission matrix for the administrative API.

Role model (see ``ADMIN_ROLE_HIERARCHY``):

* ``viewer``     — read-only, may not mutate any administrative resource.
* ``operator``   — day-to-day operational writes (agenda, contacts, outbound,
  reminders, curation, analytics outcomes).
* ``superadmin`` — privileged company, onboarding, security and billing
  administration.

Every mutating (POST/PUT/PATCH/DELETE) route under ``/admin`` must have an entry
here; ``tests/test_admin_rbac_matrix.py`` walks the running application and
fails when a route is missing, extra, or wired to a different role than the one
declared below. Authentication remains generic (``require_admin_access``); it
never implies authorization to mutate.
"""

from typing import Final

VIEWER: Final = "viewer"
OPERATOR: Final = "operator"
SUPERADMIN: Final = "superadmin"

# (HTTP method, route path) -> minimum required role.
ADMIN_ENDPOINT_PERMISSIONS: Final[dict[tuple[str, str], str]] = {
    # Security / audit administration
    ("POST", "/admin/audit-events/cleanup"): SUPERADMIN,
    # Company and onboarding administration
    ("POST", "/admin/companies"): SUPERADMIN,
    ("PUT", "/admin/company-context"): SUPERADMIN,
    ("POST", "/admin/onboarding/setup"): SUPERADMIN,
    ("POST", "/admin/calendar/integrations"): SUPERADMIN,
    ("PUT", "/admin/whatsapp/bindings"): SUPERADMIN,
    ("POST", "/admin/whatsapp/bindings/{binding_id}/state"): SUPERADMIN,
    # Billing administration (tenant-owner billing is not implemented yet)
    ("POST", "/admin/billing/trial"): SUPERADMIN,
    ("POST", "/admin/billing/activate"): SUPERADMIN,
    ("POST", "/admin/billing/cancel"): SUPERADMIN,
    ("POST", "/admin/billing/reactivate"): SUPERADMIN,
    ("POST", "/admin/billing/change-plan"): SUPERADMIN,
    ("POST", "/admin/billing/usage/record"): SUPERADMIN,
    ("POST", "/admin/billing/checkout"): SUPERADMIN,
    ("POST", "/admin/billing/portal"): SUPERADMIN,
    # Operational writes
    ("POST", "/admin/calendar/availability/query"): OPERATOR,
    ("POST", "/admin/calendar/queue"): OPERATOR,
    ("POST", "/admin/calendar/bookings"): OPERATOR,
    ("POST", "/admin/calendar/bookings/{booking_id}/reschedule"): OPERATOR,
    ("POST", "/admin/calendar/bookings/{booking_id}/cancel"): OPERATOR,
    ("POST", "/admin/calendar/queue-offers/{offer_id}/respond"): OPERATOR,
    ("POST", "/admin/reminders"): OPERATOR,
    ("POST", "/admin/reminders/dispatch-due"): OPERATOR,
    ("PATCH", "/admin/contacts/{contact_id}"): OPERATOR,
    ("POST", "/admin/pending-questions/{question_id}/answer"): OPERATOR,
    ("POST", "/admin/message-examples"): OPERATOR,
    ("POST", "/admin/training-import"): OPERATOR,
    ("POST", "/admin/outbound-messages/{outbound_message_id}/mark-sent"): OPERATOR,
    ("POST", "/admin/outbound-messages/{outbound_message_id}/dispatch"): OPERATOR,
    ("POST", "/admin/outbound-messages/dispatch-pending"): OPERATOR,
    ("POST", "/admin/finetuning/jobs"): OPERATOR,
    ("POST", "/admin/analytics/bookings/{booking_id}/outcome"): OPERATOR,
    ("POST", "/admin/analytics/bookings/{booking_id}/cancellation-reason"): OPERATOR,
    ("POST", "/admin/simulator/messages"): OPERATOR,
}

MUTATING_HTTP_METHODS: Final[frozenset[str]] = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def get_required_role(method: str, path: str) -> str:
    """Return the role required to call a mutating admin endpoint."""
    try:
        return ADMIN_ENDPOINT_PERMISSIONS[(method.upper(), path)]
    except KeyError as exc:  # pragma: no cover - guarded by the matrix test
        raise KeyError(f"No admin permission declared for {method.upper()} {path}") from exc
