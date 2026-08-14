"""Module entitlement resolver — Django mirror of Flask's ``_is_module_enabled``.

Single source for "is module X enabled for entity Y" so every consumer
(token refresh, future per-route guards) agrees. Reads the same
``entity_function`` / ``entity_function_map`` tables Flask seeds and
backfills, applying the same fallback semantics: explicit map row wins,
otherwise the catalog row's ``is_active`` decides, otherwise default to
True (legacy entities that predate the gating table).
"""

from __future__ import annotations

from bills.models import EntityFunction, EntityFunctionMap

# Canonical codes — keep in sync with Flask's blueprints/entity/services/modules.py
# and the catalog seeded by migration b8f3a2c1d4e5.
MODULE_PETTY_CASH = "PETTY_CASH"
MODULE_BILL = "BILL"


def is_module_enabled(entity_id: str, function_code: str) -> bool:
    """Return True iff this module is enabled for this entity.

    Mirrors Flask's ``_is_module_enabled`` (blueprints/entity/routes/modules.py),
    and the mirror had drifted: EVERY UNKNOWN ANSWERS NO.

    ``is_enabled`` on the map row is not a fact of its own — it is a projection of
    the module's ``entity_module_subscription``, which the subscription lifecycle
    writes. So a map row is the only thing that ever grants a module, and its
    absence means nothing has granted it.

    This used to fall through to the catalog's ``is_active``, which Flask deleted
    for a reason worth repeating: ``is_active`` says whether a module is OFFERED at
    all, never who may use it, so falling back to it handed the module to every
    entity that had never subscribed. Keeping that fallback here meant the two
    apps disagreed about the same entity — Minty denying a module while
    ``/auth/entitlements`` granted it — and the frontend trusts this one over the
    signed token, so Django's answer won.
    """
    if not entity_id or not function_code:
        return False

    catalog = (
        EntityFunction.objects.filter(function_code=function_code)
        .only("id", "is_active")
        .first()
    )
    if catalog is None:
        return False

    mapping = (
        EntityFunctionMap.objects.filter(
            entity_id=entity_id, entity_function_id=catalog.id
        )
        .only("is_enabled")
        .first()
    )
    if mapping is not None:
        return bool(mapping.is_enabled)

    return False


def get_module_claims(entity_id: str) -> dict:
    """Return the JWT-ready claim shape for both modules.

    Used by ``token_refresh`` so the frontend keeps the same module-visibility
    signals across the 8-hour refresh cycle as it had in the initial handoff,
    and by ``/auth/entitlements`` as the DB-fresh answer the frontend trusts
    over its own cookie.

    NO COMPANY MEANS NO MODULES. Stated here as well as in ``is_module_enabled``
    because this is the one that gets PUBLISHED: the frontend trusts this answer
    over the signed token's own claims, so anything optimistic here silently
    overrides what Minty decided.

    That is not hypothetical. When an empty entity answered True, a caller with
    no role reached here with no company context and was told both modules were
    enabled — which reopened a module gate that was correctly shut and dropped
    them on a page where every call failed with Unauthorized.
    """
    if not entity_id:
        return {"billing_enabled": False, "petty_cash_enabled": False}
    return {
        "billing_enabled": is_module_enabled(entity_id, MODULE_BILL),
        "petty_cash_enabled": is_module_enabled(entity_id, MODULE_PETTY_CASH),
    }
