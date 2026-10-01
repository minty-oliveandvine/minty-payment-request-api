import logging

from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import Http404
from ninja import Query, Router, Schema

from bills.models import (
    CurrencyInfo,
    EntityBillAccountXero,
    EntityBillCurrency,
    EntityFunction,
    EntityFunctionMap,
)
from bills.schemas import (
    CurrencyInfoCreateIn,
    CurrencyInfoOut,
    CurrencyInfoUpdateIn,
    EntityBillAccountXeroCreateIn,
    EntityBillAccountXeroOut,
    EntityBillAccountXeroUpdateIn,
    EntityBillContactCreateIn,
    EntityBillContactOut,
    EntityBillCurrencyCreateIn,
    EntityBillCurrencyOut,
    EntityBillCurrencyUpdateIn,
    EntityFunctionCreateIn,
    EntityFunctionMapCreateIn,
    EntityFunctionMapOut,
    EntityFunctionMapUpdateIn,
    EntityFunctionNameOut,
    EntityFunctionOut,
    EntityFunctionUpdateIn,
    ErrorOut,
    MessageOut,
)
from core.exceptions import PermissionDeniedError
from core.permissions import (
    ALL_BILL_ROLES,
    check_edit_bill_settings,
    check_not_system_superuser,
    normalize_role,
)
from shared_models.enums import SystemRole
from shared_models.models import Entity, User, UserEntity

logger = logging.getLogger("minty-api")


def _apply_partial_update(obj, update_data: dict) -> None:
    """Assign each non-None value from a partial-update dict onto ``obj``.

    Shared by the config PUT endpoints. ``update_data`` is the caller's
    ``payload.dict(exclude_unset=True)`` — a field explicitly set to a falsy
    value (0, False, "") is still applied; only ``None`` is skipped, matching
    the original per-endpoint loops exactly. Does not call ``.save()``; the
    caller does that so any surrounding side effects stay put.
    """
    for field, value in update_data.items():
        if value is not None:
            setattr(obj, field, value)


# Xero Account.Type values permitted in the bill settings chart of accounts
# list.
# Human-readable labels → Xero Type:
#   Current Asset        → CURRENT
#   Non-current asset    → NONCURRENT
#   Current Liability    → CURRLIAB
#   Non-current liability→ TERMLIAB
#   Fixed Asset          → FIXED
#   Inventory            → INVENTORY
#   Direct Cost          → DIRECTCOSTS
#   Expense              → EXPENSE
#   Liability            → LIABILITY
#   Overhead             → OVERHEADS
#   Prepayment           → PREPAYMENT
BILL_SETTINGS_ACCOUNT_TYPES = frozenset(
    {
        "DIRECTCOSTS",
        "EXPENSE",
        "FIXED",
        "OVERHEADS",
        "PREPAYMENT",
    }
)

LAST_TICKED_CODE_MESSAGE = "Keep at least one account code ticked."


# ═══════════════════════════════════════════════════════════════════════════
# ENTITY FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════

entity_functions_router = Router()


@entity_functions_router.post(
    "/",
    response={201: EntityFunctionOut},
    summary="Create an entity function",
)
def create_entity_function(request, payload: EntityFunctionCreateIn):
    check_not_system_superuser(request, "modify configuration")
    check_edit_bill_settings(request.entity_role)
    ef = EntityFunction.objects.create(**payload.dict())
    logger.info("EntityFunction created id=%s", ef.id)
    return 201, ef


@entity_functions_router.get(
    "/",
    response=list[EntityFunctionOut],
    summary="List entity functions",
)
def list_entity_functions(request):
    return list(EntityFunction.objects.all().order_by("function_name"))


@entity_functions_router.get(
    "/{function_id}",
    response={200: EntityFunctionOut, 404: ErrorOut},
    summary="Get entity function detail",
)
def get_entity_function(request, function_id: str):
    try:
        return EntityFunction.objects.get(id=function_id)
    except EntityFunction.DoesNotExist:
        raise Http404("Entity function not found")


