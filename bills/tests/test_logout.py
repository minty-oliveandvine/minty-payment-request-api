"""Tests for the POST /api/v1/auth/logout endpoint."""

from __future__ import annotations

import jwt as pyjwt
import pytest
from django.conf import settings
from django.test import Client
from django.utils import timezone

from shared_models.models import Entity, User, UserEntity

LOGOUT_URL = "/api/v1/auth/logout"


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def user(db):
    return User.objects.create(
        id="019a4781-94dd-51ec-aea9-b32510435baf",
        email="logout@minty.com",
        password="hashed_pw",
        first_name="Logout",
        last_name="Test",
        username="logouttest",
        system_role="normal",
    )


@pytest.fixture
def entity(db):
    return Entity.objects.create(
        id="logout-test-entity",
        name="Logout Test Entity",
        country_code="HK",
        currency_id="11111111-1111-1111-1111-111111111111",
        status="active",
    )


@pytest.fixture
def membership(db, user, entity):
    return UserEntity.objects.create(user=user, entity=entity, role="admin")


def _auth(user_id, entity_id):
    token = pyjwt.encode(
        {"user_id": user_id, "entity_id": entity_id},
        settings.SECRET_KEY,
        algorithm="HS256",
    )
    return {
        "HTTP_AUTHORIZATION": f"Bearer {token}",
        "HTTP_X_ENTITY_ID": entity_id,
    }


def test_logout_returns_200(client, user, entity, membership):
    response = client.post(LOGOUT_URL, **_auth(user.id, entity.id))
    assert response.status_code == 200


def test_logout_returns_detail_field(client, user, entity, membership):
    response = client.post(LOGOUT_URL, **_auth(user.id, entity.id))
    data = response.json()
    assert data.get("detail") == "logged out"


def test_logout_without_token_returns_401(client, entity):
    response = client.post(LOGOUT_URL, HTTP_X_ENTITY_ID=entity.id)
    assert response.status_code == 401


def test_logout_twice_is_idempotent(client, user, entity, membership):
    headers = _auth(user.id, entity.id)
    client.post(LOGOUT_URL, **headers)
    response = client.post(LOGOUT_URL, **headers)
    assert response.status_code == 200


def test_logout_clears_sign_in_presence(client, user, entity, membership):
    """Signing out of billing takes the user off Minty's Settings > Users list.

    That list is driven by `signed_in_at`, so clearing it here is the whole
    mechanism — without this write the person stays listed as present until the
    presence window expires.
    """
    stamp = timezone.now()
    User.objects.filter(id=user.id).update(signed_in_at=stamp, last_seen_at=stamp)

    response = client.post(LOGOUT_URL, **_auth(user.id, entity.id))
    assert response.status_code == 200

    user.refresh_from_db()
    assert user.signed_in_at is None
    # last_seen_at is a record of when they were last around, not an intent to be
    # listed, so logout leaves it alone.
    assert user.last_seen_at is not None


def test_logout_stamps_last_seen_even_when_it_was_never_set(client, user, entity, membership):
    """The invariant Minty depends on: a cleared signed_in_at must never sit beside
    a blank last_seen_at, or Minty reads the pair as "never stamped" and adopts the
    user straight back onto its signed-in list on their next page."""
    assert user.signed_in_at is None and user.last_seen_at is None

    response = client.post(LOGOUT_URL, **_auth(user.id, entity.id))
    assert response.status_code == 200

    user.refresh_from_db()
    assert user.signed_in_at is None
    assert user.last_seen_at is not None


def test_logout_leaves_other_users_presence_alone(client, user, entity, membership):
    stamp = timezone.now()
    other = User.objects.create(
        id="0aeb0f18-8031-5e9e-b3e4-110a17d7058d",
        email="bystander@minty.com",
        password="hashed_pw",
        first_name="By",
        last_name="Stander",
        username="bystander",
        system_role="normal",
        signed_in_at=stamp,
        last_seen_at=stamp,
    )

    client.post(LOGOUT_URL, **_auth(user.id, entity.id))

    other.refresh_from_db()
    assert other.signed_in_at is not None
