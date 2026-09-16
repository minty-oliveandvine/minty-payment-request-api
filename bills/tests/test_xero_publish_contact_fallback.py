"""
Tests for the xero_contact_id fallback/heal logic in publish_bill_to_xero().

Coverage matrix
───────────────
TC-FALL-001  xero_contact_id already set → fallback lookup skipped, publish proceeds
TC-FALL-002  xero_contact_id empty, exact-case match in XeroContactSync → bill healed
TC-FALL-003  xero_contact_id empty, case-insensitive match in XeroContactSync → bill healed
TC-FALL-004  xero_contact_id empty, no matching XeroContactSync row → BillValidationError
TC-FALL-005  xero_contact_id empty, sync row exists but its xero_contact_id is empty → BillValidationError
TC-FALL-006  Successful fallback persists xero_contact_id to the database
"""

import datetime
import uuid
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from bills.models import Bill
from bills.services.xero_publish_service import publish_bill_to_xero
from core.exceptions import BillValidationError
from shared_models.models import Entity, User, UserEntity, XeroContactSync

# ═══════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════

FAKE_ORG_ID = "xero-org-fallback-001"
FAKE_ACCESS_TOKEN = "fake-bearer-token-fallback"
FAKE_USER_ID = "e37ecb13-fdc7-5b79-8adb-8398e8d00c1d"  # was "fallback-user-001"; user.id is a uuid now
FAKE_INVOICE_ID = "xero-invoice-fallback-0001"
FAKE_INVOICE_NUMBER = "INV-FALL-001"
FAKE_XERO_CONTACT_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

# Patch targets — mirror what test_xero_publish.py uses
_PUT_PATH = "bills.services.xero_publish_service.requests.put"
_POST_PATH = "bills.services.xero_publish_service.requests.post"
_UPLOAD_ATTACHMENTS_PATH = (
    "bills.services.xero_publish_service._upload_bill_attachments_to_xero"
)
_UPLOAD_BANKSLIPS_PATH = (
    "bills.services.xero_publish_service._upload_existing_bankslips_to_xero"
)


# ═══════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════


def _xero_200(
    invoice_id=FAKE_INVOICE_ID, invoice_number=FAKE_INVOICE_NUMBER
) -> MagicMock:
    """Return a mock requests.Response that looks like a successful Xero 200."""
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {"Content-Type": "application/json"}
    resp.json.return_value = {
        "Invoices": [
            {
                "InvoiceID": invoice_id,
                "InvoiceNumber": invoice_number,
                "Status": "AUTHORISED",
                "AmountDue": 500.00,
                "AmountPaid": 0.00,
                "Total": 500.00,
                "CurrencyCode": "HKD",
                "LineItems": [
                    {
                        "LineItemID": "li-fall-001",
                        "Description": "Locks service",
                        "Quantity": 1.0,
                        "UnitAmount": 500.00,
                        "LineAmount": 500.00,
                        "TaxType": "NONE",
                        "TaxAmount": 0.00,
                        "AccountCode": "200",
                        "AccountID": "acc-fall-001",
                        "ValidationErrors": [],
                    }
                ],
            }
        ],
        "ProviderName": "Minty Fallback Test",
        "DateTimeUTC": "/Date(1712534400000+0000)/",
    }
    return resp


# ═══════════════════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def fall_entity(db) -> Entity:
    return Entity.objects.create(
        id="13bd2fba-943a-598f-89b0-76e824cd30dd",
        name="Fallback Entity",
        country_code="HK",
        currency_id="11111111-1111-1111-1111-111111111111",
        status="connected",
        xero_org_id=FAKE_ORG_ID,
    )


@pytest.fixture
def fall_user(db) -> User:
    return User.objects.create(
        id=FAKE_USER_ID,
        email="fallback@minty.com",
        password="hashed_pw",
        first_name="Fall",
        last_name="Back",
        username="fallbackuser",
        system_role="normal",
    )


@pytest.fixture
def fall_user_entity(db, fall_user, fall_entity) -> UserEntity:
    return UserEntity.objects.create(
        user=fall_user,
        entity=fall_entity,
        role="admin",
    )


@pytest.fixture
def bill_with_contact_id(db, fall_entity, fall_user) -> Bill:
    """AUTHORISED bill that already has a populated xero_contact_id."""
    return Bill.objects.create(
        entity_id=fall_entity.id,
        contact="24 Locks",
        xero_contact_id=FAKE_XERO_CONTACT_UUID,
        status=Bill.Status.AUTHORISED,
        amount=Decimal("500.00"),
        description="Lock service invoice",
        reference="INV-LOCK-001",
        invoice_date=datetime.date(2026, 4, 1),
        due_date=datetime.date(2026, 4, 30),
        currency_code="HKD",
        xero_account_code="200",
        uploaded_by=fall_user.id,
    )


