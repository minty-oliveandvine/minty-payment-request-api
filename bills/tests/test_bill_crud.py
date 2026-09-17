import json
import re

import pytest

from bills.models import Bill


@pytest.mark.django_db
class TestCreateBill:
    def test_create_draft_minimal(self, api_client, auth_headers, test_user_entity):
        resp = api_client.post(
            "/api/v1/bills/",
            data=json.dumps({"contact": "Vendor A"}),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["status"] == "draft"
        assert body["contact"] == "Vendor A"

    def test_create_draft_with_line_items(
        self, api_client, auth_headers, test_user_entity
    ):
        resp = api_client.post(
            "/api/v1/bills/",
            data=json.dumps(
                {
                    "contact": "Vendor B",
                    "amount": "250.00",
                    "currency_code": "HKD",
                    "line_items": [
                        {
                            "description": "Item 1",
                            "quantity": "2",
                            "unit_amount": "75.00",
                            "line_amount": "150.00",
                        },
                        {
                            "description": "Item 2",
                            "quantity": "1",
                            "unit_amount": "100.00",
                            "line_amount": "100.00",
                        },
                    ],
                }
            ),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert len(body["line_items"]) == 2
        assert body["currency_code"] == "HKD"

    def test_create_draft_empty_body(self, api_client, auth_headers, test_user_entity):
        resp = api_client.post(
            "/api/v1/bills/",
            data=json.dumps({}),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 201
        assert resp.json()["status"] == "draft"

    def test_create_requires_auth(self, api_client):
        resp = api_client.post(
            "/api/v1/bills/",
            data=json.dumps({}),
            content_type="application/json",
        )
        assert resp.status_code == 401

    def test_create_rejects_duplicate_reference(
        self, api_client, auth_headers, test_user_entity
    ):
        payload = {"contact": "V1", "reference": "INV-DUP-001"}
        r1 = api_client.post(
            "/api/v1/bills/",
            data=json.dumps(payload),
            content_type="application/json",
            **auth_headers,
        )
        assert r1.status_code == 201
        r2 = api_client.post(
            "/api/v1/bills/",
            data=json.dumps(payload),
            content_type="application/json",
            **auth_headers,
        )
        assert r2.status_code == 422
        assert "invoice number" in r2.json()["detail"].lower()

    def test_create_duplicate_reference_case_insensitive(
        self, api_client, auth_headers, test_user_entity
    ):
        api_client.post(
            "/api/v1/bills/",
            data=json.dumps({"contact": "A", "reference": "inv-abc"}),
            content_type="application/json",
            **auth_headers,
        )
        r2 = api_client.post(
            "/api/v1/bills/",
            data=json.dumps({"contact": "B", "reference": "INV-ABC"}),
            content_type="application/json",
            **auth_headers,
        )
        assert r2.status_code == 422

    def test_create_allows_multiple_empty_reference(
        self, api_client, auth_headers, test_user_entity
    ):
        refs = []
        for _ in range(2):
            r = api_client.post(
                "/api/v1/bills/",
                data=json.dumps({"contact": "X"}),
                content_type="application/json",
                **auth_headers,
            )
            assert r.status_code == 201
            refs.append(r.json()["reference"])
        assert refs[0] != refs[1]
        pat = re.compile(r"^MBI[A-Z]{3}-\d{12}(-[0-9A-F]{4})?$")
        assert pat.match(refs[0])
        assert pat.match(refs[1])

    def test_suggested_reference_endpoint(
        self, api_client, auth_headers, test_user_entity
    ):
        r = api_client.get("/api/v1/bills/suggested-reference/", **auth_headers)
        assert r.status_code == 200
        ref = r.json()["reference"]
        assert re.match(r"^MBI[A-Z]{3}-\d{12}(-[0-9A-F]{4})?$", ref)


@pytest.mark.django_db
class TestListBills:
    def test_list_empty(self, api_client, auth_headers, test_user_entity):
        resp = api_client.get("/api/v1/bills/", **auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_with_bills(
        self, api_client, auth_headers, draft_bill, test_user_entity
    ):
        resp = api_client.get("/api/v1/bills/", **auth_headers)
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_filter_by_status(
        self, api_client, auth_headers, draft_bill, test_user_entity
    ):
        resp = api_client.get("/api/v1/bills/?status=draft", **auth_headers)
        assert resp.status_code == 200
        assert len(resp.json()) == 1

        resp = api_client.get("/api/v1/bills/?status=paid", **auth_headers)
        assert resp.status_code == 200
        assert len(resp.json()) == 0

    def test_filter_by_contact(
        self, api_client, auth_headers, draft_bill, test_user_entity
    ):
        resp = api_client.get("/api/v1/bills/?contact=Vendor", **auth_headers)
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_filter_by_search_matches_description(
        self, api_client, auth_headers, draft_bill, test_user_entity
    ):
        resp = api_client.get("/api/v1/bills/?search=bill", **auth_headers)
        assert resp.status_code == 200
        assert len(resp.json()) == 1
        assert resp.json()[0]["id"] == str(draft_bill.id)

    def test_filter_by_search_matches_contact(
        self, api_client, auth_headers, draft_bill, test_user_entity
    ):
        resp = api_client.get("/api/v1/bills/?search=Vendor", **auth_headers)
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_filter_by_search_no_match(
        self, api_client, auth_headers, draft_bill, test_user_entity
    ):
        resp = api_client.get("/api/v1/bills/?search=zzznomatch", **auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []


@pytest.mark.django_db
class TestGetBill:
    def test_get_detail(self, api_client, auth_headers, draft_bill, test_user_entity):
        resp = api_client.get(f"/api/v1/bills/{draft_bill.id}", **auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == str(draft_bill.id)
        assert body["contact"] == "Vendor A"
        assert "reference" in body
        assert "currency_code" in body

    def test_get_not_found(self, api_client, auth_headers, test_user_entity):
        resp = api_client.get("/api/v1/bills/nonexistent", **auth_headers)
        assert resp.status_code == 404


@pytest.mark.django_db
class TestUpdateBill:
    def test_update_contact(
        self, api_client, auth_headers, draft_bill, test_user_entity
    ):
        resp = api_client.put(
            f"/api/v1/bills/{draft_bill.id}",
            data=json.dumps({"contact": "Updated Vendor"}),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["contact"] == "Updated Vendor"

    def test_update_status(
        self, api_client, auth_headers, draft_bill, test_user_entity
    ):
        resp = api_client.put(
            f"/api/v1/bills/{draft_bill.id}",
            data=json.dumps(
                {
                    "status": "submitted",
                    "invoice_date": "2026-01-15",
                    "due_date": "2026-02-15",
                    "line_items": [
                        {
                            "description": "Line",
                            "quantity": "1",
                            "unit_amount": "100.00",
                            "line_amount": "100.00",
                        },
                    ],
                }
            ),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "submitted"

    def test_update_with_line_items(
        self, api_client, auth_headers, draft_bill, test_user_entity
    ):
        resp = api_client.put(
            f"/api/v1/bills/{draft_bill.id}",
            data=json.dumps(
                {
                    "line_items": [
                        {
                            "description": "New item",
                            "quantity": "1",
                            "unit_amount": "50.00",
                            "line_amount": "50.00",
                        },
                    ],
                }
            ),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 200
        assert len(resp.json()["line_items"]) == 1

    def test_update_reference_and_currency(
        self, api_client, auth_headers, draft_bill, test_user_entity
    ):
        # bill.currency_id points at currency_info since C8: the code must be a real currency
        from bills.models import CurrencyInfo

        CurrencyInfo.objects.get_or_create(
            currency_code="USD", defaults={"currency_name": "US Dollar", "symbol": "$", "decimal_places": 2},
        )
        resp = api_client.put(
            f"/api/v1/bills/{draft_bill.id}",
            data=json.dumps(
                {
                    "reference": "INV-2026-001",
                    "currency_code": "USD",
                }
            ),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["reference"] == "INV-2026-001"
        assert body["currency_code"] == "USD"

    def test_update_rejects_duplicate_reference(
        self, api_client, auth_headers, draft_bill, test_user_entity, test_user
    ):
        other = Bill.objects.create(
            entity_id=draft_bill.entity_id,
            contact="Other",
            status="draft",
            amount=50,
            reference="INV-TAKEN",
            uploaded_by=test_user.id,
        )
        resp = api_client.put(
            f"/api/v1/bills/{draft_bill.id}",
            data=json.dumps({"reference": "inv-taken"}),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 422
        assert other.id != draft_bill.id

    def test_update_allows_same_reference_on_self(
        self, api_client, auth_headers, draft_bill, test_user_entity
    ):
        api_client.put(
            f"/api/v1/bills/{draft_bill.id}",
            data=json.dumps({"reference": "INV-SELF"}),
            content_type="application/json",
            **auth_headers,
        )
        resp = api_client.put(
            f"/api/v1/bills/{draft_bill.id}",
            data=json.dumps({"contact": "Still me", "reference": "INV-SELF"}),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["reference"] == "INV-SELF"

    def test_update_allows_reference_only_case_change(
        self, api_client, auth_headers, draft_bill, test_user_entity
    ):
        """Uniqueness is not re-checked when invoice # is unchanged (incl. case-only edits)."""
        api_client.put(
            f"/api/v1/bills/{draft_bill.id}",
            data=json.dumps({"reference": "my-inv-001"}),
            content_type="application/json",
            **auth_headers,
        )
        resp = api_client.put(
            f"/api/v1/bills/{draft_bill.id}",
            data=json.dumps({"contact": "x", "reference": "MY-INV-001"}),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["reference"] == "MY-INV-001"


@pytest.mark.django_db
class TestDeleteBill:
    def test_delete_bill(self, api_client, auth_headers, draft_bill, test_user_entity):
        bid = draft_bill.id
        resp = api_client.delete(f"/api/v1/bills/{bid}", **auth_headers)
        assert resp.status_code == 200
        assert "delet" in resp.json().get("message", "").lower()

        resp = api_client.get(f"/api/v1/bills/{bid}", **auth_headers)
        assert resp.status_code == 404

    def test_reference_reusable_after_void(
        self, api_client, auth_headers, test_user_entity
    ):
        ref = "INV-VOID-REUSE"
        r1 = api_client.post(
            "/api/v1/bills/",
            data=json.dumps({"contact": "A", "reference": ref}),
            content_type="application/json",
            **auth_headers,
        )
        assert r1.status_code == 201
        bid = r1.json()["id"]
        api_client.delete(f"/api/v1/bills/{bid}", **auth_headers)
        r2 = api_client.post(
            "/api/v1/bills/",
            data=json.dumps({"contact": "B", "reference": ref}),
            content_type="application/json",
            **auth_headers,
        )
        assert r2.status_code == 201
        assert r2.json()["reference"] == ref


@pytest.mark.django_db
class TestDraftReferenceUniqueness:
    def test_draft_save_rejects_duplicate(
        self, api_client, auth_headers, test_user_entity
    ):
        api_client.post(
            "/api/v1/bills/draft/",
            data=json.dumps({"contact": "A", "reference": "DRAFT-DUP"}),
            content_type="application/json",
            **auth_headers,
        )
        r2 = api_client.post(
            "/api/v1/bills/draft/",
            data=json.dumps({"contact": "B", "reference": "draft-dup"}),
            content_type="application/json",
            **auth_headers,
        )
        assert r2.status_code == 422

    def test_draft_update_rejects_duplicate(
        self, api_client, auth_headers, draft_bill, test_user_entity, test_user
    ):
        Bill.objects.create(
            entity_id=draft_bill.entity_id,
            contact="Other",
            status="draft",
            amount=1,
            reference="DRAFT-OTHER",
            uploaded_by=test_user.id,
        )
        resp = api_client.put(
            f"/api/v1/bills/{draft_bill.id}/draft",
            data=json.dumps({"reference": "draft-other"}),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 422
