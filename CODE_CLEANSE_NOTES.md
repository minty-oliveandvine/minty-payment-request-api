# Code Cleanse Notes

Running log so this work can be resumed in a fresh session.
Branch: `code-cleanse` (branched from `Minty-BillingBackend`). **Nothing is committed by
Claude — the user commits.**

## How to verify (do this after EVERY step)

```bash
.cleanse/verify.sh "what I just did"
```

Exit 0 = no regressions. Exit 1 = a test that passed at baseline now fails → revert the step.

## Environment

No Python environment on this machine had Django installed, so the suite could not be run
as-is. Created `.cleanse-venv/` (python 3.12.2, `pip install -r requirements.txt`) purely to
run tests. It is in `.git/info/exclude`, so it is untracked and local-only — delete it when done.

`ruff`, `isort`, and `black` are on PATH via `/opt/anaconda3/bin`. `ruff` is in
requirements.txt but **there is no ruff/isort/black config file anywhere in the repo** — no
`pyproject.toml`, `setup.cfg`, `.ruff.toml`, `.isort.cfg`, or pre-commit config. So there is
no project-defined line length or import-ordering profile. Formatting choices in step 3 of
each subfolder must therefore be made conservatively and flagged, not assumed.

## Baseline (recorded BEFORE any changes)

**14 failed, 398 passed** — recorded in `.cleanse/baseline_failures.txt`.