@entity_functions_router.put(
    "/{function_id}",
    response={200: EntityFunctionOut, 404: ErrorOut},
    summary="Update an entity function",
)
def update_entity_function(request, function_id: str, payload: EntityFunctionUpdateIn):
    check_not_system_superuser(request, "modify configuration")
    check_edit_bill_settings(request.entity_role)
    try:
        ef = EntityFunction.objects.get(id=function_id)
    except EntityFunction.DoesNotExist:
        raise Http404("Entity function not found")

    _apply_partial_update(ef, payload.dict(exclude_unset=True))
    ef.save()
    logger.info("EntityFunction updated id=%s", ef.id)
    return ef


@entity_functions_router.delete(
    "/{function_id}",
    response={200: MessageOut, 404: ErrorOut},
    summary="Delete an entity function",
)
def delete_entity_function(request, function_id: str):
    check_not_system_superuser(request, "modify configuration")
    check_edit_bill_settings(request.entity_role)
    try:
        ef = EntityFunction.objects.get(id=function_id)
    except EntityFunction.DoesNotExist:
        raise Http404("Entity function not found")

    ef.delete()
    logger.info("EntityFunction deleted id=%s", function_id)
    return {"message": "Entity function deleted"}


# ═══════════════════════════════════════════════════════════════════════════
# ENTITY FUNCTION MAPS (entity-scoped)
# ═══════════════════════════════════════════════════════════════════════════

entity_function_maps_router = Router()


@entity_function_maps_router.post(
    "/",
    response={201: EntityFunctionMapOut},
    summary="Create an entity function map",
)
def create_entity_function_map(request, payload: EntityFunctionMapCreateIn):
    check_not_system_superuser(request, "modify configuration")
    check_edit_bill_settings(request.entity_role)
    efm = EntityFunctionMap.objects.create(
        entity_id=request.entity_id,
        entity_function_id=payload.entity_function_id,
        is_enabled=payload.is_enabled,
        settings_json=payload.settings_json,
        created_by=request.auth_user.id,
    )
    logger.info(
        "EntityFunctionMap created entity=%s function=%s", request.entity_id, efm.entity_function_id
    )
    return 201, efm


@entity_function_maps_router.get(
    "/",
    response=list[EntityFunctionMapOut],
    summary="List entity function maps",
)
def list_entity_function_maps(request):
    return list(
        EntityFunctionMap.objects.filter(entity_id=request.entity_id).order_by(
            "-created_at"
        )
    )


@entity_function_maps_router.get(
    "/functions",
    response=list[EntityFunctionNameOut],
    summary="List function names enabled for the entity",
)
def list_entity_function_names(request):
    rows = (
        EntityFunctionMap.objects.filter(entity_id=request.entity_id, is_enabled=True)
        .select_related("entity_function")
        .order_by("entity_function__function_name")
    )
    return [{"function_name": r.entity_function.function_name} for r in rows]


# A map row has no id of its own: it is THE row for (this entity, that function), so the
# detail routes are addressed by the function id (the entity comes from the header).
@entity_function_maps_router.get(
    "/{function_id}",
    response={200: EntityFunctionMapOut, 404: ErrorOut},
    summary="Get entity function map detail",
)
def get_entity_function_map(request, function_id: str):
    try:
        return EntityFunctionMap.objects.get(
            entity_function_id=function_id, entity_id=request.entity_id
        )
    except (EntityFunctionMap.DoesNotExist, ValidationError):
        raise Http404("Entity function map not found")


@entity_function_maps_router.put(
    "/{function_id}",
    response={200: EntityFunctionMapOut, 404: ErrorOut},
    summary="Update an entity function map",
)
def update_entity_function_map(
    request, function_id: str, payload: EntityFunctionMapUpdateIn
):
    check_not_system_superuser(request, "modify configuration")
    check_edit_bill_settings(request.entity_role)
    try:
        efm = EntityFunctionMap.objects.get(
            entity_function_id=function_id, entity_id=request.entity_id
        )
    except (EntityFunctionMap.DoesNotExist, ValidationError):
        raise Http404("Entity function map not found")

    _apply_partial_update(efm, payload.dict(exclude_unset=True))
    efm.save()
    logger.info("EntityFunctionMap updated entity=%s function=%s", request.entity_id, function_id)
    return efm


