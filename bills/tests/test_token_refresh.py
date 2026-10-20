"""Tests for the Module 2 JWT token refresh endpoint (Fix 3 — option 1).

Verifies that a valid (non-expired) billing JWT can be exchanged for a
fresh 8-hour token via POST /api/v1/auth/token/refresh, and that an
already-expired token cannot.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

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
        id="59413a81-db95-5233-928a-14fbdb3fa29b",
        email="refresh@minty.com",
        password="hashed_pw",
        first_name="Refresh",
        last_name="Test",
        username="refreshtest",
        system_role="normal",
    )


@pytest.fixture
def entity(db):
    return Entity.objects.create(
        id="8f5c6d43-a250-5a1a-910f-957e32b44e27",
        name="Refresh Test Entity",
        country_code="HK",
        currency_id="11111111-1111-1111-1111-111111111111",
        status="disconnected",
    )


@pytest.fixture
def membership(db, user, entity):
    return UserEntity.objects.create(user=user, entity=entity, role="admin")


def _make_token(user_id, entity_id, *, hours_from_now=4, **extra):
    """Issue a billing-scoped JWT with a configurable expiry (``extra`` adds claims)."""
    return pyjwt.encode(
        {
            "user_id": user_id,
            "entity_id": entity_id,
            "module": "billing",
            "exp": datetime.now(UTC) + timedelta(hours=hours_from_now),
            "iat": datetime.now(UTC),
            **extra,
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

    exp = datetime.fromtimestamp(payload["exp"], tz=UTC)
    remaining_hours = (exp - datetime.now(UTC)).total_seconds() / 3600

    assert remaining_hours >= BILLING_TOKEN_HOURS - (
        60 / 3600
    ), f"Refreshed token expires in {remaining_hours:.2f}h — expected ~{BILLING_TOKEN_HOURS}h"


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


@pytest.fixture
def superadmin(db):
    return User.objects.create(
        id="3d0c2b1e-5f4a-5b6c-8d7e-9f0a1b2c3d4e",
        email="refresh-super@minty.com",
        password="hashed_pw",
        first_name="Super",
        last_name="Refresh",
        username="refreshsuper",
        system_role="superadmin",
    )


def _refreshed_payload(client, token, entity_id):
    response = client.post(REFRESH_URL, **_auth(token, entity_id))
    assert response.status_code == 200, response.content
    return pyjwt.decode(response.json()["token"], settings.SECRET_KEY, algorithms=["HS256"])


def test_refresh_keeps_a_non_member_superadmin_view_only(client, superadmin, entity):
    """A super admin on a company they don't belong to stays superadmin + view-only."""
    token = _make_token(superadmin.id, entity.id, system_role="superadmin", is_view_only=True)
    payload = _refreshed_payload(client, token, entity.id)

    assert payload["system_role"] == "superadmin"
    assert payload["is_view_only"] is True


def test_refresh_of_a_member_superadmin_is_not_view_only(client, superadmin, entity):
    UserEntity.objects.create(user=superadmin, entity=entity, role="admin")
    token = _make_token(superadmin.id, entity.id, system_role="superadmin")
    payload = _refreshed_payload(client, token, entity.id)

    assert payload["system_role"] == "superadmin"
    assert payload["is_view_only"] is False


def test_refresh_of_a_normal_user_has_no_system_role(client, user, entity, membership):
    token = _make_token(user.id, entity.id)
    payload = _refreshed_payload(client, token, entity.id)

    assert payload["system_role"] == ""
    assert payload["is_view_only"] is False


def test_refresh_turns_the_legacy_superuser_word_into_superadmin(client, superadmin, entity):
    token = _make_token(superadmin.id, entity.id, system_role="superuser")
    payload = _refreshed_payload(client, token, entity.id)

    assert payload["system_role"] == "superadmin"
    assert payload["is_view_only"] is True


def test_refresh_carries_the_sid_over(client, user, entity, membership):
    token = _make_token(user.id, entity.id, sid="sess-123")
    payload = _refreshed_payload(client, token, entity.id)

    assert payload["sid"] == "sess-123"


def test_refresh_adds_no_sid_when_the_old_token_had_none(client, user, entity, membership):
    payload = _refreshed_payload(client, _make_token(user.id, entity.id), entity.id)

    assert "sid" not in payload


def test_expired_token_cannot_be_refreshed(client, user, entity, membership):
    """An already-expired billing JWT returns 401 — cannot bootstrap from nothing."""
    expired_token = pyjwt.encode(
        {
            "user_id": user.id,
            "entity_id": entity.id,
            "module": "billing",
            "exp": datetime.now(UTC) - timedelta(seconds=1),
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
            "exp": datetime.now(UTC) + timedelta(hours=hours_from_now),
            "iat": datetime.now(UTC),
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
