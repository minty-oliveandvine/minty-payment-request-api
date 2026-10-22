"""This service's refusals, rendered in the one envelope every Minty API answers with
(``minty_shared.django.errors``, user 2026-10-22): ``{"error": "<sentence>"}`` - no longer
``detail`` - with ``fields`` (one sentence per input) when the schema refuses the input, 422.
A bill rule is 422, a permission 403, an unauthenticated call 401 ``Unauthorized``, anything
unforeseen 500 with the house sentence. ``@minty/shared``'s client reads ``error`` first.
"""

import logging

from minty_shared.django.errors import (
    HOUSE_FALLBACK,
    register_error_handlers,
    validation_message,
)

__all__ = [
    "HOUSE_FALLBACK",
    "BillValidationError",
    "PermissionDeniedError",
    "register_exception_handlers",
    "validation_message",
]

logger = logging.getLogger("minty-api")


class BillValidationError(Exception):
    pass


class PermissionDeniedError(Exception):
    pass


def register_exception_handlers(api):
    register_error_handlers(
        api,
        logger=logger,
        refusals={BillValidationError: 422, PermissionDeniedError: 403},
    )
