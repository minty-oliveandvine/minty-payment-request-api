import logging

from bills.models import Audit, Bill

logger = logging.getLogger("minty-api")


def log_audit(bill: Bill, action: str, user_id: str, detail: str = ""):
    """Write an audit record to the database. Call inside a transaction.atomic() block."""
    Audit.objects.create(
        bill=bill,
        action=action,
        user_id=user_id,
        detail=detail,
    )
    logger.info(
        "Audit: bill=%s action=%s user=%s detail=%s", bill.id, action, user_id, detail
    )
