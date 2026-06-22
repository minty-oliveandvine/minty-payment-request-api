"""Tests for the Module 2 JWT token refresh endpoint (Fix 3 — option 1).

Verifies that a valid (non-expired) billing JWT can be exchanged for a
fresh 8-hour token via POST /api/v1/auth/token/refresh, and that an
already-expired token cannot.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt as pyjwt
import pytest
from django.conf import settings
from django.test import Client

from shared_models.models import Entity, User, UserEntity

BILLING_TOKEN_HOURS = 8


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def user(db):
    return User.objects.create(
        id="refresh-test-user",
        email="refresh@minty.com",
        password="hashed_pw",
        first_name="Refresh",
        last_name="Test",
        username="refreshtest",
        system_role="user",
    )


@pytest.fixture
def entity(db):
    return Entity.objects.create(
        id="refresh-test-entity",
        name="Refresh Test Entity",
        country_code="HK",
        currency_code="HKD",
        status="active",
    )


@pytest.fixture
def membership(db, user, entity):
    return UserEntity.objects.create(user=user, entity=entity, role="admin")


def _make_token(user_id, entity_id, *, hours_from_now=4):
    """Issue a billing-scoped JWT with a configurable expiry."""
    return pyjwt.encode(
        {
            "user_id": user_id,
            "entity_id": entity_id,
            "module": "billing",
            "exp": datetime.now(timezone.utc) + timedelta(hours=hours_from_now),
            "iat": datetime.now(timezone.utc),
        },
        settings.SECRET_KEY,
        algorithm="HS256",
    )


def _auth(token, entity_id):
    return {
        "HTTP_AUTHORIZATION": f"Bearer {token}",
        "HTTP_X_ENTITY_ID": entity_id,
    }


REFRESH_URL = "/api/v1/auth/token/refresh"


# ---------------------------------------------------------------------------
# Refresh endpoint tests
# ---------------------------------------------------------------------------

def test_valid_token_can_be_refreshed(client, user, entity, membership):
    """A valid billing JWT is exchanged for a fresh 8-hour token."""
    token = _make_token(user.id, entity.id, hours_from_now=4)
    response = client.post(REFRESH_URL, **_auth(token, entity.id))

    assert response.status_code == 200
    data = response.json()
    assert "token" in data
    assert "expires_in" in data


def test_refreshed_token_has_8_hour_lifetime(client, user, entity, membership):
    """The new token issued by the refresh endpoint has an 8-hour expiry."""
    token = _make_token(user.id, entity.id, hours_from_now=4)
    response = client.post(REFRESH_URL, **_auth(token, entity.id))

    new_token = response.json()["token"]
    payload = pyjwt.decode(new_token, settings.SECRET_KEY, algorithms=["HS256"])

    exp = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
    remaining_hours = (exp - datetime.now(timezone.utc)).total_seconds() / 3600

    assert remaining_hours >= BILLING_TOKEN_HOURS - (60 / 3600), (
        f"Refreshed token expires in {remaining_hours:.2f}h — expected ~{BILLING_TOKEN_HOURS}h"
    )


def test_refreshed_token_carries_correct_claims(client, user, entity, membership):
    """Refreshed token preserves user_id, entity_id, role, and module."""
    token = _make_token(user.id, entity.id, hours_from_now=4)
    response = client.post(REFRESH_URL, **_auth(token, entity.id))

    new_token = response.json()["token"]
    payload = pyjwt.decode(new_token, settings.SECRET_KEY, algorithms=["HS256"])

    assert payload["user_id"] == user.id
    assert payload["entity_id"] == entity.id
    assert payload["module"] == "billing"
    assert "role" in payload


def test_expired_token_cannot_be_refreshed(client, user, entity, membership):
    """An already-expired billing JWT returns 401 — cannot bootstrap from nothing."""
    expired_token = pyjwt.encode(
        {
            "user_id": user.id,
            "entity_id": entity.id,
            "module": "billing",
            "exp": datetime.now(timezone.utc) - timedelta(seconds=1),
        },
        settings.SECRET_KEY,
        algorithm="HS256",
    )
    response = client.post(REFRESH_URL, **_auth(expired_token, entity.id))
    assert response.status_code == 401


def test_missing_token_returns_401(client, user, entity, membership):
    """Calling the refresh endpoint without any token returns 401."""
    response = client.post(REFRESH_URL, HTTP_X_ENTITY_ID=entity.id)
    assert response.status_code == 401


def test_expires_in_value_matches_lifetime(client, user, entity, membership):
    """expires_in in the response matches the 8-hour lifetime in seconds."""
    token = _make_token(user.id, entity.id, hours_from_now=4)
    response = client.post(REFRESH_URL, **_auth(token, entity.id))

    data = response.json()
    assert data["expires_in"] == BILLING_TOKEN_HOURS * 3600


def _make_unscoped_token(user_id, *, hours_from_now=4):
    """Handoff-style JWT with no entity (Select Company → profile)."""
    return pyjwt.encode(
        {
            "user_id": user_id,
            "entity_id": "",
            "module": "billing",
            "exp": datetime.now(timezone.utc) + timedelta(hours=hours_from_now),
            "iat": datetime.now(timezone.utc),
        },
        settings.SECRET_KEY,
        algorithm="HS256",
    )


def test_unscoped_token_can_be_refreshed(client, user, entity, membership):
    """Empty entity_id in JWT + no X-Entity-Id: auth and refresh still work."""
    token = _make_unscoped_token(user.id, hours_from_now=4)
    response = client.post(
        REFRESH_URL,
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )

    assert response.status_code == 200
    new_token = response.json()["token"]
    payload = pyjwt.decode(new_token, settings.SECRET_KEY, algorithms=["HS256"])
    assert payload["user_id"] == user.id
    assert payload.get("entity_id") == ""
