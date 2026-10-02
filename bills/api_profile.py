import logging

from ninja import Router

from bills.schemas import ProfileOut
from shared_models.models import UserEntity

logger = logging.getLogger("minty-api")

# GET /me only. PUT /me (profile update) and DELETE /me (deactivation) were removed on
# 2026-10-01: their caller, minty-payment-request-web's My Profile page, moved to minty-web, which saves
# the profile through Minty's /api/me/profile. The GET stays for lib/useUserRole.ts
# (is_view_only, member_entity_ids).
profile_router = Router()


def _get_member_entity_ids(user_id: str) -> list[str]:
    """Return the list of entity IDs the user has an explicit UserEntity row for."""
    return [
        str(eid)
        for eid in UserEntity.objects.filter(user_id=user_id).values_list("entity_id", flat=True)
    ]


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
        "id": str(user.id),  # user.id is a uuid.UUID (C1); the wire keeps the string
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "username": user.username,
        "is_view_only": getattr(request, "is_system_superuser", False),
        "member_entity_ids": member_entity_ids,
    }
