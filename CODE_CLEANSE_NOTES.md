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
- [ ] `docs`
- [ ] `bills`

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
