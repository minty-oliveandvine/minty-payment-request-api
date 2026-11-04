"""Reading an invoice to fill the new-bill form: ``POST /bills/extract/`` (``bills/api.py``) and
``bills/services/invoice_reader.py``, on ``minty_shared.ai`` (owner, 2026-11-04). The engine's
own cases (retries, timeouts, 429/5xx, cost, the tier tripwire) are minty-shared-py's
``tests/test_ai.py``.

No network: the model client is a stand-in - a test that reached Google would test Google."""

from __future__ import annotations

import json

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from minty_db.models import Entity, UserEntity, XeroContactSync
from minty_shared import ai

from bills.models import EntityBillAccountXero
from bills.services import invoice_reader

pytestmark = pytest.mark.django_db

PDF = b"%PDF-1.4 an invoice"
ORG = "org-current"
URL = "/api/v1/bills/extract/"


def _reply(**over) -> str:
    body = {
        "supplier_contact_id": "con-abc", "supplier_name": "ABC Printing", "supplier_confidence": 0.93,
        "account_code": "400", "account_confidence": 0.88,
        "amount_value": 1200.5, "amount_confidence": 0.97,
        "description_value": "Flyer printing", "description_confidence": 0.81,
        "invoice_date": "2026-10-20", "invoice_date_confidence": 0.95,
        "due_date": "2026-11-19", "due_date_confidence": 0.9,
        "currency": "HKD",
    }
    body.update(over)
    return json.dumps(body)


class FakeUsage:
    total_input_tokens = 5000
    total_output_tokens = 300
    total_thought_tokens = 400
    total_cached_tokens = 0


class FakeInteraction:
    def __init__(self, output_text=None, status="completed"):
        self.id = "interaction-1"
        self.output_text = output_text
        self.status = status
        self.errors = []
        self.usage = FakeUsage()


class FakeModel:
    def __init__(self):
        self.answer = FakeInteraction(_reply())
        self.requests = []
        self.interactions = self

    def create(self, **request):
        self.requests.append(request)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


@pytest.fixture
def model(monkeypatch) -> FakeModel:
    fake = FakeModel()
    monkeypatch.setattr(ai, "get_client", lambda timeout_s: fake)
    monkeypatch.setattr(ai.time, "sleep", lambda _s: None)
    for name, value in {"BILL_AI_ENABLED": "true", "BILL_AI_CONF_HIGH": "0.85",
                        "BILL_AI_CONF_MEDIUM": "0.60", "BILL_AI_MODEL": "gemini-3.8-flash"}.items():
        monkeypatch.setenv(name, value)
    invoice_reader.LIMITER.reset()
    return fake


@pytest.fixture
def lists(test_entity):
    """Two bill accounts on (400 Advertising, 310 Cost of Goods), one off, one deleted, one of a
    type bills may not use; two suppliers of the current Xero org, one of another org."""
    # By query, not instance.save(): a save turns the fixture's id into a UUID, which the
    # auth token fixture then cannot encode.
    Entity.objects.filter(id=test_entity.id).update(xero_org_id=ORG)
    for code, name, kind, active, deleted in [
        ("400", "Advertising", "EXPENSE", True, False),
        ("310", "Cost of Goods Sold", "DIRECTCOSTS", True, False),
        ("410", "Cleaning", "EXPENSE", False, False),
        ("420", "Old account", "EXPENSE", True, True),
        ("200", "Sales", "REVENUE", True, False),
    ]:
        EntityBillAccountXero.objects.create(entity_id=test_entity.id, account_code=code, account_name=name,
                                             account_type=kind, is_active=active, is_deleted=deleted)
    for xid, name, org in [("con-abc", "ABC Printing", ORG), ("con-taxi", "Taxi Co", ""),
                           ("con-other", "Other Org Ltd", "org-previous")]:
        XeroContactSync.objects.create(entity_id=test_entity.id, xero_contact_id=xid, name=name, xero_org_id=org)


def _extract(api_client, auth_headers, *, body=PDF, name="invoice.pdf", files=None):
    data = {}
    if files is not None:
        data["invoice"] = files
    elif body is not None:
        data["invoice"] = SimpleUploadedFile(name, body, content_type="application/octet-stream")
    return api_client.post(URL, data=data, **auth_headers)


# --- the route ---------------------------------------------------------------------------------


def test_an_invoice_is_read_against_the_company_s_bill_lists(api_client, auth_headers, test_user_entity,
                                                             lists, model):
    resp = _extract(api_client, auth_headers)

    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["reason"] is None
    s = body["suggestions"]
    assert s["supplier"] == {"value": "ABC Printing", "confidence": 0.93, "band": "high", "applied": True,
                             "contact_id": "con-abc"}
    assert (s["account"]["code"], s["account"]["value"]) == ("400", "400 - Advertising")
    assert s["amount"]["value"] == "1200.50"
    assert (s["invoice_date"]["value"], s["due_date"]["value"]) == ("2026-10-20", "2026-11-19")
    assert s["description"]["value"] == "Flyer printing"
    assert (s["currency"], s["entity_currency"]) == ("HKD", "HKD")
    [request] = model.requests
    assert request["input"][1]["type"] == "document"
    assert request["system_instruction"] == invoice_reader.INSTRUCTION