@entity_function_maps_router.delete(
    "/{function_id}",
    response={200: MessageOut, 404: ErrorOut},
    summary="Delete an entity function map",
)
def delete_entity_function_map(request, function_id: str):
    check_not_system_superuser(request, "modify configuration")
    check_edit_bill_settings(request.entity_role)
    try:
        efm = EntityFunctionMap.objects.get(
            entity_function_id=function_id, entity_id=request.entity_id
        )
    except (EntityFunctionMap.DoesNotExist, ValidationError):
        raise Http404("Entity function map not found")

    efm.delete()
    logger.info("EntityFunctionMap deleted entity=%s function=%s", request.entity_id, function_id)
    return {"message": "Entity function map deleted"}


# ═══════════════════════════════════════════════════════════════════════════
# ENTITY BILL ACCOUNT XERO (entity-scoped)
# ═══════════════════════════════════════════════════════════════════════════

entity_bill_accounts_router = Router()


@entity_bill_accounts_router.post(
    "/",
    response={201: EntityBillAccountXeroOut},
    summary="Create an entity bill account",
)
def create_entity_bill_account(request, payload: EntityBillAccountXeroCreateIn):
    check_not_system_superuser(request, "modify configuration")
    check_edit_bill_settings(request.entity_role)
    account = EntityBillAccountXero.objects.create(
        entity_id=request.entity_id,
        created_by=str(request.auth_user.id),
        **payload.dict(),
    )
    logger.info(
        "EntityBillAccountXero created id=%s entity=%s",
        account.id,
        request.entity_id,
    )
    return 201, account


@entity_bill_accounts_router.get(
    "/",
    response=list[EntityBillAccountXeroOut],
    summary="List entity bill accounts",
)
def list_entity_bill_accounts(
    request,
    account_type: str = None,
    bill_dropdown: bool = Query(False),
    include_deleted: bool = Query(False),
    include_inactive: bool = Query(False),
    sync_chart: bool = Query(True),
    force_chart_sync: bool = Query(False),
):
    # The Bill CoA list is rendered SOLELY from entity_bill_account_xero (the
    # query below) — no live Xero read feeds this response. When the Bill
    # Settings tab opens (include_inactive=true) we still kick off a
    # background, debounced chart-of-accounts sync so the table is refreshed
    # for the next load. The Flask endpoint runs it in a daemon thread and
    # returns immediately, so this render is never blocked on the Xero diff.
    # force_chart_sync bypasses the per-entity debounce for a manual refresh.
    if include_inactive or force_chart_sync:
        from bills.services.flask_billing_sync import trigger_chart_sync_if_changed

        trigger_chart_sync_if_changed(
            request, request.entity_id, force=force_chart_sync
        )

    qs = EntityBillAccountXero.objects.filter(entity_id=request.entity_id)
    if not include_deleted:
        qs = qs.filter(is_deleted=False)
    # Skip the active-only filter when the caller explicitly requests inactive records
    # (e.g. the Bill Settings tab needs to show all accounts so the user can toggle them).
    if not include_inactive:
        qs = qs.filter(is_active=True)
    if account_type:
        # Explicit type filter bypasses the allowlist — caller is responsible for
        # passing valid type values (used e.g. for admin/diagnostic lookups).
        types = [t.strip() for t in account_type.split(",") if t.strip()]
        qs = qs.filter(account_type__in=types)
    else:
        # Both the bill dropdown and the default settings list are restricted to
        # exactly the 8 permitted account types.
        qs = qs.filter(account_type__in=BILL_SETTINGS_ACCOUNT_TYPES)
    return list(qs.order_by("sort_order", "account_code"))


@entity_bill_accounts_router.get(
    "/{account_id}",
    response={200: EntityBillAccountXeroOut, 404: ErrorOut},
    summary="Get entity bill account detail",
)
def get_entity_bill_account(request, account_id: str):
    try:
        return EntityBillAccountXero.objects.get(
            id=account_id, entity_id=request.entity_id
        )
    except EntityBillAccountXero.DoesNotExist:
        raise Http404("Entity bill account not found")


