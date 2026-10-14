"""manage.py generate_token: the dev bearer token names the user and entity as strings."""

import re
from io import StringIO

import jwt
import pytest
from django.conf import settings
from django.core.management import call_command


@pytest.mark.django_db
def test_generate_token_mints_a_token_for_the_user_and_entity(test_user, test_entity, test_user_entity):
    out = StringIO()
    call_command("generate_token", user_id=str(test_user.id), entity_id=str(test_entity.id), stdout=out)

    token = re.search(r"eyJ[A-Za-z0-9._-]+", out.getvalue()).group(0)
    claims = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
    assert claims == {"user_id": str(test_user.id), "entity_id": str(test_entity.id)}
