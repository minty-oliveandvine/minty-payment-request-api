"""``GET /api/v1/bills/by-reference/{reference}`` - the web app's details address carries the
Payment No. (the bill's reference) since 2026-10-05, so the page finds the bill by it."""

import pytest

from bills.models import Bill
from shared_models.models import Entity


def _bill(entity_id, user_id, reference, status="submitted"):
    return Bill.objects.create(entity_id=entity_id, contact="7-Eleven", status=status, amount=10,
                               reference=reference, uploaded_by=user_id)


def _get(api_client, auth_headers, reference):
    return api_client.get(f"/api/v1/bills/by-reference/{reference}", **auth_headers)


@pytest.mark.django_db
class TestBillByReference:
    def test_finds_the_bill_case_insensitively(self, api_client, auth_headers, test_user_entity,
                                              test_user, test_entity):
        bill = _bill(test_entity.id, test_user.id, "MBIANG-135840051026")

        for ref in ("MBIANG-135840051026", "mbiang-135840051026"):
            resp = _get(api_client, auth_headers, ref)
            assert resp.status_code == 200, resp.content
            assert resp.json()["id"] == str(bill.id)

    def test_a_live_bill_wins_over_a_void_one_with_the_same_reference(
        self, api_client, auth_headers, test_user_entity, test_user, test_entity
    ):
        _bill(test_entity.id, test_user.id, "MBI-1", status="void")
        live = _bill(test_entity.id, test_user.id, "MBI-1")
        _bill(test_entity.id, test_user.id, "MBI-1", status="void")

        assert _get(api_client, auth_headers, "MBI-1").json()["id"] == str(live.id)

    def test_another_companys_reference_and_an_unknown_one_are_not_found(
        self, api_client, auth_headers, test_user_entity, test_user, test_entity
    ):
        other = Entity.objects.create(id="2c1f4c55-0000-4000-8000-000000000001", name="Other",
                                      country_code="HK", currency_id=test_entity.currency_id,
                                      status="disconnected")
        _bill(other.id, test_user.id, "MBI-OTHER")

        assert _get(api_client, auth_headers, "MBI-OTHER").status_code == 404
        assert _get(api_client, auth_headers, "NOPE").status_code == 404
