"""
Side-by-side comparison: are Module 1 and Module 2 contact lists exactly the same?

Simulates both modules under every scenario:
  A. Entity connected — Xero API returns contacts
  B. Entity connected — Xero API fails, both fall back to DB
  C. Entity disconnected — both use DB only

For each scenario we compare the NAME SET produced by each module.
"""

from unittest.mock import MagicMock, patch

import uuid

import pytest

from bills.tests.conftest import give_xero_token

from bills.services.contact_service import get_entity_bill_contacts
from shared_models.models import XeroContactSync


def _uid(label):
    """xero_contact_sync.id is a uuid column since C5: a stable uuid for a test label."""
    import uuid as _uuid

    return _uuid.uuid5(_uuid.NAMESPACE_URL, f"minty-test-{label}")


# ── Helpers that replicate Module 1 logic exactly ────────────────────────


def _module1_connected_xero_ok(xero_api_contacts: list[dict]) -> set[str]:
    """Module 1 when connected and Xero returns data: uses Xero response directly."""
    return {c["Name"] for c in xero_api_contacts}


def _module1_db_fallback(entity_id: str) -> set[str]:
    """Module 1 DB fallback: XeroContactSync.filter_by(entity_id=entity_id).all()"""
    rows = XeroContactSync.objects.filter(entity_id=entity_id).all()
    return {r.name for r in rows}


def _module2_result_names(entity_id, jwt_user_id) -> set[str]:
    """Module 2 via the new contact service."""
    contacts = get_entity_bill_contacts(
        entity_id=entity_id,
        jwt_user_id=jwt_user_id,
    )
    return {c["name"] for c in contacts}


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def connected_entity(db, test_entity):
    test_entity.status = "connected"
    test_entity.xero_org_id = "org-abc"
    test_entity.save()
    return test_entity


@pytest.fixture
def disconnected_entity(db, test_entity):
    test_entity.status = "disconnected"
    test_entity.xero_org_id = "org-abc"
    test_entity.save()
    return test_entity


@pytest.fixture
def user_with_token(db, test_user):
    give_xero_token(test_user, "fake-xero-token")
    return test_user


@pytest.fixture
def _seed_db_contacts(db, test_entity):
    """DB contacts with mixed xero_org_id states (realistic production data)."""
    for i, (name, org_id, cat) in enumerate(
        [
            ("Alpha Supplies", "org-abc", "SUPPLIER"),
            ("Beta Corp", "org-abc", None),
            ("Gamma Ltd", None, "expense_contact"),
            ("Delta Inc", "", "director_contact"),
            ("Epsilon Trading", "org-abc", "cashsale_contact"),
        ],
        start=1,
    ):
        XeroContactSync.objects.create(
            id=uuid.uuid5(uuid.NAMESPACE_URL, f"contact-{i}"),  # a uuid column since C5
            entity_id=test_entity.id,
            xero_contact_id=f"xero-{i:03d}",
            xero_org_id=org_id,
            name=name,
            category=cat,
        )


FAKE_XERO_API_CONTACTS = [
    {"ContactID": "xero-001", "Name": "Alpha Supplies"},
    {"ContactID": "xero-002", "Name": "Beta Corp"},
    {"ContactID": "xero-006", "Name": "Zeta Holdings"},
    {"ContactID": "xero-007", "Name": "Eta Services"},
]


def _mock_xero_ok():
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"Contacts": FAKE_XERO_API_CONTACTS}
    return resp


def _mock_xero_fail():
    resp = MagicMock()
    resp.status_code = 500
    resp.text = "Internal Server Error"
    return resp


# ── Tests ────────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestExactMatchConnectedXeroOk:
    """Scenario A: Entity connected, Xero API succeeds for both modules."""

    def test_same_names(self, connected_entity, user_with_token, test_user_entity):
        m1_names = _module1_connected_xero_ok(FAKE_XERO_API_CONTACTS)

        with patch(
            "bills.services.contact_service.requests.get",
            return_value=_mock_xero_ok(),
        ):
            m2_names = _module2_result_names(
                connected_entity.id,
                user_with_token.id,
            )

        assert m1_names == m2_names, (
            f"MISMATCH when both use Xero API.\n"
            f"  Module 1: {sorted(m1_names)}\n"
            f"  Module 2: {sorted(m2_names)}"
        )