@pytest.fixture
def bill_without_contact_id(db, fall_entity, fall_user) -> Bill:
    """AUTHORISED bill with xero_contact_id left blank (the pre-fix scenario)."""
    return Bill.objects.create(
        entity_id=fall_entity.id,
        contact="24 Locks",
        xero_contact_id="",
        status=Bill.Status.AUTHORISED,
        amount=Decimal("500.00"),
        description="Lock service invoice",
        reference="INV-LOCK-002",
        invoice_date=datetime.date(2026, 4, 1),
        due_date=datetime.date(2026, 4, 30),
        currency_code="HKD",
        xero_account_code="200",
        uploaded_by=fall_user.id,
    )


@pytest.fixture
def contact_sync_row(db, fall_entity) -> XeroContactSync:
    """A XeroContactSync row with a valid UUID for '24 Locks'."""
    return XeroContactSync.objects.create(
        id=str(uuid.uuid4()),
        entity_id=fall_entity.id,
        xero_contact_id=FAKE_XERO_CONTACT_UUID,
        xero_org_id=FAKE_ORG_ID,
        name="24 Locks",
    )


# ═══════════════════════════════════════════════════════════════════════════
# TESTS
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.django_db
class TestContactFallbackSkippedWhenContactIdPresent:
    """TC-FALL-001: bill.xero_contact_id is already set — fallback is never triggered."""

    def test_publish_proceeds_without_querying_contact_sync(
        self, bill_with_contact_id, fall_entity, fall_user_entity
    ):
        """XeroContactSync.objects.filter should not be called when xero_contact_id is set."""
        with (
            patch(_PUT_PATH, return_value=_xero_200()),
            patch(_UPLOAD_ATTACHMENTS_PATH),
            patch(_UPLOAD_BANKSLIPS_PATH),
            patch(
                "bills.services.xero_publish_service.XeroContactSync.objects"
            ) as mock_qs,
        ):
            publish_bill_to_xero(
                str(bill_with_contact_id.id),
                fall_entity.id,
                FAKE_USER_ID,
                FAKE_ACCESS_TOKEN,
            )

        mock_qs.filter.assert_not_called()

    def test_bill_published_successfully_when_contact_id_present(
        self, bill_with_contact_id, fall_entity, fall_user_entity
    ):
        with (
            patch(_PUT_PATH, return_value=_xero_200()),
            patch(_UPLOAD_ATTACHMENTS_PATH),
            patch(_UPLOAD_BANKSLIPS_PATH),
        ):
            publish_bill_to_xero(
                str(bill_with_contact_id.id),
                fall_entity.id,
                FAKE_USER_ID,
                FAKE_ACCESS_TOKEN,
            )

        bill_with_contact_id.refresh_from_db()
        assert bill_with_contact_id.published == Bill.PublishStatus.PUBLISHED


@pytest.mark.django_db
class TestContactFallbackSuccessExactCase:
    """TC-FALL-002: xero_contact_id empty, exact-case match in XeroContactSync → healed."""

    def test_bill_xero_contact_id_healed_in_memory(
        self, bill_without_contact_id, contact_sync_row, fall_entity, fall_user_entity
    ):
        """After the fallback, the bill object used by the service carries the UUID."""
        with (
            patch(_PUT_PATH, return_value=_xero_200()),
            patch(_UPLOAD_ATTACHMENTS_PATH),
            patch(_UPLOAD_BANKSLIPS_PATH),
        ):
            publish_bill_to_xero(
                str(bill_without_contact_id.id),
                fall_entity.id,
                FAKE_USER_ID,
                FAKE_ACCESS_TOKEN,
            )

        # The PUT payload must have carried the healed contact ID to Xero.
        # We verify indirectly: publish succeeds (no exception) and the bill
        # ends up in PUBLISHED state, which only happens when the payload was valid.
        bill_without_contact_id.refresh_from_db()
        assert bill_without_contact_id.published == Bill.PublishStatus.PUBLISHED

    def test_xero_contact_id_written_into_put_payload(
        self, bill_without_contact_id, contact_sync_row, fall_entity, fall_user_entity
    ):
        """The ContactID field in the PUT body must be the healed UUID, not ''."""
        with (
            patch(_PUT_PATH, return_value=_xero_200()) as mock_put,
            patch(_UPLOAD_ATTACHMENTS_PATH),
            patch(_UPLOAD_BANKSLIPS_PATH),
        ):
            publish_bill_to_xero(
                str(bill_without_contact_id.id),
                fall_entity.id,
                FAKE_USER_ID,
                FAKE_ACCESS_TOKEN,
            )

        put_body = mock_put.call_args[1].get("json") or mock_put.call_args[0][1]
        contact_id_sent = put_body["Invoices"][0]["Contact"]["ContactID"]
        assert (
            contact_id_sent == FAKE_XERO_CONTACT_UUID
        ), f"Expected ContactID={FAKE_XERO_CONTACT_UUID!r}, got {contact_id_sent!r}"


