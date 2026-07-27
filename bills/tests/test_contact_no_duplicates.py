"""
BUG REGRESSION — Contacts Listed Twice (2026-04-06)

Reported symptom: each contact appears twice in the Next.js contact picker.

Root-cause analysis identified three potential backend causes:
  1. A contact exists in BOTH Xero live GET and xero_contact_sync with the SAME
     xero_contact_id.  _merge_db_contacts_missing_from_live uses a `seen` set keyed
     on xero_contact_id, so this should NOT produce duplicates — confirmed safe.

  2. Two DB rows with different xero_contact_id but the same display name are
     collapsed to one picker row (first after sort).  Distinct IDs with distinct
     names still both appear.

  3. Final ``_dedupe_bill_contacts_by_xero_contact_id`` ensures no duplicate
     ``xero_contact_id`` in the API response (including duplicate Xero rows or
     duplicate DB rows for the same id).

These tests verify:
  A. DB-only path never returns duplicate xero_contact_id values.
  B. Xero live path never returns duplicate xero_contact_id values (merge guard).
  C. ContactID case-sensitivity does NOT cause a duplicate when casing differs.
  D. A contact that has TWO distinct DB rows with the SAME xero_contact_id is
     deduplicated by the merge helper (would be a data bug, but service should survive).
  E. API endpoint returns HTTP 200 with no duplicate xero_contact_id values end-to-end.
  F. Category filter path (DB only) has no duplicates.
"""

from unittest.mock import MagicMock, patch

import pytest

from bills.services.contact_service import (
    _merge_db_contacts_missing_from_live,
    get_entity_bill_contacts,
)
from shared_models.models import XeroContactSync

# ── helpers ────────────────────────────────────────────────────────────────


def _contact_ids(contacts: list[dict]) -> list[str]:
    return [c["xero_contact_id"] for c in contacts]


def _assert_no_duplicate_contact_ids(contacts: list[dict], label: str = "") -> None:
    ids = _contact_ids(contacts)
    dupes = [xid for xid in set(ids) if ids.count(xid) > 1]
    assert not dupes, (
        f"{label + ': ' if label else ''}duplicate xero_contact_id(s) found: {dupes}\n"
        f"Full list: {[(c['name'], c['xero_contact_id']) for c in contacts]}"
    )


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def disconnected_entity(db, test_entity):
    test_entity.status = "active"
    test_entity.xero_org_id = None
    test_entity.save()
    return test_entity


@pytest.fixture
def connected_entity(db, test_entity):
    test_entity.status = "connected"
    test_entity.xero_org_id = "org-abc"
    test_entity.save()
    return test_entity


@pytest.fixture
def user_with_token(db, test_user):
    test_user.access_token = "fake-xero-token"
    test_user.save()
    return test_user


def _seed_standard_contacts(entity_id: str):
    """Five distinct contacts — standard baseline."""
    data = [
        ("c-alpha", "xero-alpha", "org-abc", "Alpha Supplies", "SUPPLIER"),
        ("c-beta", "xero-beta", "org-abc", "Beta Corp", None),
        ("c-gamma", "xero-gamma", None, "Gamma Ltd", "SUPPLIER"),
        ("c-delta", "xero-delta", "", "Delta Inc", None),
        ("c-eps", "xero-eps", "org-abc", "Epsilon Trading", "SUPPLIER"),
    ]
    for pk, xid, org, name, cat in data:
        XeroContactSync.objects.create(
            id=pk,
            entity_id=entity_id,
            xero_contact_id=xid,
            xero_org_id=org,
            name=name,
            category=cat,
        )


def _make_xero_resp(contacts: list[dict]) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"Contacts": contacts}
    return resp