@entity_bill_accounts_router.put(
    "/{account_id}",
    response={200: EntityBillAccountXeroOut, 404: ErrorOut, 409: ErrorOut},
    summary="Update an entity bill account",
)
def update_entity_bill_account(
    request, account_id: str, payload: EntityBillAccountXeroUpdateIn
):
    check_not_system_superuser(request, "modify configuration")
    check_edit_bill_settings(request.entity_role)
    update_data = payload.dict(exclude_unset=True)
    with transaction.atomic():
        # Lock the entity's live rows (in id order, so concurrent saves queue rather
        # than deadlock): two parallel unticks must not each see the other as "still
        # ticked" and leave the company with none.
        live = list(
            EntityBillAccountXero.objects.select_for_update()
            .filter(entity_id=request.entity_id, is_deleted=False)
            .order_by("id")
        )
        wanted = account_id.strip().lower()
        account = next((a for a in live if str(a.id) == wanted), None)
        if account is None:
            raise Http404("Entity bill account not found")

        # At-least-one rule: the Add Payment dialog needs a code to offer, so the
        # last ticked settings-type code cannot be unticked.
        if (
            update_data.get("is_active") is False
            and account.is_active
            and account.account_type in BILL_SETTINGS_ACCOUNT_TYPES
            and not any(
                a.is_active
                and a.account_type in BILL_SETTINGS_ACCOUNT_TYPES
                and a.id != account.id
                for a in live
            )
        ):
            logger.info(
                "EntityBillAccountXero untick refused (last ticked code) id=%s",
                account.id,
            )
            return 409, {"detail": LAST_TICKED_CODE_MESSAGE}

        _apply_partial_update(account, update_data)
        account.save()
    logger.info("EntityBillAccountXero updated id=%s", account.id)

    # Payment Settings writes only its own table: account_info.status belongs to
    # Petty Cash (its publish refuses a non-ACTIVE code), so it is never touched here.
    return account


@entity_bill_accounts_router.delete(
    "/{account_id}",
    response={200: MessageOut, 404: ErrorOut},
    summary="Delete an entity bill account",
)
def delete_entity_bill_account(request, account_id: str):
    check_not_system_superuser(request, "modify configuration")
    check_edit_bill_settings(request.entity_role)
    try:
        account = EntityBillAccountXero.objects.get(
            id=account_id,
            entity_id=request.entity_id,
            is_deleted=False,
        )
    except EntityBillAccountXero.DoesNotExist:
        raise Http404("Entity bill account not found")

    account.is_deleted = True
    account.save(update_fields=["is_deleted"])
    logger.info("EntityBillAccountXero soft-deleted id=%s", account_id)
    return {"message": "Entity bill account deleted"}


# ═══════════════════════════════════════════════════════════════════════════
# ENTITY BILL CONTACTS (live Xero fetch with DB fallback — matches Module 1)
# ═══════════════════════════════════════════════════════════════════════════

entity_bill_contacts_router = Router()


@entity_bill_contacts_router.get(
    "/",
    response=list[EntityBillContactOut],
    summary="List entity bill contacts",
)
def list_entity_bill_contacts(request, category: str = None):
    # Equivalent to Flask's XERO_SETTINGS_VIEW permission (min role: cashier).
    # All recognised bill roles satisfy this; entity membership is already
    # confirmed by BearerAuth.  Roles outside ALL_BILL_ROLES (e.g. a stale
    # or unknown role string) are denied here.
    if normalize_role(request.entity_role) not in ALL_BILL_ROLES:
        raise PermissionDeniedError(
            f"Role '{request.entity_role}' is not permitted to view contacts"
        )

    from bills.services.contact_service import get_entity_bill_contacts

    return get_entity_bill_contacts(
        entity_id=request.entity_id,
        jwt_user_id=str(request.auth_user.id),
        category=category,
    )


@entity_bill_contacts_router.post(
    "/",
    response={201: EntityBillContactOut, 403: ErrorOut, 422: ErrorOut},
    summary="Create a Xero contact and persist to xero_contact_sync",
)
def create_entity_bill_contact(request, payload: EntityBillContactCreateIn):
    check_not_system_superuser(request, "modify configuration")
    if normalize_role(request.entity_role) not in ALL_BILL_ROLES:
        raise PermissionDeniedError(
            f"Role '{request.entity_role}' is not permitted to manage contacts"
        )

    from bills.services.contact_service import create_entity_bill_contact_in_xero

    row = create_entity_bill_contact_in_xero(
        request.entity_id,
        jwt_user_id=str(request.auth_user.id),
        name=payload.name,
    )
    return 201, row


