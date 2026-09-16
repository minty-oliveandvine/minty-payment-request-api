"""
Tests for republishing a bill after the entity moves to a different Xero org.

An entity can be reconnected to another Xero organisation. The invoice id
recorded by a publish under the old org does not exist in the new one, so
reusing it sends a valid tenant header with a foreign invoice id: Xero rejects
it and the bill can never be published again.

There is no column recording which org a sync went to. The org is read back
from the sync's payload row, where ``_sanitise_headers`` leaves
``Xero-Tenant-Id`` intact.

Coverage matrix
---------------
Org selection
  TC-ORG-001  Prior sync recorded against org A, entity now on org B -> PUT (create)
  TC-ORG-002  Prior sync recorded against the current org -> POST (update), unchanged
  TC-ORG-003  Prior sync with no payload row (org unrecorded) -> POST (update), unchanged
  TC-ORG-004  Newest sync is org A, an older one is org B (current) -> the org B one wins

Cleanup on switch
  TC-ORG-010  Org mismatch clears bill.xero_contact_id before the payload is built
  TC-ORG-011  Contact heal after a switch ignores contacts belonging to the old org

Account codes
  TC-ORG-020  Org mismatch + code missing from the new org -> raises naming it, no HTTP
  TC-ORG-021  Org mismatch + all codes present -> proceeds and creates
  TC-ORG-022  A missing code on a SAME-org republish is not checked (no behaviour change)

Bank slips
  TC-ORG-030  upload_bankslip_to_xero refuses a sync from another org
"""

import datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from bills.models import (
    Bill,
    BillLineItem,
    EntityBillAccountXero,
    XeroBillSync,
    XeroBillSyncPayload,
)
from bills.services.xero_publish_service import (
    publish_bill_to_xero,
    upload_bankslip_to_xero,
)
from core.exceptions import BillValidationError
from shared_models.models import Entity, User, UserEntity, XeroContactSync

ORG_A = "xero-org-OLD"
ORG_B = "xero-org-NEW"
OLD_INVOICE_ID = "invoice-in-org-a"
NEW_INVOICE_ID = "invoice-in-org-b"
ACCESS_TOKEN = "fake-bearer-token"
USER_ID = "f9f70ce4-fbc3-507e-99f7-ec48f3ea2ac1"  # was "org-switch-user-001"; user.id is a uuid now

_PUT_PATH = "bills.services.xero_publish_service.requests.put"
_POST_PATH = "bills.services.xero_publish_service.requests.post"
_UPLOAD_ATTACHMENTS_PATH = (
    "bills.services.xero_publish_service._upload_bill_attachments_to_xero"
)
_UPLOAD_BANKSLIPS_PATH = (
    "bills.services.xero_publish_service._upload_existing_bankslips_to_xero"
)


def _xero_200(invoice_id=NEW_INVOICE_ID) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {"Content-Type": "application/json"}
    resp.json.return_value = {
        "Invoices": [
            {
                "InvoiceID": invoice_id,
                "InvoiceNumber": "INV-9001",
                "Status": "AUTHORISED",
                "AmountDue": 500.0,
                "AmountPaid": 0.0,
                "Total": 500.0,
                "CurrencyCode": "HKD",
                "LineItems": [],
            }
        ]
    }
    resp.text = "{}"
    return resp


# ---------------------------------------------------------------- fixtures


@pytest.fixture
def entity_on_org_b(db) -> Entity:
    """Entity whose CURRENT Xero org is B (it used to be on A)."""
    return Entity.objects.create(
        id="switch-entity-001",
        name="Switched Entity",
        country_code="HK",
        currency_id="11111111-1111-1111-1111-111111111111",
        status="connected",
        xero_org_id=ORG_B,
    )


@pytest.fixture
def switch_user(db) -> User:
    return User.objects.create(
        id=USER_ID,
        email="switcher@minty.com",
        password="hashed_pw",
        first_name="Switch",
        last_name="Er",
        username="switcher",
        system_role="normal",
    )


@pytest.fixture
def switch_user_entity(db, switch_user, entity_on_org_b) -> UserEntity:
    return UserEntity.objects.create(
        user=switch_user, entity=entity_on_org_b, role="admin"
    )


