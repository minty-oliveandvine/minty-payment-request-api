import logging

from django.db import connection
from ninja import Router

from bills.schemas import ProfileIn, ProfileOut
from bills.services.profile_service import update_user_profile
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
