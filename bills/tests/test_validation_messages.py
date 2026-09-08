"""Schema-validation errors must reach the browser as a sentence.

django-ninja's default handler puts its structured error list straight into
``detail``. The browser clients render that as serialised JSON, so a payer
submitting a bill with a missing field saw
``{"type":"missing","loc":["body","email"],...}`` in a toast. These pin the
flattening that replaced it.
"""

import pytest

from core.exceptions import validation_message


def test_a_single_missing_field_is_named():
    assert validation_message(
        [{"type": "missing", "loc": ["body", "payload", "email"], "msg": "Field required"}]
    ) == "I still need email to continue."


def test_several_missing_fields_read_as_a_list():
    message = validation_message([
        {"type": "missing", "loc": ["body", "payload", "email"], "msg": "Field required"},
        {"type": "missing", "loc": ["body", "payload", "bill_reference"], "msg": "Field required"},
    ])
    assert message == "I still need email and bill reference to continue."


def test_underscores_become_spaces():
    message = validation_message(
        [{"type": "missing", "loc": ["body", "data", "account_code"], "msg": "x"}]
    )
    assert "account code" in message
    assert "account_code" not in message


def test_a_bad_value_asks_the_user_to_check_it():
    message = validation_message([{
        "type": "int_parsing",
        "loc": ["body", "payload", "amount"],
        "msg": "Input should be a valid integer",
    }])
    assert message == "Mind checking amount? That didn't look quite right."


def test_list_indexes_are_not_shown_as_field_names():
    message = validation_message([{
        "type": "missing",
        "loc": ["body", "payload", "lines", 0, "account_code"],
        "msg": "x",
    }])
    assert message == "I still need account code to continue."


def test_wrapper_segments_are_never_named():
    """``body``/``payload`` are ninja's plumbing, not something a user recognises."""
    message = validation_message([{"type": "missing", "loc": ["body"], "msg": "x"}])
    assert "body" not in message
    assert message == "Some of those details didn't look right. Mind checking them?"


@pytest.mark.parametrize("errors", [[], None, ["not a dict"], [{}]])
def test_unrecognised_shapes_fall_back_rather_than_leak(errors):
    assert validation_message(errors) == (
        "Some of those details didn't look right. Mind checking them?"
    )


def test_no_serialised_structure_survives():
    """The regression this whole handler exists to prevent."""
    message = validation_message([
        {"type": "missing", "loc": ["body", "payload", "email"], "msg": "Field required"},
    ])
    for fragment in ("{", "}", "[", "]", "loc", "msg", "type"):
        assert fragment not in message