def test_the_prefix_carries_only_what_the_bill_form_offers(api_client, auth_headers, test_user_entity,
                                                           lists, model):
    _extract(api_client, auth_headers)

    prefix = model.requests[0]["input"][0]["text"]
    assert "Name: Test Entity" in prefix and "Base currency: HKD" in prefix
    assert "310 | Cost of Goods Sold" in prefix and "400 | Advertising" in prefix
    assert prefix.index("310 |") < prefix.index("400 |")  # by code: a byte-stable prefix
    for absent in ("410 |", "420 |", "200 |", "con-other"):  # off, deleted, not a bill type, old org
        assert absent not in prefix
    assert "con-abc | ABC Printing" in prefix and "con-taxi | Taxi Co" in prefix  # no org = legacy, kept
    assert "400" not in json.dumps(model.requests[0]["response_format"]["schema"])


@pytest.mark.parametrize("env, reason", [
    ({"BILL_AI_ENABLED": "false"}, "disabled"),
    ({"BILL_AI_RATE_USER_PER_MIN": "0"}, "rate_limited"),
    ({"BILL_AI_MAX_FILE_MB": "0"}, "file_too_large"),
], ids=["off", "rate-limited", "too-large"])
def test_refusals_past_the_checks_are_a_silent_no_suggestion(api_client, auth_headers, test_user_entity,
                                                             lists, model, monkeypatch, env, reason):
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    resp = _extract(api_client, auth_headers)

    assert resp.status_code == 200 and resp.json() == {"suggestions": None, "reason": reason}
    assert model.requests == []


def test_the_receipt_reader_s_switch_does_not_turn_on_the_invoice_reader(api_client, auth_headers,
                                                                         test_user_entity, lists, model,
                                                                         monkeypatch):
    monkeypatch.delenv("BILL_AI_ENABLED")
    monkeypatch.setenv("EXPENSE_AI_ENABLED", "true")

    assert _extract(api_client, auth_headers).json() == {"suggestions": None, "reason": "disabled"}


def test_a_company_with_nothing_to_choose_from_asks_no_question(api_client, auth_headers,
                                                                test_user_entity, model):
    resp = _extract(api_client, auth_headers)

    assert resp.status_code == 200 and resp.json() == {"suggestions": None, "reason": "no_context"}
    assert model.requests == []


def test_a_failure_of_the_model_is_a_silent_no_suggestion(api_client, auth_headers, test_user_entity,
                                                          lists, model):
    model.answer = TimeoutError("deadline exceeded")

    resp = _extract(api_client, auth_headers)

    assert resp.status_code == 200 and resp.json() == {"suggestions": None, "reason": "timeout"}


def test_one_log_line_with_numbers_and_never_the_values(api_client, auth_headers, test_user_entity,
                                                        lists, model, caplog):
    caplog.set_level("INFO", logger="minty-shared.ai")

    _extract(api_client, auth_headers)

    [line] = [r.getMessage() for r in caplog.records if "bill_ai: extract" in r.getMessage()]
    assert "tokens_in=5000" in line and "status=ok" in line
    assert "ABC" not in line and "1200" not in line and "Flyer" not in line


@pytest.mark.parametrize("body, message", [
    (None, "Attach the invoice to read it."),
    (b"PK\x03\x04 a word file", "Attach a PDF, JPEG or PNG file."),
    (b"", "That file is empty. Attach the invoice again."),
    (b"%PDF" + b"0" * (10 * 1024 * 1024), "Attach a file of 10 MB or less."),
], ids=["missing", "docx", "empty", "too-big"])
def test_a_file_the_reader_cannot_take_is_refused(api_client, auth_headers, test_user_entity, lists,
                                                  model, body, message):
    resp = _extract(api_client, auth_headers, body=body)

    assert resp.status_code == 422 and resp.json() == {"detail": message}
    assert model.requests == []


def test_two_files_are_refused(api_client, auth_headers, test_user_entity, lists, model):
    files = [SimpleUploadedFile("a.pdf", PDF), SimpleUploadedFile("b.pdf", PDF)]

    resp = _extract(api_client, auth_headers, files=files)

    assert resp.status_code == 422 and resp.json() == {"detail": "Attach one invoice at a time."}
    assert model.requests == []


def test_only_someone_who_may_create_a_bill_may_ask(api_client, auth_headers, test_user, test_entity,
                                                    lists, model):
    UserEntity.objects.create(user=test_user, entity=test_entity, role="entity_base")

    resp = _extract(api_client, auth_headers)

    assert resp.status_code == 403
    assert model.requests == []
    no_token = api_client.post(URL, data={"invoice": SimpleUploadedFile("a.pdf", PDF)})
    assert no_token.status_code == 401


# --- the reply is untrusted input --------------------------------------------------------------