@pytest.fixture
def switched_bill(db, entity_on_org_b, switch_user) -> Bill:
    return Bill.objects.create(
        entity_id=entity_on_org_b.id,
        contact="Acme Corp",
        xero_contact_id="contact-from-org-a",
        status=Bill.Status.AUTHORISED,
        amount=Decimal("500.00"),
        description="Office supplies",
        reference="INV-LOCAL-001",
        invoice_date=datetime.date(2026, 4, 1),
        due_date=datetime.date(2026, 4, 30),
        currency_code="HKD",
        xero_account_code="200",
        uploaded_by=switch_user.id,
    )


@pytest.fixture
def contact_in_org_b(db, entity_on_org_b) -> XeroContactSync:
    """The bill's contact, as it exists in the new org."""
    return XeroContactSync.objects.create(
        id="contact-row-org-b",
        entity_id=entity_on_org_b.id,
        xero_contact_id="contact-from-org-b",
        xero_org_id=ORG_B,
        name="Acme Corp",
    )


@pytest.fixture
def account_200_in_org_b(db, entity_on_org_b) -> EntityBillAccountXero:
    """The bill's account code exists in the new org."""
    return EntityBillAccountXero.objects.create(
        entity_id=entity_on_org_b.id,
        account_code="200",
        account_name="Office Expenses",
        account_type="EXPENSE",
    )


def _make_sync(bill, invoice_id, org_id, *, when_hour=10, with_payload=True):
    """A successful sync, optionally carrying the org in its payload headers.

    ``with_payload=False`` models a legacy row written before the payload
    captured request headers: the org reads back as "" (unknown).
    """
    sync = XeroBillSync.objects.create(
        bill=bill,
        sync_direction=XeroBillSync.SyncDirection.OUTBOUND,
        sync_type=XeroBillSync.SyncType.CREATE_INVOICE,
        sync_status=XeroBillSync.SyncStatus.SUCCESS,
        request_type="ACCPAY",
        request_status="AUTHORISED",
        response_invoice_id=invoice_id,
        response_invoice_number="INV-0042",
        idempotency_key="prior-key-%s-%s" % (org_id, when_hour),
        requested_by=USER_ID,
        requested_at=datetime.datetime(
            2026, 4, 1, when_hour, 0, 0, tzinfo=datetime.timezone.utc
        ),
    )
    if with_payload:
        XeroBillSyncPayload.objects.create(
            xero_bill_sync=sync,
            request_json={},
            response_json={},
            # Exactly what _sanitise_headers leaves behind on a real publish.
            request_headers={
                "Xero-Tenant-Id": org_id,
                "Content-Type": "application/json",
            },
            response_headers={},
        )
    return sync


# ---------------------------------------------------------- org selection


