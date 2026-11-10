"""An archived company (``entities.archived_at``, owner 2026-11-10) is a company nobody can reach:

- the door (``core/auth.py``) refuses it like a company the caller holds no role on - 401 on the
  company's data, for a member and a system superuser alike, so nothing says it exists;
- the person-level ``/auth/*`` endpoints (``SelfBearerAuth``) carry on with no company, as they do
  for a company the caller has no role on;
- the entity list leaves it out, for everyone.
"""

import jwt as pyjwt
import pytest
from django.conf import settings
from django.test import Client
from django.utils import timezone
from minty_db.models import Entity, User, UserEntity

pytestmark = pytest.mark.django_db

CURRENCY = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def api():
    return Client()


@pytest.fixture
def member(db):
    return User.objects.create(id="8c7f1f36-6d0e-4a3e-9a51-3c1d2f0e9a01", email="member@minty.com",
                               password="hashed", first_name="Mem", last_name="Ber", username="archmember",
                               system_role="normal")


@pytest.fixture
def superuser(db):
    return User.objects.create(id="8c7f1f36-6d0e-4a3e-9a51-3c1d2f0e9a02", email="super@minty.com",
                               password="hashed", first_name="Super", last_name="User", username="archsuper",
                               system_role="superadmin")


@pytest.fixture
def archived(db, member):
    entity = Entity.objects.create(id="8c7f1f36-6d0e-4a3e-9a51-3c1d2f0e9b01", name="Archived Co",
                                   country_code="HK", currency_id=CURRENCY, status="disconnected",
                                   archived_at=timezone.now())
    UserEntity.objects.create(user=member, entity=entity, role="admin")
    return entity


@pytest.fixture
def live(db, member):
    entity = Entity.objects.create(id="8c7f1f36-6d0e-4a3e-9a51-3c1d2f0e9b02", name="Live Co",
                                   country_code="HK", currency_id=CURRENCY, status="disconnected")
    UserEntity.objects.create(user=member, entity=entity, role="admin")
    return entity


def _auth(user, entity):
    token = pyjwt.encode({"user_id": str(user.id), "entity_id": str(entity.id)}, settings.SECRET_KEY,
                         algorithm="HS256")
    return {"HTTP_AUTHORIZATION": f"Bearer {token}", "HTTP_X_ENTITY_ID": str(entity.id)}


def test_a_member_cannot_reach_an_archived_companys_bills(api, member, archived):
    assert api.get("/api/v1/bills/", **_auth(member, archived)).status_code == 401


def test_a_superuser_cannot_reach_an_archived_companys_bills(api, superuser, archived):
    assert api.get("/api/v1/bills/", **_auth(superuser, archived)).status_code == 401


def test_the_profile_behind_the_company_door_refuses_an_archived_company(api, member, archived):
    # /profile/me sits behind BearerAuth (a role on the company), not SelfBearerAuth.
    assert api.get("/api/v1/profile/me", **_auth(member, archived)).status_code == 401


def test_the_entity_list_leaves_an_archived_company_out(api, member, archived, live):
    resp = api.get("/api/v1/entities/", **_auth(member, live))
    assert resp.status_code == 200
    assert [row["name"] for row in resp.json()] == ["Live Co"]


def test_a_superusers_entity_list_leaves_it_out_too(api, superuser, archived, live):
    resp = api.get("/api/v1/entities/", **_auth(superuser, live))
    assert resp.status_code == 200
    assert "Archived Co" not in [row["name"] for row in resp.json()]
