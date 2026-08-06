import logging

from django.db import IntegrityError, transaction

from core.exceptions import BillValidationError
from shared_models.models import User

logger = logging.getLogger("minty-api")


def _normalized(value: str | None) -> str:
    return (value or "").strip()


@transaction.atomic
def update_user_profile(
    user_id: str, email: str, first_name: str, last_name: str
) -> User:
    """
    Update user profile fields (email, first_name, last_name).
    Returns the updated User instance.

    The email is the awkward one, because it is not just a contact field — it is half
    of how the account is identified, and the two halves live in different columns:

        * Minty signs users in on ``user.username`` (auth/routes/login.py), and
          registration sets that column FROM the email.
        * Password reset looks the account up by ``user.email``
          (auth/routes/password_reset.py).

    So writing ``email`` alone — which this function used to do — left the account
    signing in under the old address and resetting under the new one. The user could
    not have discovered that until the next time they tried to log in.

    Hence: when ``username`` currently holds the old email, it moves with it. When it
    holds something else, it is a login handle the user chose separately and is left
    alone — changing it would break a working sign-in to fix a problem that isn't there.

    Both columns are UNIQUE in the database, so a collision would otherwise surface as an
    IntegrityError and a 500. They are checked up front and re-caught around the save
    (another request can take the address in between), and reported as a 422 the form can
    show against the field.
    """
    email = _normalized(email)
    if not email:
        raise BillValidationError("I'll need an email address here.")
    # Not a full RFC check — the point is to refuse input that could never receive a
    # password-reset mail, since an unreachable address here locks the account out.
    if " " in email or email.count("@") != 1 or not all(email.split("@")):
        raise BillValidationError("That doesn't look like an email address.")

    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        logger.error("User not found user_id=%s", user_id)
        raise

    previous_email = _normalized(user.email)
    email_changed = email.casefold() != previous_email.casefold()

    if email_changed:
        logger.info(
            "Profile email change user_id=%s from=%s to=%s",
            user_id,
            previous_email,
            email,
        )
        taken = (
            User.objects.exclude(id=user.id)
            .filter(email__iexact=email)
            .exists()
        )
        if taken:
            raise BillValidationError("That email address is already in use.")

        if _normalized(user.username).casefold() == previous_email.casefold():
            handle_taken = (
                User.objects.exclude(id=user.id)
                .filter(username__iexact=email)
                .exists()
            )
            if handle_taken:
                raise BillValidationError("That email address is already in use.")
            user.username = email

        user.email = email

    user.first_name = _normalized(first_name)
    user.last_name = _normalized(last_name)

    try:
        user.save()
    except IntegrityError:
        # Lost the race for the address between the check above and the write.
        logger.warning("Profile update hit a unique conflict user_id=%s", user_id)
        raise BillValidationError("That email address is already in use.")

    logger.info("User profile updated successfully user_id=%s", user_id)
    return user