@pytest.mark.django_db
class TestOrgSelection:
    def test_foreign_org_sync_forces_create(
        self, switched_bill, entity_on_org_b, switch_user_entity,
        account_200_in_org_b, contact_in_org_b,
    ):
        """TC-ORG-001: sync from org A, entity on org B -> PUT, not POST."""
        _make_sync(switched_bill, OLD_INVOICE_ID, ORG_A)

        with (
            patch(_PUT_PATH, return_value=_xero_200()) as mock_put,
            patch(_POST_PATH) as mock_post,
            patch(_UPLOAD_ATTACHMENTS_PATH),
            patch(_UPLOAD_BANKSLIPS_PATH),
        ):
            publish_bill_to_xero(
                str(switched_bill.id), entity_on_org_b.id, USER_ID, ACCESS_TOKEN
            )

        assert mock_put.called, "should create a fresh invoice in the new org"
        assert not mock_post.called, "must not update the old org's invoice"
        assert OLD_INVOICE_ID not in mock_put.call_args.args[0]

    def test_same_org_sync_still_updates(
        self, switched_bill, entity_on_org_b, switch_user_entity
    ):
        """TC-ORG-002: sync recorded against the current org -> POST as before."""
        _make_sync(switched_bill, NEW_INVOICE_ID, ORG_B)

        with (
            patch(_PUT_PATH) as mock_put,
            patch(_POST_PATH, return_value=_xero_200()) as mock_post,
            patch(_UPLOAD_ATTACHMENTS_PATH),
            patch(_UPLOAD_BANKSLIPS_PATH),
        ):
            publish_bill_to_xero(
                str(switched_bill.id), entity_on_org_b.id, USER_ID, ACCESS_TOKEN
            )

        assert mock_post.called
        assert not mock_put.called
        assert NEW_INVOICE_ID in mock_post.call_args.args[0]

    def test_unrecorded_org_is_treated_as_current(
        self, switched_bill, entity_on_org_b, switch_user_entity
    ):
        """TC-ORG-003: legacy sync with no payload -> unchanged behaviour.

        Treating unknown as a mismatch would create a duplicate invoice in the
        SAME org, which is worse than the stuck state this guards against.
        """
        _make_sync(switched_bill, NEW_INVOICE_ID, ORG_A, with_payload=False)

        with (
            patch(_PUT_PATH) as mock_put,
            patch(_POST_PATH, return_value=_xero_200()) as mock_post,
            patch(_UPLOAD_ATTACHMENTS_PATH),
            patch(_UPLOAD_BANKSLIPS_PATH),
        ):
            publish_bill_to_xero(
                str(switched_bill.id), entity_on_org_b.id, USER_ID, ACCESS_TOKEN
            )

        assert mock_post.called, "a legacy sync must keep updating in place"
        assert not mock_put.called

    def test_older_matching_sync_beats_newer_foreign_one(
        self, switched_bill, entity_on_org_b, switch_user_entity
    ):
        """TC-ORG-004: newest is org A, an older one is org B -> org B wins."""
        _make_sync(switched_bill, NEW_INVOICE_ID, ORG_B, when_hour=9)
        _make_sync(switched_bill, OLD_INVOICE_ID, ORG_A, when_hour=11)

        with (
            patch(_PUT_PATH) as mock_put,
            patch(_POST_PATH, return_value=_xero_200()) as mock_post,
            patch(_UPLOAD_ATTACHMENTS_PATH),
            patch(_UPLOAD_BANKSLIPS_PATH),
        ):
            publish_bill_to_xero(
                str(switched_bill.id), entity_on_org_b.id, USER_ID, ACCESS_TOKEN
            )

        assert mock_post.called
        assert NEW_INVOICE_ID in mock_post.call_args.args[0]
        assert not mock_put.called


# ------------------------------------------------------- cleanup on switch


@pytest.mark.django_db
class TestCleanupOnSwitch:
    def test_stale_contact_id_is_cleared(
        self, switched_bill, entity_on_org_b, switch_user_entity,
        account_200_in_org_b, contact_in_org_b,
    ):
        """TC-ORG-010: the org-A contact id must not survive into the new org."""
        _make_sync(switched_bill, OLD_INVOICE_ID, ORG_A)

        with (
            patch(_PUT_PATH, return_value=_xero_200()),
            patch(_POST_PATH),
            patch(_UPLOAD_ATTACHMENTS_PATH),
            patch(_UPLOAD_BANKSLIPS_PATH),
        ):
            publish_bill_to_xero(
                str(switched_bill.id), entity_on_org_b.id, USER_ID, ACCESS_TOKEN
            )

        switched_bill.refresh_from_db()
        assert switched_bill.xero_contact_id == "contact-from-org-b"

    def test_heal_ignores_old_org_contacts(
        self, switched_bill, entity_on_org_b, switch_user_entity, account_200_in_org_b
    ):
        """TC-ORG-011: a contact belonging to org A must not be re-attached."""
        _make_sync(switched_bill, OLD_INVOICE_ID, ORG_A)
        XeroContactSync.objects.create(
            id="contact-row-org-a",
            entity_id=entity_on_org_b.id,
            xero_contact_id="contact-from-org-a",
            xero_org_id=ORG_A,
            name="Acme Corp",
        )

        with (
            patch(_PUT_PATH, return_value=_xero_200()),
            patch(_POST_PATH),
            patch(_UPLOAD_ATTACHMENTS_PATH),
            patch(_UPLOAD_BANKSLIPS_PATH),
            pytest.raises(BillValidationError, match="could not be matched"),
        ):
            publish_bill_to_xero(
                str(switched_bill.id), entity_on_org_b.id, USER_ID, ACCESS_TOKEN
            )


# ------------------------------------------------------------ account codes