def _read(api_client, auth_headers, model, **reply):
    model.answer = FakeInteraction(_reply(**reply))
    body = _extract(api_client, auth_headers).json()
    return body["suggestions"], body["reason"]


@pytest.mark.usefixtures("test_user_entity", "lists")
class TestTheReplyIsChecked:
    def test_a_supplier_not_sent_is_dropped_and_its_name_offered(self, api_client, auth_headers, model):
        s, _ = _read(api_client, auth_headers, model, supplier_contact_id="con-other",
                     supplier_name="Other Org Ltd")
        assert s["supplier"]["contact_id"] == "" and s["supplier"]["applied"] is False
        assert s["supplier"]["detected_name"] == "Other Org Ltd"
        assert s["amount"]["applied"] is True  # one bad field is not a bad reply

    def test_a_supplier_id_matches_whatever_its_case(self, api_client, auth_headers, model):
        s, _ = _read(api_client, auth_headers, model, supplier_contact_id="CON-ABC")
        assert s["supplier"]["contact_id"] == "con-abc"

    @pytest.mark.parametrize("code", ["410", "420", "200", "999"])
    def test_an_account_the_form_does_not_offer_is_dropped(self, api_client, auth_headers, model, code):
        s, _ = _read(api_client, auth_headers, model, account_code=code)
        assert s["account"]["code"] == "" and s["account"]["applied"] is False

    @pytest.mark.parametrize("amount", [0, -50, 99_999_999_999])
    def test_an_out_of_range_amount_is_dropped(self, api_client, auth_headers, model, amount):
        s, _ = _read(api_client, auth_headers, model, amount_value=amount)
        assert s["amount"]["value"] == "" and s["amount"]["applied"] is False

    def test_the_amount_takes_the_currency_s_places(self, api_client, auth_headers, model, test_entity):
        from bills.models import CurrencyInfo

        jpy = CurrencyInfo.objects.create(id="22222222-2222-2222-2222-222222222222", currency_code="JPY",
                                          currency_name="Yen", symbol="¥", decimal_places=0, is_active=True)
        Entity.objects.filter(id=test_entity.id).update(currency_id=jpy.id)
        s, _ = _read(api_client, auth_headers, model, amount_value=1200.4, currency="JPY")
        assert s["amount"]["value"] == "1200"
        assert (s["currency"], s["entity_currency"]) == ("JPY", "JPY")

    def test_a_due_date_before_the_invoice_date_is_dropped(self, api_client, auth_headers, model):
        s, _ = _read(api_client, auth_headers, model, invoice_date="2026-10-20", due_date="2026-10-01")
        assert s["invoice_date"]["value"] == "2026-10-20"
        assert s["due_date"]["value"] == "" and s["due_date"]["applied"] is False

    @pytest.mark.parametrize("value", ["20/10/2026", "2026-13-40", "soon"])
    def test_a_date_that_is_not_a_date_is_dropped(self, api_client, auth_headers, model, value):
        s, _ = _read(api_client, auth_headers, model, invoice_date=value)
        assert s["invoice_date"]["value"] == "" and s["invoice_date"]["applied"] is False

    def test_another_currency_is_handed_on_for_a_note_only(self, api_client, auth_headers, model):
        s, _ = _read(api_client, auth_headers, model, currency="usd")
        assert (s["currency"], s["entity_currency"]) == ("USD", "HKD")
        s, _ = _read(api_client, auth_headers, model, currency="dollars")
        assert s["currency"] == ""

    def test_the_description_is_capped_and_the_supplier_name_too(self, api_client, auth_headers, model):
        s, _ = _read(api_client, auth_headers, model, description_value="x" * 5000,
                     supplier_contact_id="", supplier_name="z" * 400)
        assert len(s["description"]["value"]) == 200 and len(s["supplier"]["detected_name"]) == 100

    def test_low_is_kept_for_the_log_but_not_prefilled_and_medium_is(self, api_client, auth_headers, model):
        s, _ = _read(api_client, auth_headers, model, amount_confidence=0.2, due_date_confidence=0.7)
        assert (s["amount"]["band"], s["amount"]["applied"]) == ("low", False)
        assert (s["due_date"]["band"], s["due_date"]["applied"]) == ("medium", True)

    def test_nothing_usable_says_so(self, api_client, auth_headers, model):
        low = {k: 0.1 for k in ("supplier_confidence", "account_confidence", "amount_confidence",
                                "description_confidence", "invoice_date_confidence", "due_date_confidence")}
        _, reason = _read(api_client, auth_headers, model, **low)
        assert reason == "no_usable_field"


def test_the_instruction_names_the_issuer_and_never_the_bill_number():
    instruction = invoice_reader.INSTRUCTION
    assert "the business that ISSUED the invoice, never the customer" in instruction
    assert "supplier_name IS AN EXCEPTION" in instruction
    assert "bill number" not in instruction.lower() and "invoice number" not in instruction.lower()
    assert "bill_number" not in invoice_reader.InvoiceSuggestion.model_fields
