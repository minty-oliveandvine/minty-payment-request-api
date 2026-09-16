"""
Role-based permission matrix for Bill operations.

Roles (per entity, from user_entity.role):
    cashier, shop_manager, accountant, admin, super_admin

Matrix:
    ┌────────────────────────────────────┬───────────────────────────────────────────┐
    │ Action                             │ Allowed Roles                             │
    ├────────────────────────────────────┼───────────────────────────────────────────┤
    │ Create Bill                        │ All bill roles                            │
    │ Edit Bill (Draft / Submitted)      │ All bill roles                            │
    │ Edit Bill (Paid / Partially paid)  │ Accountant, Admin, Super Admin            │
    │ Change paid-related status         │ Accountant, Admin, Super Admin            │
    │ Delete Bill (Draft/Submitted)      │ All bill roles                            │
    │ Delete Bill (Paid / Partially paid)│ Accountant, Admin, Super Admin            │
    │ Publish / Republish to Xero        │ Accountant, Admin, Super Admin            │
    │ Delete payment (any bill status)   │ Accountant, Admin, Super Admin            │
    │ Edit Bill Settings                 │ Accountant, Admin, Super Admin            │
    └────────────────────────────────────┴───────────────────────────────────────────┘
"""

from core.exceptions import PermissionDeniedError

ELEVATED_ROLES = frozenset({"accountant", "admin", "super_admin"})
ALL_BILL_ROLES = frozenset(
    {"cashier", "shop_manager", "accountant", "admin", "super_admin"}
)
PAID_LIKE_STATUSES = frozenset({"paid", "partially_paid"})


def normalize_role(role: str) -> str:
    """Lowercase entity role for matrix checks; spaces and hyphens → underscores."""
    return (role or "").strip().lower().replace(" ", "_").replace("-", "_")


def _require_elevated(role: str, message: str):
    """Raise PermissionDeniedError with `message` unless `role` is elevated.

    The shared gate behind every "Accountant, Admin, or Super Admin only"
    action. The message is passed in rather than derived so each endpoint keeps
    its own exact user-facing wording.
    """
    if normalize_role(role) not in ELEVATED_ROLES:
        raise PermissionDeniedError(message)


def _check_bill_action(role: str, bill_status: str, elevated_message: str, verb: str):
    """Shared gate for status-dependent bill actions (edit / delete).

    Paid-like bills are restricted to elevated roles; every other status is open
    to all bill roles. `verb` fills the generic denial, `elevated_message` is the
    paid-like denial — both kept as caller-supplied strings so the rendered text
    is unchanged.
    """
    normalized = normalize_role(role)
    if bill_status in PAID_LIKE_STATUSES:
        if normalized not in ELEVATED_ROLES:
            raise PermissionDeniedError(elevated_message)
    elif normalized not in ALL_BILL_ROLES:
        raise PermissionDeniedError(f"Role '{role}' is not allowed to {verb} bills")


def check_create_bill(role: str):
    if normalize_role(role) not in ALL_BILL_ROLES:
        raise PermissionDeniedError(f"Role '{role}' is not allowed to create bills")


def check_edit_bill(role: str, bill_status: str):
    _check_bill_action(
        role,
        bill_status,
        "Only Accountant, Admin, or Super Admin can edit paid or partially paid bills",
        "edit",
    )


def check_delete_bill(role: str, bill_status: str):
    _check_bill_action(
        role,
        bill_status,
        "Only Accountant, Admin, or Super Admin can delete paid or partially paid bills",
        "delete",
    )


def check_mark_paid(role: str):
    _require_elevated(
        role, "Only Accountant, Admin, or Super Admin can change paid status"
    )


def check_publish_xero(role: str):
    _require_elevated(
        role, "Only Accountant, Admin, or Super Admin can publish to Xero"
    )


def check_return_bill(role: str):
    _require_elevated(
        role,
        "Only Accountant, Admin, or Super Admin can return or void a payment request.",
    )


def check_edit_bill_settings(role: str):
    """Block bill-settings writes for non-elevated roles (Cashier, Shop Manager).

    Mirrors the existing cashier-exclusion gate used by check_publish_xero,
    check_mark_paid, etc.: ELEVATED_ROLES is the canonical "no cashier and no
    shop_manager" set, which is the same logic we want for bill settings.
    """
    _require_elevated(
        role, "Only Accountant, Admin, or Super Admin can edit bill settings"
    )


def check_bill_mutable(bill_status: str):
    """Block mutating operations on terminal-status bills.

    Voided bills are hard-blocked for all roles — callers in the edit endpoints
    invoke this only after confirming status == "voided".

    Paid bills are hard-blocked here for payment create/update operations
    (all roles).  Edit endpoints do NOT call this for paid bills; instead they
    defer to check_edit_bill(), which allows elevated roles (Accountant, Admin,
    Super Admin) to edit paid bills per the permission matrix.
    """
    if bill_status == "voided":
        raise PermissionDeniedError("Cannot edit a voided bill.")
    if bill_status == "paid":
        raise PermissionDeniedError(
            "This bill is fully paid and immutable. No changes are allowed."
        )


def check_not_system_superuser(request, action: str = "modify data") -> None:
    """Raise PermissionDeniedError if the caller is a system superuser without
    explicit membership in the current entity.

    System superusers (system_role='superadmin') have view-only access ONLY for
    entities they are not a member of.  A superuser who is also an explicit
    UserEntity member (e.g. super_admin of this entity) keeps full per-entity
    CRUD and is allowed to mutate.

    Call this as the FIRST line of every write endpoint so cross-entity
    superuser visits get a clean 403 before any business logic runs.
    """
    if getattr(request, "is_system_superuser", False) and not getattr(
        request, "is_entity_member", False
    ):
        raise PermissionDeniedError(
            f"You have view-only access. Superusers cannot {action} "
            "for entities they are not a member of."
        )
