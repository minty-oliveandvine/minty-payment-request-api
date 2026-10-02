# Error copy

What this API sends when something fails, and why. The copy standard is shared
across Minty, minty-payment-request-api, minty-payment-request-web and the onboarding apps; the canonical
write-up lives in the Minty repo as `docs/features/ERROR_MESSAGE_LEAKS.md`.

Everything here reaches a payer's screen: minty-payment-request-web renders `detail`
directly when it reads as a sentence.

## The standard

> `I couldn't <do the specific thing>. <Short next step>?`

Under two sentences; first person; name the specific thing; no codes or stack
text in the visible string; sentence case. Validation and limits skip the
apology and state the rule instead -- `Files need to be under 10MB.`

House fallback: `Something went wrong on my end. Mind trying again?`

## The handlers

`core/exceptions.py`, registered from `config/urls.py`. This is **django-ninja,
not DRF** -- do not reach for DRF idioms.

| Exception | Status | Body |
|---|---|---|
| `SchemaValidationError` (ninja's `ValidationError`) | 422 | `validation_message(exc.errors)` |
| `BillValidationError` | 422 | `str(exc)` -- authored at the raise site |
| `PermissionDeniedError` | 403 | `str(exc)` -- authored at the raise site |
| `Exception` | 500 | the house fallback; detail is logged |

### Why the schema handler exists

Registering a handler for `Exception` does **not** shadow ninja's more specific
built-in handlers. Without a `ValidationError` handler of our own, ninja's
default answered with `detail: [{type, loc, msg}, ...]` -- a list, not a string
-- and the browser rendered it as serialised JSON. A payer submitting a bill
with a missing field saw:

    {"type":"missing","loc":["body","email"],"msg":"Field required"}

`validation_message` flattens that into one sentence naming the fields. Its
behaviour is pinned by `bills/tests/test_validation_messages.py`.

## When you raise

`BillValidationError` and `PermissionDeniedError` messages are shown to the user
verbatim. Write them as copy, not as diagnostics:

```python
# No: developer register, and it names our internals
raise BillValidationError("Attachment not found on this bill")
# Yes:
raise BillValidationError("I couldn't find that attachment on this bill.")
```

**Never interpolate an exception into the message.** `str(exc)` on a
`requests.RequestException` carries the full URL and connection detail, and this
is a user-facing body:

```python
# No:
raise BillValidationError(f"Xero API call failed: {exc}")
# Yes:
logger.warning("Xero API call failed: %s", exc)
raise BillValidationError("I couldn't reach Xero just now. Mind trying again?")
```

Some tests assert on this copy (`test_xero_publish.py`,
`test_entity_bill_contacts_create.py`). That is deliberate -- it stops a message
regressing silently. Update the assertion along with the copy.
