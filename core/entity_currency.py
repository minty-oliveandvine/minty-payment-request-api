"""The entity's selected currency (entities.currency_id -> currency_info)."""

from django.db.models import Subquery

from core.exceptions import BillValidationError

NO_CURRENCY_MESSAGE = (
    "Your company has no currency set. Choose one in Entity & Integration settings."
)


def entity_currency_code(entity_id: str) -> str:
    """ISO code of the entity's currency, or "" when it has none."""
    from bills.models import CurrencyInfo
    from shared_models.models import Entity

    if not entity_id:
        return ""
    code = (
        CurrencyInfo.objects.filter(
            id=Subquery(Entity.objects.filter(id=str(entity_id)).values("currency_id")[:1])
        )
        .values_list("currency_code", flat=True)
        .first()
    )
    return (code or "").strip()


def require_entity_currency_code(entity_id: str) -> str:
    """The entity's ISO code; refuse (422) when the entity has none set."""
    code = entity_currency_code(entity_id)
    if not code:
        raise BillValidationError(NO_CURRENCY_MESSAGE)
    return code
