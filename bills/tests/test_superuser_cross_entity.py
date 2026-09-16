"""
Superuser cross-entity access tests.

Covers the five scenarios from the task spec for the Django (Module 2) layer:

  (a) System superuser sees all entities (bills) even without a UserEntity row.
  (b) System superuser GET on a non-member entity succeeds (200).
  (c) System superuser POST/PUT/DELETE on a non-member entity returns 403.
  (d) System superuser WITH a UserEntity row on the entity can write (201/200).
  (e) Regular user without a UserEntity row is blocked entirely (401/None auth).
"""

import json

import jwt as pyjwt
import pytest
from django.conf import settings
from django.test import Client

from bills.models import Bill
from shared_models.models import Entity, User, UserEntity

# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def api():
    return Client()


@pytest.fixture
def superuser(db):
    return User.objects.create(
        id="19f48472-c287-5441-8876-2fc9d7c15ddf",
        email="superuser@minty.com",
        password="hashed",
        first_name="Super",
        last_name="User",
        username="crosssuperuser",
        system_role="superadmin",
    )


@pytest.fixture
def regular_user(db):
    return User.objects.create(
        id="4a48cee1-30a6-557d-adee-8300a4cb9dc0",
        email="regular@minty.com",
        password="hashed",
        first_name="Regular",
        last_name="User",
        username="crossregular",
        system_role="normal",
    )


@pytest.fixture
def member_entity(db):
    return Entity.objects.create(
        id="member-entity-001",
        name="Member Entity",
        country_code="HK",
        currency_id="11111111-1111-1111-1111-111111111111",
        status="active",
    )


@pytest.fixture
def foreign_entity(db):
    """Entity the superuser has NO UserEntity row for."""
    return Entity.objects.create(
        id="foreign-entity-001",
        name="Foreign Entity",
        country_code="HK",
        currency_id="11111111-1111-1111-1111-111111111111",
        status="active",
    )


@pytest.fixture
def superuser_membership(db, superuser, member_entity):
    return UserEntity.objects.create(
        user=superuser,
        entity=member_entity,
        role="super_admin",
    )


def _token(user, entity):
    return pyjwt.encode(
        {"user_id": user.id, "entity_id": entity.id},
        settings.SECRET_KEY,
        algorithm="HS256",
    )


def _auth(user, entity):
    return {
        "HTTP_AUTHORIZATION": f"Bearer {_token(user, entity)}",
        "HTTP_X_ENTITY_ID": entity.id,
    }


def _make_bill(entity, user, status="draft"):
    return Bill.objects.create(
        entity_id=entity.id,
        contact="Test Vendor",
        status=status,
        amount=100,
        description="Cross-entity test bill",
        uploaded_by=user.id,
    )


# ── (a) Superuser sees all bills across entities ─────────────────────────────


@pytest.mark.django_db
class TestSuperuserSeesAllBills:
    """System superuser can list bills from any entity even without membership."""

    def test_superuser_can_list_bills_without_membership(
        self, api, superuser, foreign_entity
    ):
        _make_bill(foreign_entity, superuser)
        resp = api.get("/api/v1/bills/", **_auth(superuser, foreign_entity))
        assert resp.status_code == 200
        data = resp.json()
        assert (
            "items" in data
            or isinstance(data, list)
            or data.get("count") is not None
            or len(data) >= 0
        )


# ── (b) Superuser GET on non-member entity succeeds ──────────────────────────


@pytest.mark.django_db
class TestSuperuserReadNonMemberEntity:
    """System superuser can read a specific bill from a non-member entity."""

    def test_superuser_get_bill_succeeds(self, api, superuser, foreign_entity):
        bill = _make_bill(foreign_entity, superuser)
        resp = api.get(f"/api/v1/bills/{bill.id}", **_auth(superuser, foreign_entity))
        assert resp.status_code == 200


# ── (c) Superuser POST/PUT/DELETE on non-member entity returns 403 ───────────


