"""The one error envelope every Minty API answers with (minty_shared.django.errors, user
2026-10-22): ``{"error": <sentence>, "fields"?: {...}}`` - never ``detail``; 422 for input the
schema refuses, naming each field; 401 ``Unauthorized``; 500 the house sentence."""

from __future__ import annotations

import json

import pytest
from django.test import RequestFactory

HOUSE = "Something went wrong on my end. Mind trying again?"


def _handled(exc):
    from config.urls import api

    request = RequestFactory().get("/api/v1/bills/")
    response = api._lookup_exception_handler(exc)(request, exc)
    return response.status_code, json.loads(response.content)


def test_no_token_is_401_unauthorized(api_client):
    resp = api_client.get("/api/v1/bills/")
    assert (resp.status_code, resp.json()) == (401, {"error": "Unauthorized"})


def test_an_unhandled_failure_is_the_house_sentence():
    assert _handled(RuntimeError("database fell over: secret-detail")) == (500, {"error": HOUSE})


def test_a_bill_rule_is_422_with_the_error_key():
    from core.exceptions import BillValidationError

    assert _handled(BillValidationError("Invoice number already exists.")) == (
        422,
        {"error": "Invoice number already exists."},
    )


def test_permission_denied_is_403_with_the_error_key():
    from core.exceptions import PermissionDeniedError

    assert _handled(PermissionDeniedError("Only accountants can.")) == (
        403,
        {"error": "Only accountants can."},
    )


def test_schema_validation_is_422_naming_each_field():
    from ninja.errors import ValidationError

    exc = ValidationError(
        [
            {"type": "missing", "loc": ("body", "payload", "amount"), "msg": "x"},
            {"type": "string_type", "loc": ("body", "payload", "payee_name"), "msg": "x"},
        ]
    )
    status, body = _handled(exc)
    assert status == 422
    assert body["error"] == "Mind checking amount and payee name? That didn't look quite right."
    assert body["fields"] == {
        "amount": "The amount is still empty. Add the amount to continue.",
        "payee_name": "The payee name doesn't look right. Check it and try again.",
    }


@pytest.mark.django_db
def test_a_hand_written_refusal_uses_the_error_key(api_client, auth_headers, draft_bill, test_user_entity):
    resp = api_client.post(
        f"/api/v1/bills/{draft_bill.id}/return/",
        {"status": "nonsense"},
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code in (403, 404, 422)
    body = resp.json()
    assert isinstance(body.get("error"), str) and body["error"]
    assert "detail" not in body
