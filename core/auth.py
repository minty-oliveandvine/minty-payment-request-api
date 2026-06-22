import logging

import jwt
from django.conf import settings
from django.db import connection
from ninja.security import HttpBearer

from shared_models.models import User

logger = logging.getLogger("minty-api")


class BearerAuth(HttpBearer):
    """
    Validates the JWT issued by the Flask app (Module 1) during the
    cross-module handoff.  Also verifies that the caller has access to
    the entity specified in the X-Entity-Id header and loads their
    entity-level role onto the request for permission checks.
    """

    def authenticate(self, request, token):
        try:
            import hashlib
            key = settings.SECRET_KEY
            key_hash = hashlib.sha256(key.encode()).hexdigest()[:16]
            logger.info(
                "Auth attempt: key_len=%d key_hash=%s token_len=%d",
                len(key), key_hash, len(token),
            )
            payload = jwt.decode(token, key, algorithms=["HS256"])
            user = User.objects.get(id=payload["user_id"])
            user_id = payload["user_id"]
            token_entity_id = (payload.get("entity_id") or "").strip()
            header_entity_id = (request.headers.get("X-Entity-Id") or "").strip()
            entity_id = header_entity_id or token_entity_id

            # Trust the JWT-claimed system_role first: Flask issued and signed
            # this token, and reads its own pettycashv2.user table when doing
            # so. Falling back to a DB query here causes mismatches when the
            # stored value has unexpected casing or whitespace.
            jwt_system_role = (payload.get("system_role") or "").strip().lower()

            if header_entity_id and token_entity_id and header_entity_id != token_entity_id:
                logger.warning(
                    "Entity ID mismatch: header=%s, token=%s, user=%s",
                    header_entity_id, token_entity_id, user_id,
                )

            if not entity_id:
                # Unscoped: Flask handoff with empty entity (e.g. profile from Select Company).
                if not token_entity_id and not header_entity_id:
                    system_superuser = (
                        jwt_system_role == "superuser"
                        or self._is_system_superuser(str(user.id))
                    )
                    with connection.cursor() as cursor:
                        cursor.execute(
                            "SELECT 1 FROM user_entity WHERE user_id = %s AND role = 'super_admin' LIMIT 1",
                            [str(user.id)],
                        )
                        is_super_admin = cursor.fetchone() is not None or system_superuser
                    request.auth_user = user
                    request.entity_id = ""
                    request.entity_role = ""
                    request.is_super_admin = is_super_admin
                    request.is_system_superuser = system_superuser
                    request.is_entity_member = False
                    logger.info(
                        "Auth: unscoped billing session user_id=%s (no entity context)",
                        user_id,
                    )
                    return user
                logger.warning("Auth rejected: no entity_id in header or token")
                return None

            entity_role = self._get_entity_role(user.id, entity_id)

            # Resolve whether this user is a system superuser once; reuse below.
            # Trust the JWT claim first (see comment above), then fall back to
            # DB lookup so older tokens without the claim still work.
            system_superuser = (
                jwt_system_role == "superuser"
                or self._is_system_superuser(str(user.id))
            )
            # is_entity_member is True only when the user has an explicit
            # UserEntity row for this entity.  System superusers without a
            # row are NOT members (they get view-only access via the virtual
            # role granted below).
            is_entity_member = entity_role is not None

            # Superusers (system_role='superuser') are allowed into any entity
            # for read-only access even without a user_entity row.  Give them a
            # virtual 'super_admin' entity role so permission checks downstream
            # still work correctly.
            if entity_role is None:
                if system_superuser:
                    entity_role = "super_admin"
                    logger.info(
                        "Auth: superuser granted virtual super_admin role "
                        "for user_id=%s entity_id=%s",
                        user.id, entity_id,
                    )
                else:
                    logger.warning(
                        "Auth rejected: no role for user_id=%s entity_id=%s",
                        user.id, entity_id,
                    )
                    return None

            # is_super_admin is True for entity-level super_admin users AND for
            # system superusers (system_role='superuser') so that the entity
            # list and other "see all" paths work correctly for both groups.
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT 1 FROM user_entity WHERE user_id = %s AND role = 'super_admin' LIMIT 1",
                    [str(user.id)],
                )
                is_super_admin = cursor.fetchone() is not None or system_superuser

            request.auth_user = user
            request.entity_id = entity_id
            request.entity_role = entity_role
            request.is_super_admin = is_super_admin
            request.is_system_superuser = system_superuser
            request.is_entity_member = is_entity_member
            return user
        except jwt.ExpiredSignatureError:
            logger.warning("Auth rejected: token expired")
            return None
        except (jwt.DecodeError, jwt.InvalidTokenError) as exc:
            logger.warning("Auth rejected: invalid token — %s", exc)
            return None
        except KeyError as exc:
            logger.warning("Auth rejected: missing claim in token — %s", exc)
            return None
        except User.DoesNotExist:
            logger.warning("Auth rejected: user not found in DB")
            return None

    @staticmethod
    def _get_entity_role(user_id: str, entity_id: str) -> str | None:
        """Return the user's role for the entity, or None if no access."""
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT role FROM user_entity WHERE user_id = %s AND entity_id = %s LIMIT 1",
                [user_id, entity_id],
            )
            row = cursor.fetchone()
            return row[0] if row else None

    @staticmethod
    def _is_system_superuser(user_id: str) -> bool:
        """Return True if the user has system_role='superuser'.

        Case-insensitive match so legacy rows with non-canonical casing
        ('Superuser', 'SUPERUSER', etc.) still resolve correctly.
        """
        return User.objects.filter(
            id=str(user_id), system_role__iexact="superuser"
        ).exists()
