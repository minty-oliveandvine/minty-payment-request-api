"""The door and Payment Settings' rights (owner, 2026-11-12; the settings audit): an unapproved
membership is no membership, as in every other API; the account-code list needs cashier and up;
its changes stay accountant and up - one rank ladder, minty_shared.policy."""

import jwt as pyjwt
import pytest
from django.conf import settings
from django.test import Client
from minty_db.models import Entity, User, UserEntity

ENTITY = "b66d1e23-a0a3-585b-a656-f6437e173551"
LIST = "/api/v1/entity-bill-accounts/?include_inactive=true&sync_chart=false"


def person(email, role, *, approved=True):
    user = User.objects.create(
        email=email,
        password="x",
        first_name="P",
        last_name="Q",
        username=email,
        system_role="normal",
    )
    UserEntity.objects.create(user=user, entity_id=ENTITY, role=role, approved=approved)
    token = pyjwt.encode(
        {"user_id": str(user.id), "entity_id": ENTITY}, settings.SECRET_KEY, algorithm="HS256"
    )
    return {"HTTP_AUTHORIZATION": f"Bearer {token}", "HTTP_X_ENTITY_ID": ENTITY}


@pytest.fixture
def company(db):
    return Entity.objects.create(
        id=ENTITY,
        name="Door Co",
        country_code="HK",
        currency_id="11111111-1111-1111-1111-111111111111",
        status="disconnected",
    )


def test_an_unapproved_membership_is_no_membership(company):
    assert (
        Client().get(LIST, **person("pending@test.com", "admin", approved=False)).status_code == 401
    )


@pytest.mark.parametrize(
    "role, status", [("entity_base", 403), ("cashier", 200), ("shop_manager", 200)]
)
def test_the_account_codes_are_read_from_cashier_up(company, role, status):
    assert Client().get(LIST, **person(f"{role}@test.com", role)).status_code == status


@pytest.mark.parametrize(
    "role, allowed", [("shop_manager", False), ("accountant", True), ("Admin", True)]
)
def test_the_account_codes_change_from_accountant_up(company, role, allowed):
    from core.exceptions import PermissionDeniedError
    from core.permissions import check_edit_bill_settings

    if allowed:
        check_edit_bill_settings(role)
    else:
        with pytest.raises(PermissionDeniedError):
            check_edit_bill_settings(role)


RIGHTS = "/api/v1/entity-bill-accounts/rights"


@pytest.mark.parametrize(
    "role, can_edit",
    [("cashier", False), ("shop_manager", False), ("accountant", True), ("super_admin", True)],
)
def test_the_rights_the_page_draws_from_are_the_servers(company, role, can_edit):
    """Payment Settings shows what these say, never a role read from the token (owner, 2026-11-12)."""
    resp = Client().get(RIGHTS, **person(f"r-{role}@test.com", role))
    assert resp.status_code == 200
    assert resp.json() == {"can_edit": can_edit, "view_only": False}


def test_minty_staff_visiting_may_look_only(company):
    staff = User.objects.create(
        email="staff@test.com", password="x", first_name="S", last_name="T", username="staff",
        system_role="superadmin",
    )
    token = pyjwt.encode(
        {"user_id": str(staff.id), "entity_id": ENTITY}, settings.SECRET_KEY, algorithm="HS256"
    )
    resp = Client().get(RIGHTS, HTTP_AUTHORIZATION=f"Bearer {token}", HTTP_X_ENTITY_ID=ENTITY)
    assert resp.json() == {"can_edit": False, "view_only": True}
