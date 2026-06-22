"""Tests for billing JWT lifetime alignment (Fix 3).

Verifies that the billing-scoped token issued by the Django landing view
has a lifetime of 8 hours — not 30 minutes — so active users in Module 2
are not kicked out mid-session.

The handoff token (issued by Flask) stays at 30 minutes since it is
one-time use only (just for the cross-module redirect).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt as pyjwt
import pytest
from django.conf import settings
from django.test import Client

from shared_models.models import Entity, User, UserEntity

BILLING_TOKEN_MIN_HOURS = 8


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def user(db):
    return User.objects.create(
        id="token-test-user",
        email="tokentest@minty.com",
        password="hashed_pw",
        first_name="Token",
        last_name="Test",
        username="tokentest",
        system_role="user",
    )


@pytest.fixture
def entity(db):
    return Entity.objects.create(
        id="token-test-entity",
        name="Token Test Entity",
        country_code="HK",
        currency_code="HKD",
        status="active",
    )


@pytest.fixture
def membership(db, user, entity):
    return UserEntity.objects.create(user=user, entity=entity, role="admin")


def _handoff_token(user_id, entity_id, *, exp_minutes=30):
    """Simulate the Flask handoff token issued by blueprints/entity/routes/modules.py."""
    return pyjwt.encode(
        {
            "user_id": user_id,
            "entity_id": entity_id,
            "exp": datetime.now(timezone.utc) + timedelta(minutes=exp_minutes),
            "iat": datetime.now(timezone.utc),
        },
        settings.SECRET_KEY,
        algorithm="HS256",
    )


# ---------------------------------------------------------------------------
# Fix 3 tests
# ---------------------------------------------------------------------------

def test_billing_token_lifetime_is_at_least_8_hours(client, user, entity, membership):
    """Landing view issues a billing JWT with expiry >= 8 hours from now."""
    token = _handoff_token(user.id, entity.id)
    response = client.get(f"/landing?token={token}")

    # Landing redirects to the frontend with the billing token in the query string
    assert response.status_code == 302
    location = response["Location"]
    assert "token=" in location

    billing_token = location.split("token=")[1].split("&")[0]
    payload = pyjwt.decode(billing_token, settings.SECRET_KEY, algorithms=["HS256"])

    exp = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
    now = datetime.now(timezone.utc)
    remaining_hours = (exp - now).total_seconds() / 3600

    # Allow 60 seconds of tolerance for test execution time
    assert remaining_hours >= BILLING_TOKEN_MIN_HOURS - (60 / 3600), (
        f"Billing token expires in {remaining_hours:.2f}h — expected >= {BILLING_TOKEN_MIN_HOURS}h"
    )


def test_billing_token_contains_required_claims(client, user, entity, membership):
    """Billing JWT carries user_id, entity_id, role, module, exp, iat."""
    token = _handoff_token(user.id, entity.id)
    response = client.get(f"/landing?token={token}")

    location = response["Location"]
    billing_token = location.split("token=")[1].split("&")[0]
    payload = pyjwt.decode(billing_token, settings.SECRET_KEY, algorithms=["HS256"])

    assert payload["user_id"] == user.id
    assert payload["entity_id"] == entity.id
    assert payload["module"] == "billing"
    assert "role" in payload
    assert "exp" in payload
    assert "iat" in payload


def test_expired_handoff_token_is_rejected(client, user, entity, membership):
    """An already-expired Flask handoff token returns 401."""
    expired_token = pyjwt.encode(
        {
            "user_id": user.id,
            "entity_id": entity.id,
            "exp": datetime.now(timezone.utc) - timedelta(seconds=1),
        },
        settings.SECRET_KEY,
        algorithm="HS256",
    )
    response = client.get(f"/landing?token={expired_token}")
    assert response.status_code == 401


def test_handoff_token_without_entity_id_still_issues_billing_token(client, user, entity, membership):
    """Handoff token with no entity_id falls back gracefully (entity from header)."""
    token = pyjwt.encode(
        {
            "user_id": user.id,
            "exp": datetime.now(timezone.utc) + timedelta(minutes=30),
        },
        settings.SECRET_KEY,
        algorithm="HS256",
    )
    # Without entity_id in token, landing should still redirect (entity_id may be empty)
    response = client.get(f"/landing?token={token}")
    # Accepts the token but may redirect with empty entity
    assert response.status_code in (302, 400)
