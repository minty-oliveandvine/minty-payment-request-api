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

    Mirrors Flask's _is_module_enabled in blueprints/entity/routes/modules.py.
    Returns True for unknown function codes too — the safe default for
    legacy entities that exist before the catalog was seeded.
    """
    if not entity_id or not function_code:
        return True

    catalog = (
        EntityFunction.objects.filter(function_code=function_code)
        .only("id", "is_active")
        .first()
    )
    if catalog is None:
        return True

    mapping = (
        EntityFunctionMap.objects.filter(
            entity_id=entity_id, entity_function_id=catalog.id
        )
        .only("is_enabled")
        .first()
    )
    if mapping is not None:
        return bool(mapping.is_enabled)

    return bool(catalog.is_active)


def get_module_claims(entity_id: str) -> dict:
    """Return the JWT-ready claim shape for both modules.

    Used by ``token_refresh`` so the frontend keeps the same module-visibility
    signals across the 8-hour refresh cycle as it had in the initial handoff.
    """
    return {
        "billing_enabled": is_module_enabled(entity_id, MODULE_BILL),
        "petty_cash_enabled": is_module_enabled(entity_id, MODULE_PETTY_CASH),
    }
