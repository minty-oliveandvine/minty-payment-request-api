from django.urls import path
from ninja import NinjaAPI

from core.api_session import session_router
from core.auth import BearerAuth
from core.exceptions import register_exception_handlers

api = NinjaAPI(
    title="Minty Billing API",
    version="1.0.0",
    description="Module 2 — Bill, Payment & Configuration Management",
    auth=BearerAuth(),
)

register_exception_handlers(api)

from bills.api import attachments_router, bills_router  # noqa: E402
from bills.api_audit import audit_router  # noqa: E402
from bills.api_config import (  # noqa: E402
    currencies_router,
    entities_router,
    entity_bill_accounts_router,
    entity_bill_contacts_router,
    entity_bill_currencies_router,
    entity_function_maps_router,
    entity_functions_router,
)
from bills.api_payments import payment_attachments_router, payments_router  # noqa: E402
from bills.api_profile import profile_router  # noqa: E402
from bills.api_xero import xero_syncs_router  # noqa: E402
from bills.api_xero_actions import xero_actions_router  # noqa: E402

api.add_router("/auth", session_router, tags=["Session"])
api.add_router("/profile", profile_router, tags=["Profile"])
api.add_router("/bills", bills_router, tags=["Bills"])
api.add_router("/bills", audit_router, tags=["Audit"])
api.add_router("/bills", attachments_router, tags=["Bill Attachments"])
api.add_router("/bills", payments_router, tags=["Payments"])
api.add_router("/bills", payment_attachments_router, tags=["Payment Attachments"])
api.add_router("/bills", xero_syncs_router, tags=["Xero Bill Syncs"])
api.add_router("/xero", xero_actions_router, tags=["Xero Actions"])
api.add_router("/entities", entities_router, tags=["Entities"])
api.add_router("/entity-functions", entity_functions_router, tags=["Entity Functions"])
api.add_router(
    "/entity-function-maps", entity_function_maps_router, tags=["Entity Function Maps"]
)
api.add_router(
    "/entity-bill-accounts", entity_bill_accounts_router, tags=["Entity Bill Accounts"]
)
api.add_router(
    "/entity-bill-contacts", entity_bill_contacts_router, tags=["Entity Bill Contacts"]
)
api.add_router("/currencies", currencies_router, tags=["Currencies"])
api.add_router(
    "/entity-bill-currencies",
    entity_bill_currencies_router,
    tags=["Entity Bill Currencies"],
)

urlpatterns = [
    path("api/v1/", api.urls),
]
