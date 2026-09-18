import logging

from django.core.exceptions import ValidationError
from django.http import Http404
from ninja import Router

from bills.models import Audit, Bill
from bills.schemas import AuditOut, ErrorOut
from shared_models.models import User

logger = logging.getLogger("minty-api")

audit_router = Router()


def _get_bill_or_404(bill_id: str, entity_id: str) -> Bill:
    try:
        return Bill.objects.get(id=bill_id, entity_id=entity_id)
    except (Bill.DoesNotExist, ValidationError, ValueError):  # a malformed id is not found either
        raise Http404("Bill not found")


@audit_router.get(
    "/{bill_id}/audit",
    response={200: list[AuditOut], 404: ErrorOut},
    summary="Get audit history for a bill",
)
def get_audit_history(request, bill_id: str):
    bill = _get_bill_or_404(bill_id, request.entity_id)
    audits = list(Audit.objects.filter(bill=bill).order_by("-created_at"))

    user_ids = {a.user_id for a in audits}
    users = {str(u.id): u for u in User.objects.filter(id__in=user_ids)}

    result = []
    for a in audits:
        user = users.get(a.user_id)
        if user:
            first = user.first_name or ""
            last = user.last_name or ""
            user_name = f"{first} {last}".strip()
            user_email = user.email or ""
        else:
            user_name = ""
            user_email = ""

        result.append(
            {
                "id": str(a.id),
                "bill_id": str(a.bill_id),
                "action": a.action,
                "detail": a.detail,
                "date": a.created_at,
                "user_id": str(a.user_id) if a.user_id else "",
                "user_name": user_name,
                "user_email": user_email,
            }
        )
    return result
