import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import jwt
from django.conf import settings
from django.http import HttpResponse, HttpResponseRedirect

from core.auth import get_entity_role as _get_entity_role
from shared_models.enums import is_superadmin
from shared_models.models import Entity, User

logger = logging.getLogger("minty-api.auth")


def landing(request):
    """
    Cross-module handoff from Flask (Module 1).
    Validates the incoming JWT, issues a billing-scoped token, then
    redirects to the Next.js frontend with auth parameters in the URL.
    """
    token = request.GET.get("token", "")
    if not token:
        return HttpResponse("Missing token parameter", status=400)

    try:
        decoded = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
        user_id = decoded["user_id"]
        entity_id = decoded.get("entity_id", "")
        user = User.objects.get(id=user_id)

        entity_role = _get_entity_role(user_id, entity_id) or ""

        # Check if user is a system superuser without entity membership (view-only mode)
        is_system_superuser = is_superadmin(user.system_role)
        is_view_only = is_system_superuser and not entity_role
        if is_view_only:
            entity_role = "super_admin"

        billing_token = jwt.encode(
            {
                "user_id": user_id,
                "entity_id": entity_id,
                "role": entity_role,
                "module": "billing",
                "is_system_superuser": is_system_superuser,
                "is_view_only": is_view_only,
                "exp": datetime.now(timezone.utc) + timedelta(hours=8),
                "iat": datetime.now(timezone.utc),
            },
            settings.SECRET_KEY,
            algorithm="HS256",
        )

        entity_name = ""
        if entity_id:
            try:
                entity_name = Entity.objects.get(id=entity_id).name
            except Entity.DoesNotExist:
                pass

        logger.info("Landing handoff user_id=%s entity_id=%s", user_id, entity_id)

        frontend_url = settings.PAYMENT_REQUEST_WEB_URL
        qs = urlencode(
            {
                "token": billing_token,
                "entity_id": entity_id,
                "entity_name": entity_name,
            }
        )
        return HttpResponseRedirect(f"{frontend_url}/module-selection?{qs}")

    except jwt.ExpiredSignatureError:
        return HttpResponse("Token expired. Please go back and try again.", status=401)
    except (jwt.DecodeError, jwt.InvalidTokenError):
        return HttpResponse("Invalid token.", status=401)
    except User.DoesNotExist:
        return HttpResponse("User not found.", status=404)
