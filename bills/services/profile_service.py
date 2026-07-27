import logging

from django.db import transaction

from shared_models.models import User

logger = logging.getLogger("minty-api")


@transaction.atomic
def update_user_profile(
    user_id: str, email: str, first_name: str, last_name: str
) -> User:
    """
    Update user profile fields (email, first_name, last_name).
    Returns the updated User instance.
    """
    logger.info("Updating user profile user_id=%s email=%s", user_id, email)

    try:
        user = User.objects.get(id=user_id)

        user.email = email.strip()
        user.first_name = first_name.strip()
        user.last_name = last_name.strip()
        user.save()

        logger.info("User profile updated successfully user_id=%s", user_id)
        return user

    except User.DoesNotExist:
        logger.error("User not found user_id=%s", user_id)
        raise
    except Exception as e:
        logger.error(
            "Failed to update user profile user_id=%s error=%s", user_id, str(e)
        )
        raise