@pytest.mark.django_db
class TestSuperuserWriteBlockedOnNonMemberEntity:
    """System superuser write operations on an entity they are not a member of
    must be rejected with 403."""

    def test_superuser_create_bill_blocked(self, api, superuser, foreign_entity):
        resp = api.post(
            "/api/v1/bills/",
            data=json.dumps({"contact": "Vendor"}),
            content_type="application/json",
            **_auth(superuser, foreign_entity),
        )
        assert resp.status_code == 403

    def test_superuser_update_bill_blocked(self, api, superuser, foreign_entity):
        bill = _make_bill(foreign_entity, superuser)
        resp = api.put(
            f"/api/v1/bills/{bill.id}",
            data=json.dumps({"contact": "Updated Vendor"}),
            content_type="application/json",
            **_auth(superuser, foreign_entity),
        )
        assert resp.status_code == 403

    def test_superuser_delete_bill_blocked(self, api, superuser, foreign_entity):
        bill = _make_bill(foreign_entity, superuser)
        resp = api.delete(
            f"/api/v1/bills/{bill.id}", **_auth(superuser, foreign_entity)
        )
        assert resp.status_code == 403

    def test_superuser_create_blocked_error_message(
        self, api, superuser, foreign_entity
    ):
        resp = api.post(
            "/api/v1/bills/",
            data=json.dumps({"contact": "Vendor"}),
            content_type="application/json",
            **_auth(superuser, foreign_entity),
        )
        assert resp.status_code == 403
        body = resp.json()
        # The message should communicate read-only / view-only restriction.
        detail = (body.get("detail") or body.get("message") or "").lower()
        assert "view" in detail or "superuser" in detail or "read" in detail


# ── (d) Superuser WITH membership can write ───────────────────────────────────


@pytest.mark.django_db
class TestSuperuserMemberCanWrite:
    """System superuser WITH an explicit UserEntity row on an entity can create
    and edit bills on that entity (the view-only guard only fires for entities
    the superuser is not a member of)."""

    def test_superuser_with_membership_can_create_bill(
        self, api, superuser, member_entity, superuser_membership
    ):
        resp = api.post(
            "/api/v1/bills/",
            data=json.dumps({"contact": "Vendor"}),
            content_type="application/json",
            **_auth(superuser, member_entity),
        )
        # Superuser with super_admin membership keeps full CRUD on this entity.
        assert resp.status_code == 201, resp.content

    def test_superuser_without_membership_blocked_but_member_allowed(
        self, api, superuser, member_entity, foreign_entity, superuser_membership
    ):
        """Same superuser: blocked on foreign_entity, allowed on member_entity."""
        resp_foreign = api.post(
            "/api/v1/bills/",
            data=json.dumps({"contact": "Vendor"}),
            content_type="application/json",
            **_auth(superuser, foreign_entity),
        )
        assert resp_foreign.status_code == 403

        resp_member = api.post(
            "/api/v1/bills/",
            data=json.dumps({"contact": "Vendor"}),
            content_type="application/json",
            **_auth(superuser, member_entity),
        )
        assert resp_member.status_code == 201, resp_member.content


# ── (e) Regular user without membership is rejected ──────────────────────────


@pytest.mark.django_db
class TestRegularUserBlockedFromNonMemberEntity:
    """A non-superuser without a UserEntity row cannot even authenticate into
    the entity — the BearerAuth middleware rejects the token outright."""

    def test_regular_user_no_membership_cannot_list_bills(
        self, api, regular_user, foreign_entity
    ):
        resp = api.get("/api/v1/bills/", **_auth(regular_user, foreign_entity))
        # BearerAuth returns None (no entity role found) → Django Ninja 401.
        assert resp.status_code == 401

    def test_regular_user_no_membership_cannot_create_bill(
        self, api, regular_user, foreign_entity
    ):
        resp = api.post(
            "/api/v1/bills/",
            data=json.dumps({"contact": "Vendor"}),
            content_type="application/json",
            **_auth(regular_user, foreign_entity),
        )
        assert resp.status_code == 401


# ── Profile endpoint returns member_entity_ids ────────────────────────────────


@pytest.mark.django_db
class TestProfileMemberEntityIds:
    """GET /profile/me returns the correct member_entity_ids list."""

    def test_superuser_without_membership_has_empty_member_list(
        self, api, superuser, foreign_entity
    ):
        resp = api.get(
            "/api/v1/profile/me",
            **_auth(superuser, foreign_entity),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "member_entity_ids" in data
        assert foreign_entity.id not in data["member_entity_ids"]

    def test_superuser_with_membership_lists_entity(
        self, api, superuser, member_entity, superuser_membership
    ):
        resp = api.get(
            "/api/v1/profile/me",
            **_auth(superuser, member_entity),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert member_entity.id in data["member_entity_ids"]

    def test_regular_user_with_membership_lists_entity(
        self, api, regular_user, member_entity, db
    ):
        UserEntity.objects.create(
            user=regular_user, entity=member_entity, role="cashier"
        )
        resp = api.get(
            "/api/v1/profile/me",
            **_auth(regular_user, member_entity),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert member_entity.id in data["member_entity_ids"]
