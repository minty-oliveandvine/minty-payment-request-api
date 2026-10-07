"""/healthz: the host's liveness probe - public, and never touches the database.

No test here takes the ``db`` fixture, so any query /healthz made would raise pytest-django's
"Database access not allowed".
"""

from __future__ import annotations


def test_healthz_answers_without_a_token_or_the_database(api_client):
    res = api_client.get("/healthz")
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "service": "minty-payment-request-api"}


def test_the_api_stays_token_gated(api_client):
    assert api_client.get("/api/v1/bills/").status_code == 401
