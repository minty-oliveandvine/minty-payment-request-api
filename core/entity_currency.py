"""The entity's selected currency (entities.currency_id -> currency_info)."""

from django.db.models import Subquery

from core.exceptions import BillValidationError

NO_CURRENCY_MESSAGE = (
    "Your company has no currency set. Choose one in Entity & Integration settings."
)


DEFAULT_DECIMAL_PLACES = 2


def entity_currency(entity_id: str) -> tuple[str, int]:
    """(ISO code, decimal places) of the entity's currency; ("", 2) when it has none."""
    from bills.models import CurrencyInfo
    from shared_models.models import Entity

    if not entity_id:
        return "", DEFAULT_DECIMAL_PLACES
    row = (
        CurrencyInfo.objects.filter(
            id=Subquery(Entity.objects.filter(id=str(entity_id)).values("currency_id")[:1])
        )
        .values_list("currency_code", "decimal_places")
        .first()
    )
    if not row:
        return "", DEFAULT_DECIMAL_PLACES
    code, places = row
    return (code or "").strip(), DEFAULT_DECIMAL_PLACES if places is None else int(places)


def entity_currency_code(entity_id: str) -> str:
    """ISO code of the entity's currency, or "" when it has none."""
    return entity_currency(entity_id)[0]


def require_entity_currency_code(entity_id: str) -> str:
    """The entity's ISO code; refuse (422) when the entity has none set."""
    code = entity_currency_code(entity_id)
    if not code:
        raise BillValidationError(NO_CURRENCY_MESSAGE)
    return code
