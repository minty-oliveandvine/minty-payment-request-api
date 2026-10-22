"""The one log line (minty_shared.logctx) as this service writes it: a trace per request, taken
from a well-formed ``X-Request-ID`` and echoed back; the user and entity bound only once auth has
authorised them; the route name as the fallback step; and the trace sent on every call to Flask."""

from __future__ import annotations

import logging
import re
from types import SimpleNamespace

import pytest
from minty_shared import logctx

HEX8 = re.compile(r"[0-9a-f]{8}")


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.addFilter(logctx.ContextFilter())
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)


@pytest.fixture
def lines():
    capture = _Capture()
    loggers = [logging.getLogger("minty-api"), logging.getLogger("minty-api.http")]
    for lg in loggers:
        lg.addHandler(capture)
    yield capture.records
    for lg in loggers:
        lg.removeHandler(capture)


def _access(records):
    (record,) = [r for r in records if r.name == "minty-api.http"]
    return record


def test_well_formed_trace_is_kept_and_echoed(api_client, lines):
    res = api_client.get("/healthz", HTTP_X_REQUEST_ID="deadbeef")
    assert res["X-Request-ID"] == "deadbeef"
    record = _access(lines)
    assert record.trace == "deadbeef"
    assert re.fullmatch(r"GET /healthz 200 \d+ms", record.getMessage())
    assert (record.endpoint, record.method) == ("/healthz", "GET")


@pytest.mark.parametrize("incoming", [None, "short", "DEADBEEF", "deadbeef\nINFO forged"])
def test_a_missing_or_malformed_trace_is_replaced(api_client, lines, incoming):
    headers = {"HTTP_X_REQUEST_ID": incoming} if incoming is not None else {}
    res = api_client.get("/healthz", **headers)
    assert HEX8.fullmatch(res["X-Request-ID"])
    assert _access(lines).trace == res["X-Request-ID"]


@pytest.mark.django_db
def test_member_request_carries_user_entity_and_route(
    api_client, lines, auth_headers, test_user, test_entity, test_user_entity
):
    res = api_client.get("/api/v1/bills/", **auth_headers)
    assert res.status_code == 200
    record = _access(lines)
    assert (record.user, record.entity) == (str(test_user.id), str(test_entity.id))
    # One ninja URL pattern serves GET and POST on /bills/ under the POST's URL name; the step
    # is the operation this method runs.
    assert record.step == "list_bills"


@pytest.mark.django_db
def test_a_refused_request_names_nobody(api_client, lines, auth_headers, test_user, test_entity):
    # No user_entity row: the entity holds no role of theirs.
    res = api_client.get("/api/v1/bills/", **auth_headers)
    assert res.status_code == 401
    assert (_access(lines).user, _access(lines).entity) == ("-", "-")


@pytest.mark.django_db
def test_nothing_outlives_the_request(api_client, auth_headers, test_user_entity):
    api_client.get("/api/v1/bills/", **auth_headers)
    assert logctx.current() == {"trace": "-", "user": "-", "entity": "-", "step": "-"}


@pytest.mark.django_db
def test_calls_to_flask_carry_the_trace(monkeypatch, settings, test_entity):
    import requests

    from bills.services import flask_billing_sync

    settings.PETTY_CASH_URL = "http://flask.test"
    sent = []

    def fake_post(url, headers=None, **kwargs):
        sent.append(dict(headers or {}))
        return SimpleNamespace(status_code=200, text="", json=lambda: {})

    monkeypatch.setattr(requests, "post", fake_post)
    request = SimpleNamespace(headers={"Authorization": "Bearer t"})
    logctx.start_trace("a1b2c3d4")
    try:
        flask_billing_sync.trigger_flask_bill_chart_sync(request, str(test_entity.id), force=True)
        flask_billing_sync.trigger_flask_contact_sync(request, str(test_entity.id), force=True)
    finally:
        logctx.reset()
    assert sent and all(h.get("X-Request-ID") == "a1b2c3d4" for h in sent)


def test_the_background_bankslip_thread_keeps_the_trace(monkeypatch):
    import threading

    from bills.services import xero_publish_service as svc

    seen, done = {}, threading.Event()

    def fake_upload(*args, **kwargs):
        seen.update(logctx.current())
        done.set()

    monkeypatch.setattr(svc, "upload_bankslip_to_xero", fake_upload)
    monkeypatch.setattr(svc.connection, "close", lambda: None)
    logctx.start_trace("a1b2c3d4")
    logctx.bind(user="u1", entity="e1")
    try:
        svc.upload_bankslip_background("b", "p", "e1", "u1", "t")
    finally:
        logctx.reset()
    assert done.wait(5)
    assert (seen["trace"], seen["user"], seen["entity"]) == ("a1b2c3d4", "u1", "e1")



@pytest.mark.django_db
def test_a_sign_in_logs_nothing_derived_from_the_key(
    api_client, lines, auth_headers, test_user_entity, settings
):
    import hashlib

    res = api_client.get("/api/v1/bills/", **auth_headers)
    assert res.status_code == 200
    key_hash = hashlib.sha256(settings.SECRET_KEY.encode()).hexdigest()[:16]
    text = "\n".join(r.getMessage() for r in lines)
    assert key_hash not in text and "key_len" not in text and "key_hash" not in text