@pytest.mark.django_db
class TestExactMatchConnectedXeroFails:
    """Scenario B: Entity connected but Xero fails — both fall back to DB."""

    def test_same_names(
        self,
        connected_entity,
        user_with_token,
        test_user_entity,
        _seed_db_contacts,
    ):
        m1_names = _module1_db_fallback(connected_entity.id)

        with patch(
            "bills.services.contact_service.requests.get",
            return_value=_mock_xero_fail(),
        ):
            m2_names = _module2_result_names(
                connected_entity.id,
                user_with_token.id,
            )

        assert m1_names == m2_names, (
            f"MISMATCH on DB fallback (connected).\n"
            f"  Module 1: {sorted(m1_names)}\n"
            f"  Module 2: {sorted(m2_names)}"
        )

    def test_includes_contacts_without_xero_org_id(
        self,
        connected_entity,
        user_with_token,
        test_user_entity,
        _seed_db_contacts,
    ):
        with patch(
            "bills.services.contact_service.requests.get",
            return_value=_mock_xero_fail(),
        ):
            m2_names = _module2_result_names(
                connected_entity.id,
                user_with_token.id,
            )

        assert "Gamma Ltd" in m2_names
        assert "Delta Inc" in m2_names


@pytest.mark.django_db
class TestExactMatchDisconnected:
    """Scenario C: Entity disconnected — both modules use DB only."""

    def test_same_names(
        self,
        disconnected_entity,
        test_user,
        test_user_entity,
        _seed_db_contacts,
    ):
        m1_names = _module1_db_fallback(disconnected_entity.id)
        m2_names = _module2_result_names(disconnected_entity.id, test_user.id)

        assert m1_names == m2_names, (
            f"MISMATCH on DB fallback (disconnected).\n"
            f"  Module 1: {sorted(m1_names)}\n"
            f"  Module 2: {sorted(m2_names)}"
        )

    def test_all_five_present(
        self,
        disconnected_entity,
        test_user,
        test_user_entity,
        _seed_db_contacts,
    ):
        m1_names = _module1_db_fallback(disconnected_entity.id)
        m2_names = _module2_result_names(disconnected_entity.id, test_user.id)

        expected = {
            "Alpha Supplies",
            "Beta Corp",
            "Gamma Ltd",
            "Delta Inc",
            "Epsilon Trading",
        }
        assert m1_names == expected
        assert m2_names == expected

    def test_contacts_with_null_org_id_included(
        self,
        disconnected_entity,
        test_user,
        test_user_entity,
        _seed_db_contacts,
    ):
        m2_names = _module2_result_names(disconnected_entity.id, test_user.id)
        assert "Gamma Ltd" in m2_names
        assert "Delta Inc" in m2_names

    def test_contacts_with_every_category_included(
        self,
        disconnected_entity,
        test_user,
        test_user_entity,
        _seed_db_contacts,
    ):
        m2_names = _module2_result_names(disconnected_entity.id, test_user.id)
        assert "Epsilon Trading" in m2_names  # cashsale_contact
        assert "Delta Inc" in m2_names  # director_contact
        assert "Gamma Ltd" in m2_names  # expense_contact


@pytest.mark.django_db
def test_duplicate_display_names_collapsed_to_one_row(db, test_entity, test_user):
    """Picker dedupes by normalized name; first row after sort wins (smaller xero_contact_id)."""
    test_entity.status = "disconnected"
    test_entity.save()
    eid = test_entity.id
    uid = test_user.id
    for cid, xid in [("dup-a", "xid-a"), ("dup-b", "xid-b")]:
        XeroContactSync.objects.create(
            id=_uid(cid),
            entity_id=eid,
            xero_contact_id=xid,
            xero_org_id=None,
            name="Same Name",
            category=None,
        )
    contacts = get_entity_bill_contacts(entity_id=eid, jwt_user_id=uid)
    assert len(contacts) == 1
    assert contacts[0]["xero_contact_id"] == "xid-a"
    assert contacts[0]["name"] == "Same Name"