> Note: the user reported 79 failures in their own environment. This environment sees 14.
> The difference is environmental (dependency versions in a fresh venv vs. the user's env),
> not caused by any change here. The rule still holds against *this* recorded baseline:
> no test passing at 398 may start failing. If you resume in the user's env, re-record the
> baseline first rather than reusing this file.

The 14 pre-existing failures cluster into 4 groups — **not to be fixed, that is separate work**:

- `test_account_restoration.py` (5) — sync path expects live HTTP, blocked by the conftest guard
- `test_attachment.py::TestAttachmentUpload` (3) — S3 upload path
- `test_payment_attachment_upload.py` (2) — bank-slip upload path
- `test_entity_bill_accounts_list.py` (3) + `test_depreciatn_account.py` (1) — account-type filtering,
  fail with `NinjaResponseSchema` validation errors

## Survey

| Folder | .py files | LOC |
|---|---|---|
| `bills` | 70 | 17,542 |
| `core` | 14 | 976 |
| `config` | 5 | 255 |
| `shared_models` | 3 | 132 |
| `docs` | 1 | 349 |
| root (`manage.py`) | 1 | 20 |

`bills` is 93% of the code. Within it, tests are ~7,700 LOC and migrations ~1,700 LOC.
Largest single files: `bills/tests/test_xero_publish.py` (1367),
`bills/services/xero_publish_service.py` (1180), `bills/api_config.py` (718), `bills/api.py` (668).

Planned order, smallest first: **shared_models → config → core → docs → bills**.
(`scripts/` and `docker/` contain no Python.)

## Patch-target survey — READ BEFORE CONSOLIDATING ANYTHING

Full detail in `.cleanse/patch_targets.md`. Summary of what is load-bearing:

- 1 `monkeypatch.setattr`, in `bills/tests/conftest.py:27`, blocking real HTTP via
  `bills.services.xero_token_service.requests.post`.
- 268 `patch(...)` call sites resolving to ~17 distinct targets, mostly reached through
  module- and class-level path constants (`_UPLOAD_ATTACHMENTS_PATH`, `self._TOKEN_PATH`, …).
- **`bills/services/xero_publish_service.py` is the most constrained file in the repo.** Ten of
  its leading-underscore helpers are patched by name. They *look* private and dead; they are not.
- `import requests` (module form) is load-bearing in `xero_token_service`, `contact_service`,
  `xero_publish_service`. `from requests import post` would break the patches, including the
  conftest HTTP guard.
- `bills/api.py`'s `from ... import publish_bill_to_xero` / `resolve_xero_access_token_for_entity`
  style is load-bearing — those names are patched as `bills.api.*`, a different object from the
  same names in the service module.
- `_get_s3_client` is defined once (`attachment_service.py:99`) and imported into
  `xero_publish_service.py:26`, then patched under *both* module paths. Not duplication —
  do not "fix" it.

## Work log

- [x] Survey, baseline, patch-target inventory, verify harness. No source files changed yet.
- [x] `shared_models` (3 files, 132 LOC) — **1 cosmetic line change, nothing else.**
  - *dead code*: none. `ruff --select F401,F811,F841,F821` clean; default ruleset clean.
    All 5 models are referenced outside the app (User 91, Entity 142, UserEntity 52,
    XeroContactSync 59, AccountInfo 2). Checked `AccountInfo`'s 2 refs by hand — both are
    real (`bills/api_config.py:377,380`, a deliberate function-local import).
  - *duplication*: none worth extracting. The 5 classes are Django field declarations, not
    logic. They share a shape (`id`/`entity_id` CharField(36) + `class Meta: managed=False`),
    but a shared abstract base would change `Meta` inheritance and model registration for
    `managed=False` mirror tables of an external Flask-owned schema. High risk, zero
    behavioural gain, and it would touch `apps.py`'s `_meta.managed = True` test hook.
    Deliberately not done.
  - *format*: `isort` reported no changes. `black` changed exactly one thing — exploding
    `UserEntity.user`'s args onto separate lines (`models.py:60-63`). Applied: file is 111
    LOC (well under the 1000-LOC guard) and this commit contains no logic changes, so
    there is no real diff to bury.
  - verify: 14 failed / 398 passed — **no regressions**.
- [x] `config` (5 files, 255 LOC) — **1 real dedup + formatting.**
  - *dead code*: none. Checked each settings constant for external references.
    `CACHES` has 0 by-name references but is live — consumed by `django.core.cache`, used
    for the debounce in `bills/services/flask_billing_sync.py:50,136,198`. Not dead.
  - *duplication*: `os.environ.get("FLASK_APP_URL", "http://localhost:5001")` appeared
    twice (settings.py:119 and :126). Moved the `FLASK_APP_URL` definition above its
    consumer and referenced the name in the f-string. **Proved identical** by loading real
    Django settings before/after with the env var both unset and set to a custom value —
    `XERO_TOKEN_SERVICE_URL` and `FLASK_APP_URL` byte-identical in both cases.
  - *format*: `isort --profile black` + `black`. `config/urls.py` got pure line-wrapping of
    4 long `api.add_router(...)` calls; no route path or tag string altered. Verified 17
    routers still register.
  - verify: 14 failed / 398 passed — **no regressions**.
- [x] `core` (14 files, 976 LOC) — **dead code removed + 2 real dedups.**
  - *dead code*:
    - Deleted `core/pagination.py` entirely (22 LOC). `paginate_queryset` and
      `PaginatedResponse` had **zero** references repo-wide — checked .py, .md, .sh, .sql,
      .json, .yml, and for `importlib`/`getattr` dynamic access. Both date to the initial
      commit (`7652eee`), i.e. abandoned scaffolding, not new API.
      **Note:** `bills/api.py:332-336` hand-rolls the same clamp
      (`min(max(1, page_size), 100)`). It is NOT a missed reuse — `paginate_queryset`
      returns a `{count, page, page_size, results}` dict and calls `queryset.count()`
      (an extra query), while `bills/api.py` returns a bare list. Adopting the helper
      would change the API response shape. Deleted the helper rather than adopting it.
    - Deleted `is_elevated()` from `permissions.py` — zero references, also from `7652eee`.
    - Removed unused `from django.http import JsonResponse` in `exceptions.py` (the module
      uses `api.create_response`) and unused `from django.db import connection` in
      `views.py` (left over after the dedup below).
    - Fixed 2 × `F541` stray f-prefixes in `generate_token.py`; proved both render
      identically (no placeholders were present).
  - *duplication*:
    - **`_get_entity_role` was byte-identical in two places** — `core/views.py:15` and
      `core/auth.py:141` (`BearerAuth._get_entity_role`). Verified with an AST diff: same
      SQL, same params, same return; only the docstring and `@staticmethod` differed.
      Canonical implementation now lives at `core/auth.py::get_entity_role` (module level).
      `views.py` imports it as `_get_entity_role`; `BearerAuth` keeps
      `_get_entity_role = staticmethod(get_entity_role)` so `self._get_entity_role(...)`
      and any subclass override still work. Confirmed all three access paths resolve to
      the *same function object*, including via an instance. Not patched by any test
      (checked before merging, per the patch-target rule).
    - **6 permission checkers shared one gate.** `check_mark_paid`, `check_publish_xero`,
      `check_return_bill`, `check_edit_bill_settings` → `_require_elevated(role, message)`;
      `check_edit_bill` / `check_delete_bill` → `_check_bill_action(...)`.
      **Each denial message differs and is user-facing (403 body), so the message is a
      parameter, not derived** — including `check_return_bill`'s trailing period, which the
      other three lack. Proof: snapshotted all **294** combinations of
      {13 roles × 8 statuses × 9 functions} recording outcome + exact message before and
      after; `diff` is empty. Re-ran the snapshot again after `black` — still identical.
      (`.cleanse/perm_snapshot.py`, `perm_before.json`, `perm_after_fmt.json`.)
  - *format*: `isort --profile black` + `black` on all of `core` (largest file 235 LOC,
    far under the 1000-LOC guard). Pure wrapping/whitespace, incl. stripping trailing
    whitespace at `views.py:43,49`. Verified string constants unchanged per-file by AST.
    Confirmed all 6 exception handlers still register on the API after editing
    `exceptions.py`.
  - verify: 14 failed / 398 passed at every one of the 4 checkpoints — **no regressions**.
- [x] `docs` (1 file, 349 LOC) — **surveyed, zero changes by explicit decision.**
  - `ruff` passes clean; `isort --profile black` is already a no-op.
  - `docs/generate_db_design_pdf.py` is a standalone one-off generator for
    `docs/db_design_ko.pdf`. Nothing imports it, `build_pdf()` is only called from its own
    `__main__`, and pytest does not collect it (`python_files = test_*.py`).
  - It **cannot run in this repo**: `fpdf` is not in `requirements.txt`, and the fonts are
    hardcoded to `C:/Windows/Fonts/malgun.ttf` (Windows-only; this is a macOS checkout).
  - Its content is **stale** vs the live `Bill` model: it documents `attachment_id`,
    `paid_date`, `xero_invoice_id` (no longer fields) and omits `currency_code`,
    `reference`, `xero_account_code`, and the whole `payments` relation.
  - **User decision: leave it entirely alone.** Not deleted, not reformatted. It is a
    documentation artifact, not runtime code, and the call about a Korean-language design
    doc belongs to the team, not to a code cleanse. Recorded here so the staleness is
    known rather than silently inherited.
  - Also deliberately skipped `black` on it: black wraps the Korean lines by counting CJK
    characters as width-1 when they render double-width, so the "fix" reads worse than the
    original and would bury nothing useful.
- [ ] `bills` — split into stages, smallest/lowest-risk first:
  - [x] **stage 1: `models.py` + `schemas.py`** (1,141 LOC) — **formatting only.**
    - *dead code*: none. All 47 schemas are used. **Near-miss worth recording:** a naive
      "grep outside the defining file" said 6 schemas were unused — `LineItemIn`,
      `LineItemOut`, `PaymentListOut`, `XeroBillSyncLineOut`, `XeroBillSyncPayloadOut`,
      `XeroBillResponseLineOut`. All 6 are live **nested types** referenced only *within*
      `schemas.py` (`line_items: list[LineItemOut]`, `payload: XeroBillSyncPayloadOut | None`,
      etc.). Deleting on that grep would have broken the API response shapes.
    - *duplication*: exactly one structurally identical pair found by AST comparison —
      `BillAttachmentOut` and `PaymentAttachmentOut` (same 6 fields, same types, same
      defaults). **Deliberately NOT merged.** Both class names are published as distinct
      components in the generated OpenAPI schema (verified: `api.get_openapi_schema()`
      lists `AttachmentOut`, `BillAttachmentOut`, `PaymentAttachmentOut`). Aliasing one to
      the other renames a public API type and would break any frontend generating a typed
      client. They are also semantically distinct response types that can diverge.
      Field-shape coincidence is not duplication.
    - `models.py` is Django field declarations only — same reasoning as `shared_models`,
      nothing to extract.
    - *format*: `isort --profile black` + `black`. Both files are ~570 LOC, under the
      1000-LOC guard, and this stage has no logic changes. Proved safe by snapshotting all
      **582** live field definitions (Django model fields: type/max_length/default/null/
      db_index; Ninja schema fields: annotation + default) before and after — identical
      once `repr()` memory addresses are normalized. OpenAPI still emits 57 components
      across 43 paths.
    - verify: 14 failed / 398 passed — **no regressions**.
  - [x] **stage 2: `bills/services/`** (3,366 LOC) — **2 extractions + format.**
    - *dead code*: only one candidate, `trigger_flask_bill_chart_sync`
      (`flask_billing_sync.py:23`, 90 LOC, zero references anywhere in the repo).
      **NOT deleted — awaiting user decision.** It is fully implemented, POSTs to a
      *distinct* Flask endpoint (`/billing/sync-chart-accounts`) that no other function
      calls, and its two siblings in the same module *are* live. That reads like a wiring
      bug (wrong function called / endpoint retired) rather than abandoned scaffolding, so
      per the rules it is a stop-and-ask, not a silent delete.
      **User decision: leave it in place, flagged here.** Open question for whoever owns
      the Flask side: does `POST /api/entities/{id}/billing/sync-chart-accounts` still
      exist in Module 1, and should something be calling it? If the endpoint is retired,
      this function and `_BILL_CHART_SYNC_TTL` can both go. Do not delete it as part of a
      formatting pass.
    - *duplication — 2 real extractions in `attachment_service.py`:*
      - `_upload_file_to_s3(file, s3_key_prefix)` — the validate → size-check → downsize →
        S3-put block shared by `upload_attachment` and `upload_payment_attachment`.
        **4 behavioural differences preserved, not flattened:** (1) the payment path
        validates `attachment_role` and the bill path does not; (2) different S3 key
        prefixes (parameter); (3) the bill path writes an audit row and the payment path
        does not; (4) different log messages. The helper does **no DB writes** — it returns
        kwargs so `Attachment.objects.create` stays inside each caller's `transaction.atomic()`
        block, exactly as before. Verified by AST that the atomic block still contains both
        creates and that S3 I/O is still outside it (a first attempt moved the S3 upload
        inside the transaction — caught and reverted before verification).
      - `_delete_mapping_and_orphan(mapping, attachment, remaining_relation)` — the S3-delete
        + `atomic(mapping.delete, orphan cleanup)` block. `remaining_relation` is passed as a
        string so each path checks its *own* reverse accessor and an Attachment still
        referenced by the other mapping kind is never deleted. Return values, error
        messages and the audit asymmetry all preserved.
      - Proof: 26 upload cases (roles incl. an invalid one, bad MIME, oversize, extension
        fallbacks) and 18 delete cases (missing row, S3 failure, orphan vs shared
        attachment, empty `xero_attachment_id`) snapshotted before/after — capturing return
        values, S3 keys, call ordering, audit calls and every log line. All identical, and
        re-verified again after `black`. See `.cleanse/attach_snapshot.py`,
        `.cleanse/delete_snapshot.py`.
    - *deliberately NOT merged:* `trigger_chart_sync_if_changed` vs
      `trigger_flask_contact_sync` share a skeleton but differ in return type (`bool` vs
      `None`), **log level** (`error` vs `warning`), every log message, the JSON-branch
      logic, and whether JSON decode errors are swallowed. Merging needs ~8 parameters and
      obscures more than it saves. Similar shape, different behaviour.
    - *format*: `isort --profile black` + `black` on 9 of 11 service files. **
      `xero_publish_service.py` (1,180 LOC) deliberately excluded** — over the ~1000-LOC
      guard, and this stage contains logic changes. Left as a separate optional commit.
      Verified string constants byte-identical in all 8 untouched-logic files; the only
      string delta is in `attachment_service.py` and is **additions only** (new dict keys,
      relation names, docstrings).
    - verify: 14 failed / 398 passed at all 3 checkpoints — **no regressions**.
  - [x] **stage 3: `bills/` API layer** (7 files, 2,153 LOC) — **dead imports + 2 dedups + format.**
    - *dead code*: removed 2 unused schema imports — `AttachmentOut` from `api.py`
      (only referenced inside `schemas.py`) and `MessageOut` from `api_profile.py` (unused
      there; still used by other api files, so only the import line went).
    - *duplication — 2 extractions:*
      - **`_get_bill_or_404` was byte-identical** in `api.py:152` and `api_payments.py:39`
        (AST diff empty). Kept the copy in `api.py` as canonical and imported it into
        `api_payments.py` (`from bills.api import _get_bill_or_404`). Verified: not patched
        by any test, no import cycle (`bills.api` does not import `bills.api_payments`, and
        `config.urls` still loads with 17 routers), and both names resolve to the *same
        function object*. Its `"Bill not found"` string now lives once, in `api.py`.
      - **`_apply_partial_update(obj, update_data)`** in `api_config.py` — the
        `for field, value in ...: if value is not None: setattr` loop appeared **identically
        5 times** across the entity-function / function-map / account / currency /
        bill-currency PUT endpoints. Extracted the loop only. Proved the helper's semantics
        match the originals across 7 dict shapes including the falsy-but-not-None cases
        (`0`, `False`, `""`), which the `is not None` guard must still apply. The helper does
        **not** call `.save()` — each endpoint keeps its own save + logging + side effects
        (e.g. the accounts endpoint's `account_info.status` mirror is untouched).
    - *deliberately NOT merged:* the 6 CRUD groups in `api_config.py` (entity functions,
      function maps, accounts, currencies, bill currencies, contacts) share a *shape* but
      differ in model, scoping (global vs entity-scoped), every `Http404` message, every log
      message, and create-body logic. A generic CRUD factory would collapse distinct
      user-facing 404 strings and scoping behind one abstraction — high risk, exactly what
      the rules warn against. Left as separate endpoints; only the identical update-loop was
      shared out.
    - *format*: `isort --profile black` + `black` on all 7 files (largest `api_config.py`
      723 LOC, under the guard). Verified per-file that string constants are unchanged: 5
      files byte-identical, `api_config.py` adds only the helper docstring, `api_payments.py`
      drops only `"Bill not found"` (now sourced from `api.py` via the dedup — confirmed the
      string still exists there exactly once).
    - **`bills.api` import style preserved** (patch-survey constraint): still
      `from bills.services... import publish_bill_to_xero, resolve_xero_access_token_for_entity`
      and `import requests as _requests` (module form) — the names tests patch as
      `bills.api.*` are untouched.
    - verify: 14 failed / 398 passed at all 3 checkpoints — **no regressions**.
  - [ ] stage 4: `bills/tests/`
  - [ ] `bills/migrations/` — **excluded, see below**

## Tooling decision: isort MUST use `--profile black`

There is no isort config in the repo, and **isort's default profile fights black.** Default
isort rewrites parenthesized imports into hanging-indent style, which black then reverts —
running both in sequence produces churn. Measured repo-wide: default isort would rewrite
**33 files**, `--profile black` rewrites **27**, and on already-clean folders the black
profile is a no-op where the default is not.

The existing code is written in black style, so `--profile black` matches author intent.
**Always use `isort --profile black` in this repo.** If you add a `pyproject.toml` later,
set `[tool.isort] profile = "black"` so this is not rediscovered each session.

## Lessons

- **Snapshot behaviour before merging look-alike functions.** `.cleanse/perm_snapshot.py`
  exhaustively records outcome + exact message for every checker across every role/status
  combination. It caught nothing (the refactor was clean), but it is what makes "no
  behavioural change" a *verified* claim rather than a hopeful one. Re-run it after the
  formatter too — reformatting a file you just refactored can silently re-wrap a string.
- **Compare string constants against the right base.** An AST string-constant diff vs
  `HEAD` flags your own intentional refactors, not just formatter damage. To check a
  formatting step specifically, snapshot immediately *before* running the formatter.
- **`isort` on a directory containing an empty `__init__.py` can fail** with
  `InvalidSettingsPath`. Pass explicit files and add `--filter-files`, excluding
  `__init__.py`, e.g.
  `isort --profile black --filter-files $(ls bills/services/*.py | grep -v '__init__')`.
- **Extracting a helper can silently move a transaction boundary.** The first cut of
  `_upload_file_to_s3` pulled the S3 upload *inside* `transaction.atomic()`, which would
  have held a DB transaction open across a network call. Caught by diffing the atomic
  block's contents via AST before/after — not by the test suite, which stayed green.
  Always check what is inside the `with` block after an extraction.
- **The IDE's "not accessed" hints are not a dead-code oracle.** In `core/exceptions.py`
  the three handler functions are flagged unused but are registered via
  `@api.exception_handler(...)` decorators. Verified by asserting all 6 handlers are
  present on the API object after the edit.

## Deliberately left alone

- The 14 pre-existing test failures (separate work, per instructions).
- Everything named in the patch-target survey above.
- `bills/migrations/` — migration files are a historical record; editing applied migrations
  rewrites history against the live Postgres schema. Excluded from the cleanse entirely.
- **`config/urls.py`'s mid-file import block (lines 18-32, all marked `# noqa: E402`).**
  Seven `from bills.api* import ...router` statements sit after `api = NinjaAPI(...)` and
  `register_exception_handlers(api)` instead of at the top of the file. This looks like
  something a tidy-up should fix. I tested it: there is no circular import, and
  registering routers before the exception handlers works at runtime — so hoisting them
  *would* function. I left it anyway. The `# noqa: E402` markers on every line show the
  placement is deliberate, Django URLConfs commonly need late imports for app-loading
  reasons that only bite in specific startup orders (management commands, WSGI boot), and
  the payoff is purely cosmetic. Not worth the risk. Revisit only with a deliberate
  startup-order test, not as part of a formatting pass.
