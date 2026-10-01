"""PUT /entity-bill-accounts/{id} (Payment Settings ticks) and the function-map DELETE.

Payment Settings writes only ``entity_bill_account_xero``: ``account_info.status`` is Petty
Cash's tick state, so unticking here must leave it alone. The last ticked settings-type code
cannot be unticked (409, nothing written).
"""

import json
import uuid

import jwt as pyjwt
import pytest
from django.conf import settings
from django.test import Client

from bills.models import EntityBillAccountXero, EntityFunction, EntityFunctionMap
from shared_models.models import AccountInfo, Entity, User, UserEntity

ENTITY_ID = "b77e2f34-b1c4-4a6e-9d21-0f1e2d3c4b5a"


@pytest.fixture
def api():
    return Client()


@pytest.fixture
def user(db):
    return User.objects.create(
        id="d1b72a0c-359b-46de-a87f-f53843a0b4c1",
        email="acctupd@minty.com",
        password="hashed",
        first_name="Acct",
        last_name="Update",
        username="acctupd",
        system_role="normal",
    )


@pytest.fixture
def entity(db):
    return Entity.objects.create(
        id=ENTITY_ID,
        name="Acct Update Entity",
        country_code="HK",
        currency_id="11111111-1111-1111-1111-111111111111",
        status="disconnected",
    )


@pytest.fixture
def auth_headers(user, entity):
    UserEntity.objects.create(user=user, entity=entity, role="admin")
    token = pyjwt.encode(
        {"user_id": user.id, "entity_id": entity.id},
        settings.SECRET_KEY,
        algorithm="HS256",
    )
    return {"HTTP_AUTHORIZATION": f"Bearer {token}", "HTTP_X_ENTITY_ID": entity.id}


def _account(entity, code, account_type="EXPENSE", is_active=True):
    return EntityBillAccountXero.objects.create(
        entity_id=entity.id,
        account_code=code,
        account_name=f"Account {code}",
        account_type=account_type,
        is_active=is_active,
        is_deleted=False,
        xero_account_id=f"xero-{code}",
    )


def _put(api, headers, account, body):
    return api.put(
        f"/api/v1/entity-bill-accounts/{account.id}",
        data=json.dumps(body),
        content_type="application/json",
        **headers,
    )


@pytest.mark.django_db
def test_untick_does_not_touch_petty_cash_account_info(api, entity, auth_headers):
    keep = _account(entity, "600")
    untick = _account(entity, "610")
    info = AccountInfo.objects.create(
        id=uuid.uuid4(),
        entity_id=entity.id,
        type="EXPENSE",
        name="Account 610",
        xero_code="610",
        status="ACTIVE",
    )

    resp = _put(api, auth_headers, untick, {"is_active": False})

    assert resp.status_code == 200, resp.content
    untick.refresh_from_db()
    keep.refresh_from_db()
    info.refresh_from_db()
    assert untick.is_active is False
    assert keep.is_active is True
    assert info.status == "ACTIVE"


@pytest.mark.django_db
def test_unticking_the_last_ticked_code_is_refused(api, entity, auth_headers):
    last = _account(entity, "600")
    _account(entity, "610", is_active=False)
    # Not settings types / deleted: they do not count as "still ticked".
    _account(entity, "200", account_type="BANK")
    gone = _account(entity, "620")
    gone.is_deleted = True
    gone.save()

    resp = _put(api, auth_headers, last, {"is_active": False, "account_name": "Renamed"})

    assert resp.status_code == 409
    assert json.loads(resp.content) == {"detail": "Keep at least one account code ticked."}
    last.refresh_from_db()
    assert last.is_active is True
    assert last.account_name == "Account 600"  # nothing written


@pytest.mark.django_db
def test_unticking_one_of_two_is_allowed(api, entity, auth_headers):
    first = _account(entity, "600")
    _account(entity, "610", account_type="OVERHEADS")

    resp = _put(api, auth_headers, first, {"is_active": False})

    assert resp.status_code == 200, resp.content
    first.refresh_from_db()
    assert first.is_active is False


@pytest.mark.django_db
def test_other_edits_on_the_last_ticked_code_still_save(api, entity, auth_headers):
    last = _account(entity, "600")

    resp = _put(api, auth_headers, last, {"account_name": "Renamed", "is_active": True})

    assert resp.status_code == 200, resp.content
    last.refresh_from_db()
    assert last.account_name == "Renamed"


@pytest.mark.django_db
def test_unknown_account_is_404(api, entity, auth_headers):
    missing = EntityBillAccountXero(id=uuid.uuid4())
    assert _put(api, auth_headers, missing, {"is_active": False}).status_code == 404


@pytest.mark.django_db
def test_function_map_delete_returns_200(api, entity, auth_headers):
    fn = EntityFunction.objects.create(
        function_code="PAYMENT_REQUEST", function_name="Payment Request"
    )
    EntityFunctionMap.objects.create(entity_id=entity.id, entity_function=fn)

    resp = api.delete(f"/api/v1/entity-function-maps/{fn.id}", **auth_headers)

    assert resp.status_code == 200, resp.content
    assert not EntityFunctionMap.objects.filter(entity_id=entity.id).exists()
