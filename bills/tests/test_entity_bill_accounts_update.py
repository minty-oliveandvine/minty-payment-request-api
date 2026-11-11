"""PUT /entity-bill-accounts/{id} (Payment Settings ticks), and the module catalogue read-only.

Payment Settings writes ``entity_bill_account_xero`` (and its account's ``account_module``
PAYMENT_REQUEST row, below): ``account_info.status`` is Petty
Cash's tick state, so unticking here must leave it alone. The last ticked settings-type code
cannot be unticked (409, nothing written).
"""

import json
import uuid

import jwt as pyjwt
import pytest
from django.conf import settings
from django.test import Client
from minty_db.models import AccountInfo, Entity, User, UserEntity

from bills.models import EntityBillAccountXero, EntityFunction, EntityFunctionMap

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


# Owner, 2026-11-03: the module catalogue (entity_function) and which modules a company has
# (entity_function_map) are not this service's to write - subscriptions own them. Its six write
# routes are gone; the reads stay.
@pytest.mark.django_db
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/api/v1/entity-functions/"),
        ("put", "/api/v1/entity-functions/{fn}"),
        ("delete", "/api/v1/entity-functions/{fn}"),
        ("post", "/api/v1/entity-function-maps/"),
        ("put", "/api/v1/entity-function-maps/{fn}"),
        ("delete", "/api/v1/entity-function-maps/{fn}"),
    ],
)
def test_the_module_catalogue_cannot_be_written(api, entity, auth_headers, method, path):
    fn = EntityFunction.objects.create(
        function_code="PAYMENT_REQUEST", function_name="Payment Request"
    )
    EntityFunctionMap.objects.create(entity_id=entity.id, entity_function=fn, is_enabled=True)
    body = {"function_code": "X", "function_name": "X", "entity_function_id": str(fn.id),
            "is_enabled": False}

    resp = getattr(api, method)(
        path.format(fn=fn.id), data=json.dumps(body), content_type="application/json",
        **auth_headers,
    )

    assert resp.status_code in (404, 405), resp.content
    assert EntityFunction.objects.count() == 1
    assert EntityFunctionMap.objects.get(entity_id=entity.id).is_enabled is True


@pytest.mark.django_db
def test_the_module_catalogue_can_still_be_read(api, entity, auth_headers):
    fn = EntityFunction.objects.create(
        function_code="PAYMENT_REQUEST", function_name="Payment Request"
    )
    EntityFunctionMap.objects.create(entity_id=entity.id, entity_function=fn, is_enabled=True)

    assert api.get("/api/v1/entity-functions/", **auth_headers).status_code == 200
    assert api.get(f"/api/v1/entity-functions/{fn.id}", **auth_headers).status_code == 200
    assert api.get("/api/v1/entity-function-maps/", **auth_headers).status_code == 200
    names = api.get("/api/v1/entity-function-maps/functions", **auth_headers)
    assert names.json() == [{"function_name": "Payment Request"}]
    assert api.get(f"/api/v1/entity-function-maps/{fn.id}", **auth_headers).status_code == 200


# ---- account_module PAYMENT_REQUEST follows Payment Settings (2026-11-11) ---------------------------
#
# Onboarding's step 8 writes both entity_bill_account_xero and account_module PAYMENT_REQUEST (the
# Xero-optional design's module tick); a change here must keep the two in step, or the module row
# drifts. The account is found by its Xero id, else (Minty's own) by its code.


def _info(entity, code, *, xero=True):
    return AccountInfo.objects.create(
        id=uuid.uuid4(),
        entity_id=entity.id,
        type="EXPENSE",
        name=f"Account {code}",
        xero_code=code,
        code=code,
        status="ACTIVE",
        xero_account_id=f"xero-{code}" if xero else "",
    )


def _module(info):
    from minty_db.models import AccountModule

    return AccountModule.objects.filter(account_id=info.id, module_code="PAYMENT_REQUEST").first()


@pytest.mark.django_db
def test_a_tick_change_moves_the_payment_request_module_row(api, entity, auth_headers):
    _account(entity, "600")
    untick = _account(entity, "610")
    info = _info(entity, "610")

    assert _put(api, auth_headers, untick, {"is_active": False}).status_code == 200
    assert _module(info).is_active is False
    assert _put(api, auth_headers, untick, {"is_active": True}).status_code == 200
    assert _module(info).is_active is True


@pytest.mark.django_db
def test_the_default_moves_on_the_module_rows_too(api, entity, auth_headers):
    first, second = _account(entity, "600"), _account(entity, "610")
    one, two = _info(entity, "600"), _info(entity, "610")

    assert _put(api, auth_headers, first, {"is_default": True}).status_code == 200
    assert _module(one).is_default is True
    assert _put(api, auth_headers, second, {"is_default": True}).status_code == 200
    assert (_module(one).is_default, _module(two).is_default) == (False, True)


@pytest.mark.django_db
def test_a_minty_account_is_found_by_its_code(api, entity, auth_headers):
    _account(entity, "600")
    own = EntityBillAccountXero.objects.create(
        entity_id=entity.id, account_code="420", account_name="Rent", account_type="EXPENSE",
        is_active=True, is_deleted=False, xero_account_id="",
    )
    info = _info(entity, "420", xero=False)
    assert _put(api, auth_headers, own, {"is_active": False}).status_code == 200
    assert _module(info).is_active is False


@pytest.mark.django_db
def test_a_deleted_code_switches_its_module_row_off(api, entity, auth_headers):
    _account(entity, "600")
    gone = _account(entity, "610")
    info = _info(entity, "610")
    _put(api, auth_headers, gone, {"is_active": True})
    resp = api.delete(f"/api/v1/entity-bill-accounts/{gone.id}", **auth_headers)
    assert resp.status_code == 200, resp.content
    assert (_module(info).is_active, _module(info).is_default) == (False, False)


@pytest.mark.django_db
def test_a_code_with_no_account_changes_no_module_row(api, entity, auth_headers):
    from minty_db.models import AccountModule

    _account(entity, "600")
    lone = _account(entity, "610")
    assert _put(api, auth_headers, lone, {"is_active": False}).status_code == 200
    assert not AccountModule.objects.filter(entity_id=entity.id).exists()
