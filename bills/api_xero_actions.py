import logging

from django.http import Http404
from ninja import Router, Schema

from bills.models import Bill, Payment
from bills.services.xero_publish_service import upload_bankslip_to_xero
from bills.services.xero_token_service import resolve_xero_access_token_for_entity
from core.permissions import check_not_system_superuser, check_publish_xero

logger = logging.getLogger("minty-api")


class BankslipUploadIn(Schema):
    bill_id: str
    payment_id: str


class BankslipUploadOut(Schema):
    uploaded: int
    sync_id: str
    message: str


class ErrorOut(Schema):
    detail: str


xero_actions_router = Router()


@xero_actions_router.post(
    "/upload-bankslip",
    response={200: BankslipUploadOut, 404: ErrorOut, 422: ErrorOut},
    summary="Upload payment bank-slip attachments to Xero invoice",
)
def upload_bankslip_endpoint(request, payload: BankslipUploadIn):
    """Download bank-slip from S3 and upload to the published Xero invoice."""

    check_not_system_superuser(request, "upload bank slips")
    check_publish_xero(request.entity_role)

    try:
        Bill.objects.get(id=payload.bill_id, entity_id=request.entity_id)
    except Bill.DoesNotExist:
        raise Http404("Bill not found")

    try:
        Payment.objects.get(id=payload.payment_id, bill_id=payload.bill_id)
    except Payment.DoesNotExist:
        raise Http404("Payment not found")

    access_token = (
        resolve_xero_access_token_for_entity(
            request.entity_id,
            str(request.auth_user.id),
        )
        or ""
    )
    result = upload_bankslip_to_xero(
        bill_id=payload.bill_id,
        payment_id=payload.payment_id,
        entity_id=request.entity_id,
        user_id=str(request.auth_user.id),
        access_token=access_token,
    )

    return {
        "uploaded": result["uploaded"],
        "sync_id": result["sync_id"],
        "message": f"{result['uploaded']} bank slip(s) uploaded to Xero",
    }