# ═══════════════════════════════════════════════════════════════════════════
# CURRENCY INFO (global)
# ═══════════════════════════════════════════════════════════════════════════

currencies_router = Router()


@currencies_router.post(
    "/",
    response={201: CurrencyInfoOut},
    summary="Create a currency",
)
def create_currency(request, payload: CurrencyInfoCreateIn):
    check_not_system_superuser(request, "modify configuration")
    check_edit_bill_settings(request.entity_role)
    currency = CurrencyInfo.objects.create(**payload.dict())
    logger.info(
        "CurrencyInfo created id=%s code=%s", currency.id, currency.currency_code
    )
    return 201, currency


@currencies_router.get(
    "/",
    response=list[CurrencyInfoOut],
    summary="List currencies",
)
def list_currencies(request):
    return list(CurrencyInfo.objects.all().order_by("currency_code"))


@currencies_router.get(
    "/{currency_id}",
    response={200: CurrencyInfoOut, 404: ErrorOut},
    summary="Get currency detail",
)
def get_currency(request, currency_id: str):
    # ValidationError/ValueError: id is a UUIDField — a malformed path param
    # should read as "not found", not a 500.
    try:
        return CurrencyInfo.objects.get(id=currency_id)
    except (CurrencyInfo.DoesNotExist, ValidationError, ValueError):
        raise Http404("Currency not found")


@currencies_router.put(
    "/{currency_id}",
    response={200: CurrencyInfoOut, 404: ErrorOut},
    summary="Update a currency",
)
def update_currency(request, currency_id: str, payload: CurrencyInfoUpdateIn):
    check_not_system_superuser(request, "modify configuration")
    check_edit_bill_settings(request.entity_role)
    try:
        currency = CurrencyInfo.objects.get(id=currency_id)
    except (CurrencyInfo.DoesNotExist, ValidationError, ValueError):
        raise Http404("Currency not found")

    _apply_partial_update(currency, payload.dict(exclude_unset=True))
    currency.save()
    logger.info("CurrencyInfo updated id=%s", currency.id)
    return currency


@currencies_router.delete(
    "/{currency_id}",
    response={200: MessageOut, 404: ErrorOut},
    summary="Delete a currency",
)
def delete_currency(request, currency_id: str):
    check_not_system_superuser(request, "modify configuration")
    check_edit_bill_settings(request.entity_role)
    try:
        currency = CurrencyInfo.objects.get(id=currency_id)
    except (CurrencyInfo.DoesNotExist, ValidationError, ValueError):
        raise Http404("Currency not found")

    currency.delete()
    logger.info("CurrencyInfo deleted id=%s", currency_id)
    return {"message": "Currency deleted"}


# ═══════════════════════════════════════════════════════════════════════════
# ENTITY BILL CURRENCY (entity-scoped)
# ═══════════════════════════════════════════════════════════════════════════

entity_bill_currencies_router = Router()


@entity_bill_currencies_router.post(
    "/",
    response={201: EntityBillCurrencyOut},
    summary="Create an entity bill currency",
)
def create_entity_bill_currency(request, payload: EntityBillCurrencyCreateIn):
    check_not_system_superuser(request, "modify configuration")
    check_edit_bill_settings(request.entity_role)
    ebc = EntityBillCurrency.objects.create(
        entity_id=request.entity_id,
        currency_info_id=payload.currency_info_id,
        is_default=payload.is_default,
        is_enabled=payload.is_enabled,
        sort_order=payload.sort_order,
        created_by=str(request.auth_user.id),
    )
    logger.info(
        "EntityBillCurrency created id=%s entity=%s",
        ebc.id,
        request.entity_id,
    )
    return 201, ebc


