# Local Port Allocation (Minty multi-repo)

The Minty system is several repos, each run in its own Docker stack today and,
later, together via a single "start all four" orchestrator. To avoid the most
common multi-repo Docker failure — two services claiming the same host port —
this table is the **single source of truth** for local port assignments.

Keep this file in sync across repos when ports change, and confirm any change
with the affected teams before merging.

| Service | App port | DB port | Status | Notes |
|---------|:--------:|:-------:|--------|-------|
| Pettycash backend (Module 1, Flask) | `5001` | `5432` | **Fixed** | Existing; other services reference `http://localhost:5001`. |
| Billing backend (Module 2, Django) — **this repo** | `5002` | `5433` | **Proposed** | Awaiting team sign-off. DB published on 5433 because 5432 is taken by pettycash. |
| Billing frontend | `3000` | — | **Fixed** | Implied by this repo's `FRONTEND_APP_URL` default. |
| Onboarding frontend | `3001` | — | **Fixed** | Next free frontend port after 3000. |

## Rules

- **App ports** are what you open in the browser / hit with the API.
- **DB ports** are the *host* mapping only (for psql or a GUI). Inside Docker,
  every app reaches its own database at `db:5432` over its compose network —
  the host mapping never affects container-to-container traffic.
- Pick the **next free port** in the same family when adding a service
  (backends 500x, frontends 300x, databases 543x) and record it here first.

## Status legend

- **Fixed** — in use and depended on by other services; do not change without a
  coordinated update across every repo that references it.
- **Proposed** — suggested here but not yet agreed by all teams. Confirm before
  relying on it.

> When the combined orchestrator lands, it should read its port map from this
> table so there is no second place for ports to drift.