@pytest.mark.django_db
class TestAccountCodes:
    def test_missing_code_blocks_publish(
        self, switched_bill, entity_on_org_b, switch_user_entity
    ):
        """TC-ORG-020: no matching account in the new org -> named error, no HTTP."""
        _make_sync(switched_bill, OLD_INVOICE_ID, ORG_A)

        with (
            patch(_PUT_PATH) as mock_put,
            patch(_POST_PATH) as mock_post,
            patch(_UPLOAD_ATTACHMENTS_PATH),
            patch(_UPLOAD_BANKSLIPS_PATH),
            pytest.raises(BillValidationError, match="Account code 200"),
        ):
            publish_bill_to_xero(
                str(switched_bill.id), entity_on_org_b.id, USER_ID, ACCESS_TOKEN
            )

        assert not mock_put.called
        assert not mock_post.called

    def test_line_item_codes_are_checked(
        self, switched_bill, entity_on_org_b, switch_user_entity, account_200_in_org_b
    ):
        """A line item's own code is checked, not just the bill-level fallback."""
        BillLineItem.objects.create(
            bill=switched_bill,
            description="Consulting hours",
            quantity=Decimal("1.0000"),
            unit_amount=Decimal("500.00"),
            line_amount=Decimal("500.00"),
            account_code="999",
            tax_type="NONE",
            sort_order=0,
        )
        _make_sync(switched_bill, OLD_INVOICE_ID, ORG_A)

        with (
            patch(_PUT_PATH),
            patch(_POST_PATH),
            patch(_UPLOAD_ATTACHMENTS_PATH),
            patch(_UPLOAD_BANKSLIPS_PATH),
            pytest.raises(BillValidationError, match="Account code 999"),
        ):
            publish_bill_to_xero(
                str(switched_bill.id), entity_on_org_b.id, USER_ID, ACCESS_TOKEN
            )

    def test_present_code_proceeds(
        self, switched_bill, entity_on_org_b, switch_user_entity,
        account_200_in_org_b, contact_in_org_b,
    ):
        """TC-ORG-021: code exists in the new org -> creates as normal."""
        _make_sync(switched_bill, OLD_INVOICE_ID, ORG_A)

        with (
            patch(_PUT_PATH, return_value=_xero_200()) as mock_put,
            patch(_POST_PATH),
            patch(_UPLOAD_ATTACHMENTS_PATH),
            patch(_UPLOAD_BANKSLIPS_PATH),
        ):
            publish_bill_to_xero(
                str(switched_bill.id), entity_on_org_b.id, USER_ID, ACCESS_TOKEN
            )

        assert mock_put.called

    def test_same_org_republish_skips_the_check(
        self, switched_bill, entity_on_org_b, switch_user_entity
    ):
        """TC-ORG-022: no org change means no new validation.

        There is deliberately no EntityBillAccountXero row here. A same-org
        republish of a bill whose code is not in that table must behave exactly
        as it did before this check existed.
        """
        _make_sync(switched_bill, NEW_INVOICE_ID, ORG_B)

        with (
            patch(_PUT_PATH),
            patch(_POST_PATH, return_value=_xero_200()) as mock_post,
            patch(_UPLOAD_ATTACHMENTS_PATH),
            patch(_UPLOAD_BANKSLIPS_PATH),
        ):
            publish_bill_to_xero(
                str(switched_bill.id), entity_on_org_b.id, USER_ID, ACCESS_TOKEN
            )

        assert mock_post.called


# ---------------------------------------------------------------- bankslips


@pytest.mark.django_db
class TestBankslipOrgGuard:
    def test_bankslip_refuses_foreign_org_sync(
        self, switched_bill, entity_on_org_b, switch_user_entity
    ):
        """TC-ORG-030: a bank slip must not be attached to the old org's invoice."""
        switched_bill.published = Bill.PublishStatus.PUBLISHED
        switched_bill.save(update_fields=["published"])
        _make_sync(switched_bill, OLD_INVOICE_ID, ORG_A)

        with pytest.raises(BillValidationError, match="No successful Xero sync"):
            upload_bankslip_to_xero(
                str(switched_bill.id),
                "payment-does-not-matter",
                entity_on_org_b.id,
                USER_ID,
                ACCESS_TOKEN,
            )