@pytest.mark.django_db
class TestContactFallbackSuccessCaseInsensitive:
    """TC-FALL-003: bill.contact in lowercase, XeroContactSync name in title-case → still matches."""

    def test_case_insensitive_match_heals_bill(
        self, db, fall_entity, fall_user, fall_user_entity
    ):
        # Bill has lowercase contact name
        bill = Bill.objects.create(
            entity_id=fall_entity.id,
            contact="24 locks",  # all-lowercase
            xero_contact_id="",
            status=Bill.Status.AUTHORISED,
            amount=Decimal("500.00"),
            description="Case test",
            reference="INV-CASE-001",
            invoice_date=datetime.date(2026, 4, 1),
            due_date=datetime.date(2026, 4, 30),
            currency_code="HKD",
            xero_account_code="200",
            uploaded_by=fall_user.id,
        )
        # Sync row has title-case name
        XeroContactSync.objects.create(
            id=str(uuid.uuid4()),
            entity_id=fall_entity.id,
            xero_contact_id=FAKE_XERO_CONTACT_UUID,
            xero_org_id=FAKE_ORG_ID,
            name="24 Locks",  # title-case
        )

        with (
            patch(_PUT_PATH, return_value=_xero_200()) as mock_put,
            patch(_UPLOAD_ATTACHMENTS_PATH),
            patch(_UPLOAD_BANKSLIPS_PATH),
        ):
            publish_bill_to_xero(
                str(bill.id),
                fall_entity.id,
                FAKE_USER_ID,
                FAKE_ACCESS_TOKEN,
            )

        put_body = mock_put.call_args[1].get("json") or mock_put.call_args[0][1]
        contact_id_sent = put_body["Invoices"][0]["Contact"]["ContactID"]
        assert (
            contact_id_sent == FAKE_XERO_CONTACT_UUID
        ), f"Case-insensitive lookup failed: ContactID={contact_id_sent!r}"


@pytest.mark.django_db
class TestContactFallbackFailureNoSyncRow:
    """TC-FALL-004: xero_contact_id empty and no XeroContactSync row → BillValidationError."""

    def test_raises_bill_validation_error(
        self, bill_without_contact_id, fall_entity, fall_user_entity
    ):
        # Deliberately do NOT create any XeroContactSync row
        with (
            patch(_PUT_PATH),
            patch(_UPLOAD_ATTACHMENTS_PATH),
            patch(_UPLOAD_BANKSLIPS_PATH),
        ):
            with pytest.raises(BillValidationError):
                publish_bill_to_xero(
                    str(bill_without_contact_id.id),
                    fall_entity.id,
                    FAKE_USER_ID,
                    FAKE_ACCESS_TOKEN,
                )

    def test_error_message_includes_contact_name(
        self, bill_without_contact_id, fall_entity, fall_user_entity
    ):
        with (
            patch(_PUT_PATH),
            patch(_UPLOAD_ATTACHMENTS_PATH),
            patch(_UPLOAD_BANKSLIPS_PATH),
        ):
            with pytest.raises(BillValidationError) as exc_info:
                publish_bill_to_xero(
                    str(bill_without_contact_id.id),
                    fall_entity.id,
                    FAKE_USER_ID,
                    FAKE_ACCESS_TOKEN,
                )

        assert "24 Locks" in str(
            exc_info.value
        ), f"Error message should mention the contact name; got: {exc_info.value!r}"

    def test_xero_put_never_called(
        self, bill_without_contact_id, fall_entity, fall_user_entity
    ):
        """No HTTP call should be made when the fallback cannot resolve the contact."""
        with (
            patch(_PUT_PATH) as mock_put,
            patch(_UPLOAD_ATTACHMENTS_PATH),
            patch(_UPLOAD_BANKSLIPS_PATH),
        ):
            with pytest.raises(BillValidationError):
                publish_bill_to_xero(
                    str(bill_without_contact_id.id),
                    fall_entity.id,
                    FAKE_USER_ID,
                    FAKE_ACCESS_TOKEN,
                )

        mock_put.assert_not_called()


