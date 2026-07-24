"""Tests for the POST /api/v1/auth/logout endpoint."""
from __future__ import annotations

import jwt as pyjwt
import pytest
from django.conf import settings
from django.test import Client

from shared_models.models import Entity, User, UserEntity

LOGOUT_URL = "/api/v1/auth/logout"


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def user(db):
    return User.objects.create(
        id="logout-test-user",
        email="logout@minty.com",
        password="hashed_pw",
        first_name="Logout",
        last_name="Test",
        username="logouttest",
        system_role="user",
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
