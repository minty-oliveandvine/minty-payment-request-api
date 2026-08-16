import logging

from django.db import connection
from ninja import Router

from bills.schemas import DeactivateAccountOut, ProfileIn, ProfileOut
from bills.services.profile_service import update_user_profile
from core.exceptions import BillValidationError
from core.permissions import check_not_system_superuser

logger = logging.getLogger("minty-api")

profile_router = Router()


def _get_member_entity_ids(user_id: str) -> list[str]:
    """Return the list of entity IDs the user has an explicit UserEntity row for."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT entity_id FROM user_entity WHERE user_id = %s",
            [user_id],
        )
        return [str(row[0]) for row in cursor.fetchall()]


@profile_router.get("/me", response=ProfileOut)
def get_profile_endpoint(request):
    """
    Get current user's profile information including view-only status.

    `member_entity_ids` lists the entity IDs the caller has an explicit
    UserEntity membership for.  Frontend uses this to decide whether to render
    read-only UI when a system superuser is viewing an entity they are not a
    member of.
    """
    user = request.auth_user
    member_entity_ids = _get_member_entity_ids(str(user.id))

    return {
        "id": user.id,
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "username": user.username,
        "is_view_only": getattr(request, "is_system_superuser", False),
        "member_entity_ids": member_entity_ids,
    }


def _entities_paid_for_by(user_id: str) -> list[tuple[str, str]]:
    """(entity_id, entity_name) for every company whose bill sits on this user's card.

    One row per module and one payer per company across them all, so DISTINCT collapses
    a bundled company to a single answer rather than naming it twice.

    This is the "check them one by one" the rule describes, asked once. Someone in ten
    companies who pays for three gets those three back; someone who pays for none gets an
    empty list and is free to go.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT DISTINCT s.entity_id, e.name "
            "FROM entity_module_subscription s "
            "JOIN entities e ON e.id = s.entity_id "
            "WHERE s.payer_user_id = %s "
            "ORDER BY e.name",
            [user_id],
        )
        return [(str(row[0]), row[1] or "") for row in cursor.fetchall()]


@profile_router.delete("/me", response=DeactivateAccountOut)
def deactivate_account_endpoint(request):
    """Sign the caller out of Minty for good — the account, not one company.

    DEACTIVATES, never deletes. Reports, bills and payments are attributed to the person
    who made them, so removing the row would orphan every record they ever touched. The
    account is switched off and the history stays readable — the same policy Module 1
    states at ``deactivate_my_account``, which is this endpoint's sibling.

    ONE GUARD, and it spans every company they belong to: if their card pays for any of
    them, they cannot go. The payer is recorded on the subscription rows, not on the
    membership, so switching the account off does not move it — it strands it. What that
    leaves is unrecoverable from inside the app: every remaining admin is refused because
    a payer exists and is not them, the payer can no longer sign in to fix it, and the
    renewals keep charging the card.

    Being the last admin somewhere is NOT checked. That is deliberate and worth
    revisiting: it would be consistent with the per-company rule, but applied to a whole
    account it can trap someone who is the sole admin of a company where nobody else can
    be promoted.
    """
    check_not_system_superuser(request, "deactivate accounts")

    user_id = str(request.auth_user.id)
    owed = _entities_paid_for_by(user_id)
    if owed:
        names = ", ".join(name or entity_id for entity_id, name in owed)
        raise BillValidationError(
            f"Billing for {names} is on your card, so you can't sign out yet. Hand each "
            "one to another admin first — until then, switching your account off would "
            "leave those companies being charged with nobody able to stop it."
        )

    with connection.cursor() as cursor:
        # approved=False is what actually closes the door: sign-in checks it. The tokens
        # go with it so nothing keeps acting as them in the background, and the sign-in
        # stamp is cleared so they drop off every company's signed-in list at once.
        cursor.execute(
            'UPDATE "user" SET approved = %s, access_token = NULL, refresh_token = NULL, '
            "id_token = NULL, expires_in = NULL, signed_in_at = NULL "
            "WHERE id = %s",
            [False, user_id],
        )

    logger.info("Account deactivated by its owner user_id=%s", user_id)
    return DeactivateAccountOut(detail="account deactivated")


@profile_router.put("/me", response=ProfileOut)
def update_profile_endpoint(request, payload: ProfileIn):
    """
    Update current user's profile (email, first_name, last_name).
    """
    check_not_system_superuser(request, "update profile")
    user_id = request.auth_user.id

    logger.info("Profile update requested user_id=%s", user_id)

    user = update_user_profile(
        user_id=user_id,
        email=payload.email,
        first_name=payload.first_name,
        last_name=payload.last_name,
    )
    member_entity_ids = _get_member_entity_ids(str(user.id))

    return {
        "id": user.id,
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "username": user.username,
        "is_view_only": getattr(request, "is_system_superuser", False),
        "member_entity_ids": member_entity_ids,
    }
