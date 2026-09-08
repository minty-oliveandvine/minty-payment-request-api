import logging

from ninja.errors import ValidationError as SchemaValidationError

logger = logging.getLogger("minty-api")

# Shown when we have nothing specific and useful to say. Keep it cause-neutral:
# it fires for unknown reasons, so it must not assert one.
HOUSE_FALLBACK = "Something went wrong on my end. Mind trying again?"


class BillValidationError(Exception):
    pass


class PermissionDeniedError(Exception):
    pass


# django-ninja puts the param source in the first segment of ``loc`` and, for a
# body model, the schema argument name in the second. Neither means anything to
# the person reading the message.
_LOC_NOISE = frozenset({
    "body", "query", "path", "form", "header", "cookie", "file",
    "payload", "data", "request",
})


def _field_names(errors):
    """Readable field names from ninja's ``loc`` tuples, in order, deduplicated."""
    names = []
    for err in errors or []:
        if not isinstance(err, dict):
            continue
        parts = [str(p) for p in (err.get("loc") or ()) if not isinstance(p, bool)]
        parts = [p for p in parts if not p.isdigit()]
        # Strip the source/wrapper segments. If that leaves nothing, the loc
        # named no real field, so say nothing rather than naming 'body'.
        meaningful = [p for p in parts if p not in _LOC_NOISE]
        if not meaningful:
            continue
        name = meaningful[-1].replace("_", " ").strip()
        if name and name not in names:
            names.append(name)
    return names


def _join(names):
    if len(names) == 1:
        return names[0]
    return "{} and {}".format(", ".join(names[:-1]), names[-1])


def validation_message(errors):
    """Turn ninja's ``[{type, loc, msg}, ...]`` into a single sentence.

    ninja's own handler puts that list straight into ``detail``. The browser
    clients then render it as serialised JSON -- a payer submitting a bill with
    a missing field saw ``{"type":"missing","loc":["body","email"],...}`` in a
    toast. Whatever reaches a user has to be a sentence, so build one here
    rather than unpicking the structure again in every frontend.
    """
    names = _field_names(errors)
    if not names:
        return "Some of those details didn't look right. Mind checking them?"
    joined = _join(names)
    if all(str(e.get("type", "")).startswith("missing")
           for e in errors if isinstance(e, dict)):
        return "I still need {} to continue.".format(joined)
    return "Mind checking {}? That didn't look quite right.".format(joined)


def register_exception_handlers(api):
    @api.exception_handler(SchemaValidationError)
    def on_schema_validation(request, exc):
        # The structured errors stay in the log; the body carries the sentence.
        logger.warning("Schema validation error: %s", exc.errors)
        return api.create_response(
            request, {"detail": validation_message(exc.errors)}, status=422
        )

    @api.exception_handler(BillValidationError)
    def on_validation(request, exc):
        logger.warning("Validation error: %s", str(exc))
        return api.create_response(request, {"detail": str(exc)}, status=422)

    @api.exception_handler(PermissionDeniedError)
    def on_permission_denied(request, exc):
        logger.warning("Permission denied: %s", str(exc))
        return api.create_response(request, {"detail": str(exc)}, status=403)

    @api.exception_handler(Exception)
    def on_unhandled(request, exc):
        logger.exception("Unhandled error: %s", str(exc))
        return api.create_response(request, {"detail": HOUSE_FALLBACK}, status=500)
