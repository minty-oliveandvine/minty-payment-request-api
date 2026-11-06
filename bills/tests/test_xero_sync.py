"""accounts-api's one Xero sync, asked for by the bill list and the Bill CoA settings
(``bills/services/xero_sync.py``; Phase 4 of the Flask retirement). It replaces the calls to Flask's
``/api/entities/<id>/billing/sync-*`` routes, which checked no token at all.
"""

from __future__ import annotations

import jwt
import pytest
import requests
from django.conf import settings
from django.core.cache import cache

from bills.services import xero_sync

URL = "http://accounts.test/api/internal/xero/sync"


class _Resp:
    def __init__(self, status, body=None):
        self.status_code, self._body, self.text = status, body, str(body)

    def json(self):
        if self._body is None:
            raise ValueError("not JSON")
        return self._body


@pytest.fixture
def accounts_api(monkeypatch, settings):
    settings.XERO_SYNC_URL = URL
    sent = {"calls": [], "answer": _Resp(200, {"accounts": {"added": 1}, "contacts": {"added": 0, "renamed": 0}})}

    def post(url, headers=None, timeout=None, **_):
        sent["calls"].append({"url": url, "headers": headers, "timeout": timeout})
        if isinstance(sent["answer"], Exception):
            raise sent["answer"]
        return sent["answer"]

    monkeypatch.setattr(xero_sync.requests, "post", post)
    return sent


@pytest.fixture
def threads(monkeypatch):
    started = []

    class Recorder:
        def __init__(self, target=None, args=(), daemon=None):
            started.append(args)

        def start(self):
            pass

    monkeypatch.setattr(xero_sync.threading, "Thread", Recorder)
    cache.clear()
    return started


def test_the_sync_is_asked_with_a_scoped_assertion_naming_the_company(accounts_api):
    entity = "9df620d9-a0f3-5f42-a200-04f17c73b009"
    assert xero_sync.run_sync(entity) == {"accounts": {"added": 1}, "contacts": {"added": 0, "renamed": 0}}

    call, = accounts_api["calls"]
    assert call["url"] == URL and call["timeout"] == xero_sync.TIMEOUT
    claims = jwt.decode(call["headers"]["Authorization"].removeprefix("Bearer "), settings.SECRET_KEY,
                        algorithms=["HS256"])
    assert (claims["scope"], claims["entity_id"]) == ("xero-sync", entity)
    assert claims["exp"] - claims["iat"] == 60


@pytest.mark.parametrize("answer", [_Resp(409, {"error": "x", "reconnect": True}), _Resp(500, {}), _Resp(200),
                                    requests.ConnectionError("down")])
def test_a_sync_that_could_not_run_is_logged_never_raised(accounts_api, answer):
    accounts_api["answer"] = answer
    assert xero_sync.run_sync("9df620d9-a0f3-5f42-a200-04f17c73b009") is None


def test_the_sync_runs_in_the_background_once_per_window(threads):
    assert xero_sync.request_sync("e-1") is True
    assert xero_sync.request_sync("e-1") is False  # debounced
    assert xero_sync.request_sync("e-2") is True  # another company
    assert xero_sync.request_sync("e-1", force=True) is True  # a manual refresh
    assert threads == [("e-1",), ("e-2",), ("e-1",)]


@pytest.mark.django_db
def test_the_bill_list_and_the_bill_settings_start_it(api_client, threads, auth_headers, test_user_entity, test_entity):
    assert api_client.get("/api/v1/bills/", **auth_headers).status_code == 200
    assert api_client.get("/api/v1/entity-bill-accounts/?include_inactive=true", **auth_headers).status_code == 200
    assert api_client.get("/api/v1/entity-bill-accounts/", **auth_headers).status_code == 200  # no sync
    assert api_client.get("/api/v1/entity-bill-accounts/?force_chart_sync=true", **auth_headers).status_code == 200
    # The list starts it; the settings within the window are debounced; a forced refresh is not.
    assert threads == [(str(test_entity.id),), (str(test_entity.id),)]