# ═══════════════════════════════════════════════════════════════════════════
# A. DB-ONLY PATH
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.django_db
class TestDbOnlyPathNoDuplicates:
    """Disconnected entity: service uses DB only. IDs must be unique."""

    def test_standard_contacts_no_duplicates(
        self, disconnected_entity, test_user, test_user_entity
    ):
        _seed_standard_contacts(disconnected_entity.id)
        contacts = get_entity_bill_contacts(
            entity_id=disconnected_entity.id,
            jwt_user_id=test_user.id,
        )
        _assert_no_duplicate_contact_ids(contacts, "DB-only standard contacts")

    def test_single_contact_no_duplicates(
        self, disconnected_entity, test_user, test_user_entity
    ):
        XeroContactSync.objects.create(
            id="only-one",
            entity_id=disconnected_entity.id,
            xero_contact_id="xero-solo",
            xero_org_id=None,
            name="Solo Vendor",
            category=None,
        )
        contacts = get_entity_bill_contacts(
            entity_id=disconnected_entity.id,
            jwt_user_id=test_user.id,
        )
        assert len(contacts) == 1
        _assert_no_duplicate_contact_ids(contacts, "single DB contact")

    def test_empty_entity_no_duplicates(
        self, disconnected_entity, test_user, test_user_entity
    ):
        contacts = get_entity_bill_contacts(
            entity_id=disconnected_entity.id,
            jwt_user_id=test_user.id,
        )
        assert contacts == []

    def test_category_filter_no_duplicates(
        self, disconnected_entity, test_user, test_user_entity
    ):
        """Category filter uses DB path; must not return duplicates within that category."""
        _seed_standard_contacts(disconnected_entity.id)
        contacts = get_entity_bill_contacts(
            entity_id=disconnected_entity.id,
            jwt_user_id=test_user.id,
            category="SUPPLIER",
        )
        _assert_no_duplicate_contact_ids(contacts, "DB category=SUPPLIER")
        for c in contacts:
            assert (
                c["category"] == "SUPPLIER"
            ), f"Category filter leakage: {c['name']} has category={c['category']!r}"

    def test_twenty_contacts_no_duplicates(
        self, disconnected_entity, test_user, test_user_entity
    ):
        """Larger dataset to surface any O(n^2) set-membership bugs."""
        for i in range(20):
            XeroContactSync.objects.create(
                id=f"bulk-{i}",
                entity_id=disconnected_entity.id,
                xero_contact_id=f"xero-bulk-{i:03d}",
                xero_org_id="org-abc",
                name=f"Vendor {i:02d}",
                category=None,
            )
        contacts = get_entity_bill_contacts(
            entity_id=disconnected_entity.id,
            jwt_user_id=test_user.id,
        )
        assert len(contacts) == 20
        _assert_no_duplicate_contact_ids(contacts, "20-contact dataset")