@pytest.mark.django_db
class TestContactFallbackFailureEmptySyncContactId:
    """TC-FALL-005: XeroContactSync row exists but its own xero_contact_id is '' → BillValidationError."""

    def test_raises_bill_validation_error_when_sync_row_has_empty_contact_id(
        self, bill_without_contact_id, fall_entity, fall_user_entity
    ):
        # Sync row exists but carries no usable UUID
        XeroContactSync.objects.create(
            id=str(uuid.uuid4()),
            entity_id=fall_entity.id,
            xero_contact_id="",  # empty — the bad state the fix guards against
            xero_org_id=FAKE_ORG_ID,
            name="24 Locks",
        )

        with (
            patch(_PUT_PATH),
            patch(_UPLOAD_ATTACHMENTS_PATH),
            patch(_UPLOAD_BANKSLIPS_PATH),
        ):
            with pytest.raises(BillValidationError):
                publish_bill_to_xero(
                    str(bill_without_contact_id.id),
                    fall_entity.id,
                    FAKE_USER_ID,
                    FAKE_ACCESS_TOKEN,
                )

    def test_error_message_mentions_contact_name_when_sync_contact_id_empty(
        self, bill_without_contact_id, fall_entity, fall_user_entity
    ):
        XeroContactSync.objects.create(
            id=str(uuid.uuid4()),
            entity_id=fall_entity.id,
            xero_contact_id="",
            xero_org_id=FAKE_ORG_ID,
            name="24 Locks",
        )

        with (
            patch(_PUT_PATH),
            patch(_UPLOAD_ATTACHMENTS_PATH),
            patch(_UPLOAD_BANKSLIPS_PATH),
        ):
            with pytest.raises(BillValidationError) as exc_info:
                publish_bill_to_xero(
                    str(bill_without_contact_id.id),
                    fall_entity.id,
                    FAKE_USER_ID,
                    FAKE_ACCESS_TOKEN,
                )

        assert "24 Locks" in str(exc_info.value)


@pytest.mark.django_db
class TestContactFallbackPersistsToDatabase:
    """TC-FALL-006: After a successful fallback, xero_contact_id is written to the DB row."""

    def test_healed_contact_id_survives_db_round_trip(
        self, bill_without_contact_id, contact_sync_row, fall_entity, fall_user_entity
    ):
        assert (
            bill_without_contact_id.xero_contact_id == ""
        ), "Precondition: bill must start with an empty xero_contact_id"

        with (
            patch(_PUT_PATH, return_value=_xero_200()),
            patch(_UPLOAD_ATTACHMENTS_PATH),
            patch(_UPLOAD_BANKSLIPS_PATH),
        ):
            publish_bill_to_xero(
                str(bill_without_contact_id.id),
                fall_entity.id,
                FAKE_USER_ID,
                FAKE_ACCESS_TOKEN,
            )

        # Reload from the database — this confirms save(update_fields=["xero_contact_id"])
        # was called, not just an in-memory attribute assignment.
        bill_without_contact_id.refresh_from_db()
        assert (
            bill_without_contact_id.xero_contact_id == FAKE_XERO_CONTACT_UUID
        ), f"Expected healed UUID in DB, got: {bill_without_contact_id.xero_contact_id!r}"

    def test_healed_contact_id_is_isolated_to_correct_entity(
        self, db, fall_entity, fall_user, fall_user_entity
    ):
        """A sync row for a different entity must not heal a bill belonging to fall_entity."""
        other_entity = Entity.objects.create(
            id="76c7eb0e-e711-54bd-acaf-e22bda56239b",
            name="Other Entity",
            country_code="HK",
            currency_id="11111111-1111-1111-1111-111111111111",
            status="connected",
            xero_org_id="other-org-999",
        )
        # Sync row belongs to other_entity, not fall_entity
        XeroContactSync.objects.create(
            id=str(uuid.uuid4()),
            entity_id=other_entity.id,
            xero_contact_id=FAKE_XERO_CONTACT_UUID,
            xero_org_id="other-org-999",
            name="24 Locks",
        )
        bill = Bill.objects.create(
            entity_id=fall_entity.id,
            contact="24 Locks",
            xero_contact_id="",
            status=Bill.Status.AUTHORISED,
            amount=Decimal("500.00"),
            description="Isolation test",
            reference="INV-ISO-001",
            invoice_date=datetime.date(2026, 4, 1),
            due_date=datetime.date(2026, 4, 30),
            currency_code="HKD",
            xero_account_code="200",
            uploaded_by=fall_user.id,
        )

        with (
            patch(_PUT_PATH),
            patch(_UPLOAD_ATTACHMENTS_PATH),
            patch(_UPLOAD_BANKSLIPS_PATH),
        ):
            with pytest.raises(BillValidationError):
                publish_bill_to_xero(
                    str(bill.id),
                    fall_entity.id,
                    FAKE_USER_ID,
                    FAKE_ACCESS_TOKEN,
                )

        bill.refresh_from_db()
        assert (
            bill.xero_contact_id == ""
        ), "A sync row for a different entity must not heal a bill in the wrong entity"