@entity_bill_currencies_router.get(
    "/",
    response=list[EntityBillCurrencyOut],
    summary="List entity bill currencies",
)
def list_entity_bill_currencies(request):
    return list(
        EntityBillCurrency.objects.filter(entity_id=request.entity_id).order_by(
            "sort_order"
        )
    )


@entity_bill_currencies_router.get(
    "/{currency_map_id}",
    response={200: EntityBillCurrencyOut, 404: ErrorOut},
    summary="Get entity bill currency detail",
)
def get_entity_bill_currency(request, currency_map_id: str):
    try:
        return EntityBillCurrency.objects.get(
            id=currency_map_id, entity_id=request.entity_id
        )
    except EntityBillCurrency.DoesNotExist:
        raise Http404("Entity bill currency not found")


@entity_bill_currencies_router.put(
    "/{currency_map_id}",
    response={200: EntityBillCurrencyOut, 404: ErrorOut},
    summary="Update an entity bill currency",
)
def update_entity_bill_currency(
    request, currency_map_id: str, payload: EntityBillCurrencyUpdateIn
):
    check_not_system_superuser(request, "modify configuration")
    check_edit_bill_settings(request.entity_role)
    try:
        ebc = EntityBillCurrency.objects.get(
            id=currency_map_id, entity_id=request.entity_id
        )
    except EntityBillCurrency.DoesNotExist:
        raise Http404("Entity bill currency not found")

    _apply_partial_update(ebc, payload.dict(exclude_unset=True))
    ebc.save()
    logger.info("EntityBillCurrency updated id=%s", ebc.id)
    return ebc


@entity_bill_currencies_router.delete(
    "/{currency_map_id}",
    response={200: MessageOut, 404: ErrorOut},
    summary="Delete an entity bill currency",
)
def delete_entity_bill_currency(request, currency_map_id: str):
    check_not_system_superuser(request, "modify configuration")
    check_edit_bill_settings(request.entity_role)
    try:
        ebc = EntityBillCurrency.objects.get(
            id=currency_map_id, entity_id=request.entity_id
        )
    except EntityBillCurrency.DoesNotExist:
        raise Http404("Entity bill currency not found")

    ebc.delete()
    logger.info("EntityBillCurrency deleted id=%s", currency_map_id)
    return {"message": "Entity bill currency deleted"}


# ═══════════════════════════════════════════════════════════════════════════
# ENTITIES (super-admin aware list)
# ═══════════════════════════════════════════════════════════════════════════

entities_router = Router()


class EntityListItemOut(Schema):
    id: str
    name: str
    can_enter: bool


def _member_entity_ids(user_id) -> set[str]:
    """Ids (as strings) of the companies ``user_id`` holds a membership row on.

    Materialised rather than a subquery: ``user_entity.entity_id`` is a uuid column while
    ``Entity.id`` is still text until C2, and SQLite stores the two spellings differently.
    """
    return {
        str(eid)
        for eid in UserEntity.objects.filter(user_id=user_id).values_list("entity_id", flat=True)
    }



@entities_router.get(
    "/",
    response=list[EntityListItemOut],
    summary="List entities — all for super admin, own only for others",
)
def list_entities(request):
    if request.is_super_admin:
        all_entities = (
            Entity.objects.exclude(status="deleted")
            .order_by("name")
            .values("id", "name")
        )
        # System super admins (system_role='superadmin') can enter any entity for
        # read-only viewing.  Entity-level super_admins without the system role
        # are limited to entities they are explicitly a member of.
        user_is_system_superuser = User.objects.filter(
            id=str(request.auth_user.id), system_role=SystemRole.SUPERADMIN
        ).exists()

        if user_is_system_superuser:
            return [
                {"id": e["id"], "name": e["name"], "can_enter": True}
                for e in all_entities
            ]

        user_entity_ids = _member_entity_ids(str(request.auth_user.id))
        return [
            {"id": e["id"], "name": e["name"], "can_enter": e["id"] in user_entity_ids}
            for e in all_entities
        ]
    else:
        rows = (
            Entity.objects.filter(id__in=_member_entity_ids(str(request.auth_user.id)))
            .exclude(status="deleted")
            .order_by("name")
            .values_list("id", "name")
        )
        return [{"id": row[0], "name": row[1], "can_enter": True} for row in rows]