# ═══════════════════════════════════════════════════════════════════════════
# B. XERO LIVE PATH — MERGE GUARD
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.django_db
class TestXeroLivePathMergeNoDuplicates:
    """Connected entity + working Xero API. Contacts that exist in BOTH live
    Xero and xero_contact_sync must NOT appear twice after the merge step."""

    def test_contact_in_xero_and_db_not_duplicated(
        self,
        connected_entity,
        user_with_token,
        test_user_entity,
    ):
        """Primary regression test: the exact overlap scenario that causes the bug."""
        # This contact exists in both Xero API response and local DB.
        XeroContactSync.objects.create(
            id="overlap-1",
            entity_id=connected_entity.id,
            xero_contact_id="xero-overlap",
            xero_org_id="org-abc",
            name="Overlapping Vendor",
            category=None,
        )
        xero_contacts = [{"ContactID": "xero-overlap", "Name": "Overlapping Vendor"}]
        with patch(
            "bills.services.contact_service.requests.get",
            return_value=_make_xero_resp(xero_contacts),
        ):
            contacts = get_entity_bill_contacts(
                entity_id=connected_entity.id,
                jwt_user_id=user_with_token.id,
            )
        assert len(contacts) == 1, (
            f"Expected 1 contact, got {len(contacts)}: "
            f"{[(c['name'], c['xero_contact_id']) for c in contacts]}"
        )
        _assert_no_duplicate_contact_ids(contacts, "overlap: xero + db")

    def test_all_xero_contacts_also_in_db_no_duplicates(
        self,
        connected_entity,
        user_with_token,
        test_user_entity,
    ):
        """All five Xero contacts also exist in DB — none should appear twice."""
        xero_data = [
            ("xero-a", "Acme Ltd"),
            ("xero-b", "Bravo Co"),
            ("xero-c", "Charlie Inc"),
            ("xero-d", "Delta Corp"),
            ("xero-e", "Echo Group"),
        ]
        for i, (xid, name) in enumerate(xero_data, start=1):
            XeroContactSync.objects.create(
                id=f"db-{i}",
                entity_id=connected_entity.id,
                xero_contact_id=xid,
                xero_org_id="org-abc",
                name=name,
                category=None,
            )
        xero_resp = [{"ContactID": xid, "Name": name} for xid, name in xero_data]
        with patch(
            "bills.services.contact_service.requests.get",
            return_value=_make_xero_resp(xero_resp),
        ):
            contacts = get_entity_bill_contacts(
                entity_id=connected_entity.id,
                jwt_user_id=user_with_token.id,
            )
        assert len(contacts) == 5, (
            f"Expected 5 unique contacts, got {len(contacts)}: "
            f"{[(c['name'], c['xero_contact_id']) for c in contacts]}"
        )
        _assert_no_duplicate_contact_ids(contacts, "all overlap: xero + db")

    def test_xero_only_contacts_included_once(
        self,
        connected_entity,
        user_with_token,
        test_user_entity,
    ):
        """Contacts returned by Xero but absent from DB appear exactly once."""
        xero_resp = [
            {"ContactID": "xero-new-1", "Name": "New Vendor One"},
            {"ContactID": "xero-new-2", "Name": "New Vendor Two"},
        ]
        with patch(
            "bills.services.contact_service.requests.get",
            return_value=_make_xero_resp(xero_resp),
        ):
            contacts = get_entity_bill_contacts(
                entity_id=connected_entity.id,
                jwt_user_id=user_with_token.id,
            )
        assert len(contacts) == 2
        _assert_no_duplicate_contact_ids(contacts, "xero-only contacts")

    def test_db_only_contacts_appended_once(
        self,
        connected_entity,
        user_with_token,
        test_user_entity,
    ):
        """DB contacts not yet in Xero live GET are appended exactly once."""
        XeroContactSync.objects.create(
            id="db-only",
            entity_id=connected_entity.id,
            xero_contact_id="xero-db-only",
            xero_org_id="org-abc",
            name="DB-Only Vendor",
            category=None,
        )
        xero_resp = [{"ContactID": "xero-live", "Name": "Live Vendor"}]
        with patch(
            "bills.services.contact_service.requests.get",
            return_value=_make_xero_resp(xero_resp),
        ):
            contacts = get_entity_bill_contacts(
                entity_id=connected_entity.id,
                jwt_user_id=user_with_token.id,
            )
        assert len(contacts) == 2
        _assert_no_duplicate_contact_ids(contacts, "db-only appended once")

    def test_paginated_xero_response_no_duplicates(
        self,
        connected_entity,
        user_with_token,
        test_user_entity,
    ):
        """Xero paginates in batches of 1000; service must not double-add page 1 contacts."""
        page2 = [
            {"ContactID": f"xero-p2-{i}", "Name": f"Page2 Vendor {i}"} for i in range(5)
        ]

        call_count = 0

        def fake_get(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.status_code = 200
            if call_count == 1:
                # First page: full 1000 means "more pages" — but we use 10 here and
                # the service checks `len < 1000` to stop, so we need to fake 1000 on p1.
                # Use a 1000-item list to trigger the pagination branch.
                resp.json.return_value = {
                    "Contacts": [
                        {"ContactID": f"xero-p1-{i}", "Name": f"Page1 V {i}"}
                        for i in range(1000)
                    ]
                }
            else:
                resp.json.return_value = {"Contacts": page2}
            return resp

        with patch("bills.services.contact_service.requests.get", side_effect=fake_get):
            contacts = get_entity_bill_contacts(
                entity_id=connected_entity.id,
                jwt_user_id=user_with_token.id,
            )
        assert call_count == 2, "Expected exactly two Xero API calls (two pages)"
        assert len(contacts) == 1005
        _assert_no_duplicate_contact_ids(contacts, "paginated Xero response")


# ═══════════════════════════════════════════════════════════════════════════
# C. CONTACT-ID CASE-SENSITIVITY BUG
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.django_db
class TestContactIdCaseSensitivity:
    """Merge and final dedupe use normalised xero_contact_id (strip + upper)."""

    def test_same_case_no_duplicate(
        self,
        connected_entity,
        user_with_token,
        test_user_entity,
    ):
        """Baseline: matching case — contact must appear exactly once."""
        xid = "XERO-CASE-ABC"
        XeroContactSync.objects.create(
            id="case-db",
            entity_id=connected_entity.id,
            xero_contact_id=xid,
            xero_org_id="org-abc",
            name="Case Vendor",
            category=None,
        )
        with patch(
            "bills.services.contact_service.requests.get",
            return_value=_make_xero_resp([{"ContactID": xid, "Name": "Case Vendor"}]),
        ):
            contacts = get_entity_bill_contacts(
                entity_id=connected_entity.id,
                jwt_user_id=user_with_token.id,
            )
        assert len(contacts) == 1
        _assert_no_duplicate_contact_ids(contacts, "same-case baseline")

    def test_different_case_collapses_to_single_contact(
        self,
        connected_entity,
        user_with_token,
        test_user_entity,
    ):
        """Xero returns 'XERO-CASE-ABC'; DB stores 'xero-case-abc' — one logical contact."""
        xero_id_uppercase = "XERO-CASE-ABC"
        xero_id_lowercase = "xero-case-abc"

        XeroContactSync.objects.create(
            id="case-db-lower",
            entity_id=connected_entity.id,
            xero_contact_id=xero_id_lowercase,
            xero_org_id="org-abc",
            name="Case Vendor",
            category=None,
        )
        with patch(
            "bills.services.contact_service.requests.get",
            return_value=_make_xero_resp(
                [{"ContactID": xero_id_uppercase, "Name": "Case Vendor"}]
            ),
        ):
            contacts = get_entity_bill_contacts(
                entity_id=connected_entity.id,
                jwt_user_id=user_with_token.id,
            )

        assert len(contacts) == 1
        _assert_no_duplicate_contact_ids(contacts, "case mismatch")
        assert contacts[0]["xero_contact_id"] in (
            xero_id_uppercase,
            xero_id_lowercase,
        )


# ═══════════════════════════════════════════════════════════════════════════
# D. UNIT — _merge_db_contacts_missing_from_live GUARD
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.django_db
class TestMergeHelperNoDuplicates:
    """Direct unit tests of the merge helper to isolate its dedup logic."""

    def test_already_present_contact_not_appended(self, db, test_entity):
        """If live list already contains the xero_contact_id, DB row is skipped."""
        XeroContactSync.objects.create(
            id="m1",
            entity_id=test_entity.id,
            xero_contact_id="xero-111",
            xero_org_id=None,
            name="Shared Vendor",
            category=None,
        )
        live_contacts = [
            {
                "xero_contact_id": "xero-111",
                "name": "Shared Vendor",
                "entity_id": test_entity.id,
                "xero_org_id": None,
                "id": "xero-111",
                "category": None,
            }
        ]
        result = _merge_db_contacts_missing_from_live(
            live_contacts, test_entity.id, None
        )
        assert len(result) == 1
        _assert_no_duplicate_contact_ids(
            result, "merge helper: already-present not appended"
        )

    def test_missing_db_contact_appended_once(self, db, test_entity):
        """DB contact absent from live list is appended exactly once."""
        XeroContactSync.objects.create(
            id="m2",
            entity_id=test_entity.id,
            xero_contact_id="xero-222",
            xero_org_id=None,
            name="DB Only Vendor",
            category=None,
        )
        live_contacts: list[dict] = []  # Xero returned nothing
        result = _merge_db_contacts_missing_from_live(
            live_contacts, test_entity.id, None
        )
        assert len(result) == 1
        assert result[0]["xero_contact_id"] == "xero-222"

    def test_two_different_db_contacts_both_appended(self, db, test_entity):
        """Two distinct DB contacts absent from live list are each appended once."""
        for i in (3, 4):
            XeroContactSync.objects.create(
                id=f"m{i}",
                entity_id=test_entity.id,
                xero_contact_id=f"xero-{i:03d}",
                xero_org_id=None,
                name=f"Vendor {i}",
                category=None,
            )
        result = _merge_db_contacts_missing_from_live([], test_entity.id, None)
        assert len(result) == 2
        _assert_no_duplicate_contact_ids(result, "two distinct DB contacts")

    def test_empty_xero_contact_id_in_live_not_added_to_seen(self, db, test_entity):
        """A live contact with an empty xero_contact_id should not pollute the seen set."""
        XeroContactSync.objects.create(
            id="m5",
            entity_id=test_entity.id,
            xero_contact_id="xero-real",
            xero_org_id=None,
            name="Real Vendor",
            category=None,
        )
        live_contacts = [
            {
                "xero_contact_id": "",
                "name": "Malformed Live Contact",
                "entity_id": test_entity.id,
                "xero_org_id": None,
                "id": "",
                "category": None,
            }
        ]
        result = _merge_db_contacts_missing_from_live(
            live_contacts, test_entity.id, None
        )
        # DB row must be appended; malformed live contact is also kept (no filtering here).
        assert len(result) == 2
        ids = [c["xero_contact_id"] for c in result if c["xero_contact_id"]]
        assert "xero-real" in ids


# ═══════════════════════════════════════════════════════════════════════════
# E. END-TO-END HTTP — no duplicate xero_contact_id in API response
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.django_db
class TestApiEndpointNoDuplicates:
    """Full HTTP round-trip through the Django Ninja endpoint."""

    def test_db_path_no_duplicate_ids_in_response(
        self,
        api_client,
        auth_headers,
        test_user_entity,
        test_entity,
    ):
        _seed_standard_contacts(test_entity.id)
        resp = api_client.get("/api/v1/entity-bill-contacts/", **auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        ids = [c["xero_contact_id"] for c in body]
        dupes = [xid for xid in set(ids) if ids.count(xid) > 1]
        assert not dupes, f"API returned duplicate xero_contact_id(s): {dupes}"

    def test_xero_live_duplicate_contact_ids_in_payload_collapsed(
        self,
        db,
        api_client,
        auth_headers,
        test_user,
        test_entity,
        test_user_entity,
    ):
        """If Xero returns the same ContactID twice, API still returns one row."""
        test_entity.status = "connected"
        test_entity.xero_org_id = "org-abc"
        test_entity.save()
        test_user.access_token = "fake-xero-token"
        test_user.save()

        xero_resp = _make_xero_resp(
            [
                {"ContactID": "xero-dup", "Name": "Dup Vendor A"},
                {"ContactID": "xero-dup", "Name": "Dup Vendor B"},
            ]
        )
        with patch(
            "bills.services.contact_service.requests.get", return_value=xero_resp
        ):
            resp = api_client.get("/api/v1/entity-bill-contacts/", **auth_headers)

        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["xero_contact_id"] == "xero-dup"

    def test_db_path_duplicate_xero_contact_id_rows_collapsed(
        self,
        api_client,
        auth_headers,
        test_entity,
        test_user_entity,
    ):
        """Two xero_contact_sync rows with the same xero_contact_id — one API row."""
        XeroContactSync.objects.create(
            id="dup-a",
            entity_id=test_entity.id,
            xero_contact_id="xero-same",
            xero_org_id=None,
            name="First Row Name",
            category=None,
        )
        XeroContactSync.objects.create(
            id="dup-b",
            entity_id=test_entity.id,
            xero_contact_id="xero-same",
            xero_org_id=None,
            name="Second Row Name",
            category=None,
        )
        resp = api_client.get("/api/v1/entity-bill-contacts/", **auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["xero_contact_id"] == "xero-same"

    def test_xero_live_path_no_duplicate_ids_in_response(
        self,
        db,
        api_client,
        auth_headers,
        test_user,
        test_entity,
        test_user_entity,
    ):
        test_entity.status = "connected"
        test_entity.xero_org_id = "org-abc"
        test_entity.save()
        test_user.access_token = "fake-xero-token"
        test_user.save()

        # Seed DB so some contacts overlap with the Xero response.
        _seed_standard_contacts(test_entity.id)

        xero_resp = _make_xero_resp(
            [
                {"ContactID": "xero-alpha", "Name": "Alpha Supplies"},  # overlap
                {"ContactID": "xero-new-z", "Name": "Zeta Corp"},  # DB-only
            ]
        )
        with patch(
            "bills.services.contact_service.requests.get", return_value=xero_resp
        ):
            resp = api_client.get("/api/v1/entity-bill-contacts/", **auth_headers)

        assert resp.status_code == 200
        body = resp.json()
        ids = [c["xero_contact_id"] for c in body]
        dupes = [xid for xid in set(ids) if ids.count(xid) > 1]
        assert (
            not dupes
        ), f"API returned duplicate xero_contact_id(s) via live path: {dupes}"

    def test_category_filter_no_duplicate_ids_in_response(
        self,
        api_client,
        auth_headers,
        test_user_entity,
        test_entity,
    ):
        _seed_standard_contacts(test_entity.id)
        resp = api_client.get(
            "/api/v1/entity-bill-contacts/?category=SUPPLIER", **auth_headers
        )
        assert resp.status_code == 200
        body = resp.json()
        ids = [c["xero_contact_id"] for c in body]
        dupes = [xid for xid in set(ids) if ids.count(xid) > 1]
        assert not dupes, f"Category-filtered API returned duplicates: {dupes}"
        assert (
            len(body) == 3
        )  # alpha, gamma, eps are SUPPLIER in _seed_standard_contacts

    def test_response_count_matches_unique_contacts_seeded(
        self,
        api_client,
        auth_headers,
        test_user_entity,
        test_entity,
    ):
        """Exactly 5 contacts seeded, exactly 5 must be returned."""
        _seed_standard_contacts(test_entity.id)
        resp = api_client.get("/api/v1/entity-bill-contacts/", **auth_headers)
        assert resp.status_code == 200
        assert len(resp.json()) == 5


# ═══════════════════════════════════════════════════════════════════════════
# F. MULTI-ENTITY ISOLATION — contacts from entity B must not bleed into A
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.django_db
class TestMultiEntityIsolation:
    """A contact for entity B must never appear in entity A's list,
    even if the contact has the same xero_contact_id (cross-entity Xero org scenario).
    """

    def test_entity_b_contacts_not_in_entity_a_list(
        self,
        api_client,
        auth_headers,
        test_user_entity,
        test_entity,
        db,
    ):
        from shared_models.models import Entity

        entity_b = Entity.objects.create(
            id="entity-b-001",
            name="Entity B",
            country_code="HK",
            currency_id="11111111-1111-1111-1111-111111111111",
            status="active",
        )

        # Contacts for entity A.
        XeroContactSync.objects.create(
            id="a-c1",
            entity_id=test_entity.id,
            xero_contact_id="xero-shared",
            xero_org_id=None,
            name="Shared Name Entity A",
            category=None,
        )
        # Same xero_contact_id but belongs to entity B.
        XeroContactSync.objects.create(
            id="b-c1",
            entity_id=entity_b.id,
            xero_contact_id="xero-shared",
            xero_org_id=None,
            name="Shared Name Entity B",
            category=None,
        )

        resp = api_client.get("/api/v1/entity-bill-contacts/", **auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        # Only entity A's contact should appear.
        assert len(body) == 1
        assert body[0]["name"] == "Shared Name Entity A"
        assert all(
            c["entity_id"] == test_entity.id for c in body
        ), "Entity B contact leaked into Entity A response"
