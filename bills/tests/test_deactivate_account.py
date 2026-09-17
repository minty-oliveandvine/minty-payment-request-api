"""Tests for DELETE /api/v1/profile/me — the profile's Sign out button.

This closes the ACCOUNT, not a company. It deactivates rather than deletes: reports,
bills and payments are attributed to the person who made them, so removing the row
would orphan everything they ever touched.

One guard, and it spans every company they belong to. Someone in ten companies who
pays for three is refused and told which three; someone who pays for none may go.
Stranding a subscription is unrecoverable from inside the app — every remaining admin
is refused because a payer exists and is not them, the payer can no longer sign in to
fix it, and the renewals keep charging the card.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import jwt as pyjwt
import pytest
from django.conf import settings
from django.test import Client

from bills.tests.conftest import give_xero_token

from shared_models.models import (Entity, EntityModuleSubscription, User, UserToken,
                                  UserEntity)

URL = "/api/v1/profile/me"


@pytest.fixture
def client():
    return Client()


def _entity(db, suffix: str, name: str | None = None) -> Entity:
    return Entity.objects.create(
        id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"deact-entity-{suffix}")),
        name=name or f"Company {suffix.title()}",
        country_code="HK",
        currency_id="11111111-1111-1111-1111-111111111111",
        status="disconnected",
    )


def _user(suffix: str) -> User:
    return User.objects.create(
        id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"deact-user-{suffix}")),
        email=f"deact-{suffix}@minty.com",
        password="hashed_pw",
        first_name="Dee",
        last_name=suffix.title(),
        username=f"deact{suffix}",
        system_role="normal",
        approved=True,
    )


def _pays_for(entity: Entity, user: User, code: str = "PAYMENT_REQUEST") -> None:
    EntityModuleSubscription.objects.create(
        id=uuid.uuid5(uuid.NAMESPACE_URL, f"ems-{entity.id}-{code}"),
        entity_id=entity.id,
        function_code=code,
        payer_user_id=user.id,
        phase="active",
    )


def _auth(user_id: str, entity_id: str):
    token = pyjwt.encode(
        {"user_id": user_id, "entity_id": entity_id},
        settings.SECRET_KEY,
        algorithm="HS256",
    )
    return {
        "HTTP_AUTHORIZATION": f"Bearer {token}",
        "HTTP_X_ENTITY_ID": entity_id,
    }


def _reload(user: User) -> User:
    user.refresh_from_db()
    return user


# --------------------------------------------------------------------------- #
# Allowed to go
# --------------------------------------------------------------------------- #

def test_someone_who_pays_for_nothing_can_sign_out(client, db):
    entity = _entity(db, "a")
    leaver, payer = _user("leaver"), _user("payer")
    UserEntity.objects.create(user=leaver, entity=entity, role="admin")
    UserEntity.objects.create(user=payer, entity=entity, role="admin")
    _pays_for(entity, payer)

    response = client.delete(URL, **_auth(leaver.id, entity.id))

    assert response.status_code == 200
    assert _reload(leaver).approved is False


def test_signing_out_deactivates_rather_than_deletes(client, db):
    """The row survives, so every record still attributed to them stays readable."""
    entity = _entity(db, "a")
    leaver = _user("leaver")
    other = _user("other")
    UserEntity.objects.create(user=leaver, entity=entity, role="admin")
    UserEntity.objects.create(user=other, entity=entity, role="admin")

    client.delete(URL, **_auth(leaver.id, entity.id))

    assert User.objects.filter(id=leaver.id).exists()
    # And their membership is left alone — the history of who was in the company.
    assert UserEntity.objects.filter(user_id=leaver.id, entity_id=entity.id).exists()


def test_signing_out_clears_the_tokens_and_the_signed_in_stamp(client, db):
    """Nothing may keep acting as them, and they drop off every signed-in list."""
    entity = _entity(db, "a")
    leaver = _user("leaver")
    UserEntity.objects.create(user=leaver, entity=entity, role="admin")
    give_xero_token(leaver, "a", refresh_token="r", id_token="i")
    User.objects.filter(id=leaver.id).update(
        signed_in_at=datetime(2026, 8, 14, 10, 0, tzinfo=timezone.utc),
    )

    client.delete(URL, **_auth(leaver.id, entity.id))

    fresh = _reload(leaver)
    token = UserToken.objects.get(user_id=leaver.id)
    assert token.access_token is None
    assert token.refresh_token is None
    assert token.id_token is None
    assert token.access_token_expires_in is None
    assert fresh.signed_in_at is None


# --------------------------------------------------------------------------- #
# The guard: paying for ANY company blocks it
# --------------------------------------------------------------------------- #

def test_the_subscriber_of_their_only_company_cannot_sign_out(client, db):
    entity = _entity(db, "a", name="Acme")
    payer = _user("payer")
    UserEntity.objects.create(user=payer, entity=entity, role="admin")
    _pays_for(entity, payer)

    response = client.delete(URL, **_auth(payer.id, entity.id))

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "Acme" in detail
    assert "your card" in detail.lower()
    assert _reload(payer).approved is True


def test_paying_for_three_of_ten_companies_blocks_it_and_names_the_three(client, db):
    """The rule as stated: count them all, check each, refuse on any.

    Only ONE membership is created, for the company in the token. That is not a
    shortcut — the guard reads the subscription rows, which is where the payer is
    recorded, so membership is beside the point. It also has to be this way: the
    Django mirror declares `user` as the primary key of `user_entity`, so the test
    database physically cannot hold ten memberships for one person.
    """
    payer = _user("payer")
    companies = [_entity(db, str(i), name=f"Co {i:02d}") for i in range(10)]
    UserEntity.objects.create(user=payer, entity=companies[0], role="admin")

    paid = [companies[1], companies[4], companies[7]]
    for entity in paid:
        _pays_for(entity, payer)

    response = client.delete(URL, **_auth(payer.id, companies[0].id))

    assert response.status_code == 422
    detail = response.json()["detail"]
    for entity in paid:
        assert entity.name in detail
    # And says nothing about the seven they merely belong to.
    for entity in companies:
        if entity not in paid:
            assert entity.name not in detail
    assert _reload(payer).approved is True


def test_a_bundled_company_is_named_once_not_per_module(client, db):
    """Two module rows, one payer, one company — DISTINCT keeps the message readable."""
    entity = _entity(db, "a", name="Bundled Co")
    payer = _user("payer")
    UserEntity.objects.create(user=payer, entity=entity, role="admin")
    _pays_for(entity, payer, code="PAYMENT_REQUEST")
    _pays_for(entity, payer, code="PETTY_CASH")

    response = client.delete(URL, **_auth(payer.id, entity.id))

    assert response.status_code == 422
    assert response.json()["detail"].count("Bundled Co") == 1


def test_handing_the_last_subscription_over_unblocks_it(client, db):
    """The guard reads the payer live, so it opens as soon as the bill moves."""
    entity = _entity(db, "a")
    payer, heir = _user("payer"), _user("heir")
    UserEntity.objects.create(user=payer, entity=entity, role="admin")
    UserEntity.objects.create(user=heir, entity=entity, role="admin")
    _pays_for(entity, payer)

    assert client.delete(URL, **_auth(payer.id, entity.id)).status_code == 422

    EntityModuleSubscription.objects.filter(entity_id=entity.id).update(
        payer_user_id=heir.id
    )

    assert client.delete(URL, **_auth(payer.id, entity.id)).status_code == 200
    assert _reload(payer).approved is False


def test_paying_for_a_company_you_have_left_still_blocks_it(client, db):
    """The bill follows the subscription row, not the membership. Someone whose card
    still pays for a company they no longer belong to would strand it just the same."""
    home, gone = _entity(db, "a"), _entity(db, "b", name="Left Behind")
    payer = _user("payer")
    UserEntity.objects.create(user=payer, entity=home, role="admin")
    _pays_for(gone, payer)  # no membership on `gone`

    response = client.delete(URL, **_auth(payer.id, home.id))

    assert response.status_code == 422
    assert "Left Behind" in response.json()["detail"]


def test_signing_out_without_a_token_is_401(client, db):
    entity = _entity(db, "a")
    assert client.delete(URL, HTTP_X_ENTITY_ID=entity.id).status_code == 401
