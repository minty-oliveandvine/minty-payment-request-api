"""A bill's currency is the entity's (entities.currency_id), never the client's,
and an entity with no currency cannot create one (422)."""

import json

import pytest

from bills.models import Bill, CurrencyInfo
from shared_models.models import Entity

NO_CURRENCY = "Your company has no currency set. Choose one in Entity & Integration settings."


@pytest.fixture
def usd(db):
    row, _ = CurrencyInfo.objects.get_or_create(
        currency_code="USD",
        defaults={"currency_name": "US Dollar", "symbol": "$", "decimal_places": 2},
    )
    return row


@pytest.fixture
def no_currency(test_entity):
    Entity.objects.filter(id=test_entity.id).update(currency_id=None)


def _post(client, headers, path, body):
    return client.post(
        path, data=json.dumps(body), content_type="application/json", **headers
    )


SUBMITTABLE = {
    "contact": "Vendor A",
    "description": "Stationery",
    "amount": "10.00",
    "due_date": "2026-11-01",
    "invoice_date": "2026-10-01",
    "line_items": [
        {"description": "Pens", "quantity": "1", "unit_amount": "10.00",
         "line_amount": "10.00", "account_code": "400"}
    ],
}


@pytest.mark.django_db
class TestEntityCurrencyRequired:
    @pytest.mark.parametrize("path", ["/api/v1/bills/", "/api/v1/bills/draft/"])
    def test_create_takes_entity_currency_not_client(
        self, api_client, auth_headers, test_user_entity, usd, path
    ):
        resp = _post(api_client, auth_headers, path, {"contact": "V", "currency_code": "USD"})
        assert resp.status_code == 201
        assert resp.json()["currency_code"] == "HKD"

    @pytest.mark.parametrize(
        "path", ["/api/v1/bills/", "/api/v1/bills/draft/", "/api/v1/bills/submit/"]
    )
    def test_create_refused_when_entity_has_no_currency(
        self, api_client, auth_headers, test_user_entity, no_currency, path
    ):
        before = Bill.objects.count()
        resp = _post(api_client, auth_headers, path, {**SUBMITTABLE, "currency_code": "HKD"})
        assert resp.status_code == 422
        assert resp.json()["error"] == NO_CURRENCY
        assert Bill.objects.count() == before

    def test_draft_update_fills_missing_currency(
        self, api_client, auth_headers, draft_bill, test_user_entity
    ):
        assert draft_bill.currency_id is None
        resp = api_client.put(
            f"/api/v1/bills/{draft_bill.id}/draft",
            data=json.dumps({"description": "x", "currency_code": "USD"}),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["currency_code"] == "HKD"

    def test_draft_update_without_any_currency_still_saves(
        self, api_client, auth_headers, draft_bill, test_user_entity, no_currency
    ):
        resp = api_client.put(
            f"/api/v1/bills/{draft_bill.id}/draft",
            data=json.dumps({"description": "x"}),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["currency_code"] == ""

    def test_entity_currency_endpoint(self, api_client, auth_headers, test_user_entity):
        resp = api_client.get("/api/v1/auth/entity-currency", **auth_headers)
        assert resp.status_code == 200
        assert resp.json() == {"currency_code": "HKD", "decimal_places": 2}

    def test_entity_currency_endpoint_unset(
        self, api_client, auth_headers, test_user_entity, no_currency
    ):
        resp = api_client.get("/api/v1/auth/entity-currency", **auth_headers)
        assert resp.status_code == 200
        assert resp.json() == {"currency_code": "", "decimal_places": 2}

    def test_entity_currency_endpoint_zero_places(
        self, api_client, auth_headers, test_entity, test_user_entity
    ):
        jpy, _ = CurrencyInfo.objects.get_or_create(
            currency_code="JPY",
            defaults={"currency_name": "Japanese Yen", "symbol": "¥", "decimal_places": 0},
        )
        Entity.objects.filter(id=test_entity.id).update(currency_id=jpy.id)
        resp = api_client.get("/api/v1/auth/entity-currency", **auth_headers)
        assert resp.status_code == 200
        assert resp.json() == {"currency_code": "JPY", "decimal_places": 0}


@pytest.mark.django_db
class TestPaymentCurrency:
    """A payment is in its bill's currency (the company's when the bill has none)."""

    def _pay(self, client, headers, bill_id, body):
        return _post(client, headers, f"/api/v1/bills/{bill_id}/payments", body)

    def test_payment_takes_bill_currency_not_client(
        self, api_client, auth_headers, test_user_entity, draft_bill, usd
    ):
        draft_bill.currency_code = "HKD"
        draft_bill.save()
        resp = self._pay(api_client, auth_headers, draft_bill.id,
                         {"amount": "10.00", "currency_code": "USD", "payment_status": "pending"})
        assert resp.status_code == 201
        assert resp.json()["currency_code"] == "HKD"

        pid = resp.json()["id"]
        resp = api_client.put(
            f"/api/v1/bills/{draft_bill.id}/payments/{pid}",
            data=json.dumps({"currency_code": "USD"}),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["currency_code"] == "HKD"

    def test_payment_on_currency_less_bill_takes_entity_currency(
        self, api_client, auth_headers, test_user_entity, draft_bill
    ):
        assert draft_bill.currency_id is None
        resp = self._pay(api_client, auth_headers, draft_bill.id,
                         {"amount": "10.00", "payment_status": "pending"})
        assert resp.status_code == 201
        assert resp.json()["currency_code"] == "HKD"

    def test_payment_refused_when_neither_bill_nor_entity_has_currency(
        self, api_client, auth_headers, test_user_entity, draft_bill, no_currency
    ):
        resp = self._pay(api_client, auth_headers, draft_bill.id,
                         {"amount": "10.00", "currency_code": "HKD", "payment_status": "pending"})
        assert resp.status_code == 422
        assert resp.json()["error"] == NO_CURRENCY
