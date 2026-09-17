# Minty Billing — Database Design

> **Schema:** `pettycashv2` (shared with Module 1 Flask app)
> **Engine:** PostgreSQL 15
> **All PKs:** UUID v4 (`varchar(36)`)

---

## Table Overview

| Table | Managed By | Description |
|-------|-----------|-------------|
| `user` | Flask (Module 1) | User accounts, auth, Xero tokens |
| `entities` | Flask (Module 1) | Business entities / organizations |
| `user_entity` | Flask (Module 1) | User ↔ Entity access (M2M) |
| `account_info` | Flask (Module 1) | Xero chart of accounts per entity |
| `xero_contact_sync` | Flask (Module 1) | Xero contacts per entity |
| **`bill`** | **Django (Module 2)** | **Bill records** |
| **`bill_line_item`** | **Django (Module 2)** | **Line items per bill** |
| **`attachment`** | **Django (Module 2)** | **Uploaded invoice files** |
| **`audit`** | **Django (Module 2)** | **Audit trail entries** |

---

## Entity Relationship Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                    EXISTING (Module 1 — Flask)                   │
│                       managed = False                            │
│                                                                  │
│  ┌──────────────┐        ┌───────────────┐                       │
│  │    user      │        │   entities    │                       │
│  │──────────────│        │───────────────│                       │
│  │ id (PK)      │        │ id (PK)       │                       │
│  │ email        │        │ name          │                       │
│  │ first_name   │        │ country_code  │                       │
│  │ last_name    │        │ currency_code │                       │
│  │ username     │        │ xero_org_id   │                       │
│  │ role         │        │ status        │                       │
│  │ access_token │        └───────┬───────┘                       │
│  │ refresh_token│                │                               │
│  └──────┬───────┘                │                               │
│         │    ┌───────────────────┘                               │
│         │    │                                                    │
│  ┌──────▼────▼──────┐    ┌─────────────────┐  ┌────────────────┐│
│  │  user_entity     │    │  account_info   │  │xero_contact    ││
│  │──────────────────│    │─────────────────│  │_sync           ││
│  │ user_id (PK,FK)  │    │ id (PK)         │  │────────────────││
│  │ entity_id (FK)   │    │ entity_id (FK)  │  │ id (PK)        ││
│  │ role             │    │ type            │  │ entity_id (FK) ││
│  │ approved         │    │ name            │  │ xero_contact_id││
│  └──────────────────┘    │ xero_account_id │  │ name           ││
│                          │ xero_code       │  │ category       ││
│                          │ status          │  └────────────────┘│
│                          └─────────────────┘                     │
└─────────────────────────────────────────────────────────────────┘

          │ entity_id          │ uploaded_by        │ xero_contact_id
          │ (logical FK)       │ (logical FK)       │ (logical FK)
          ▼                    ▼                    ▼

┌─────────────────────────────────────────────────────────────────┐
│                      NEW (Module 2 — Django)                     │
│                        managed = True                            │
│                                                                  │
│  ┌───────────────────┐         ┌───────────────────┐             │
│  │    attachment      │◄───FK──┤       bill         │             │
│  │───────────────────│         │───────────────────│             │
│  │ id (PK)  uuid     │         │ id (PK)  uuid     │             │
│  │ original_name     │         │ entity_id         │             │
│  │ stored_name       │         │ contact           │             │
│  │ path              │         │ xero_contact_id   │             │
│  │ type              │         │ status            │             │
│  │ size              │         │ amount            │             │
│  │ uploaded_by       │         │ description       │             │
│  │ created_at        │         │ due_date          │             │
│  └───────────────────┘         │ invoice_date      │             │
│                                │ paid_date         │             │
│                                │ uploaded_by       │             │
│                                │ attachment_id (FK)│             │
│                                │ published         │             │
│                                │ xero_invoice_id   │             │
│                                │ created_at        │             │
│                                │ updated_at        │             │
│                                └────────┬──────────┘             │
│                                         │                        │
│                              ┌──────────┴──────────┐             │
│                              │                     │             │
│                     ┌────────▼────────┐   ┌────────▼────────┐    │
│                     │ bill_line_item  │   │     audit       │    │
│                     │────────────────│   │────────────────│    │
│                     │ id (PK)  uuid  │   │ id (PK)  uuid  │    │
│                     │ bill_id (FK)   │   │ bill_id (FK)   │    │
│                     │ description    │   │ action         │    │
│                     │ amount         │   │ detail (JSON)  │    │
│                     │ xero_account_id│   │ date           │    │
│                     │ xero_account   │   │ user_id        │    │
│                     │ _name          │   └────────────────┘    │
│                     └────────────────┘                          │
└─────────────────────────────────────────────────────────────────┘
```

---

## New Tables (Module 2)

### `bill`

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | `varchar(36)` | PK, default uuid4 | Unique bill identifier |
| `entity_id` | `varchar(36)` | NOT NULL, INDEX | Logical FK → `entities.id` |
| `contact` | `varchar(50)` | default `""` | Vendor / supplier name |
| `xero_contact_id` | `varchar(36)` | default `""` | Logical FK → Xero contact |
| `status` | `varchar(50)` | NOT NULL, default `"draft"` | `draft` / `submitted` / `paid` |
| `amount` | `decimal(10,2)` | default `0` | Total bill amount |
| `description` | `text` | default `""` | Bill description |
| `due_date` | `datetime` | NULL | Payment due date |
| `invoice_date` | `datetime` | NULL | Date on the invoice |
| `paid_date` | `datetime` | NULL | When marked as paid |
| `uploaded_by` | `varchar(50)` | NOT NULL, INDEX | Logical FK → `user.id` |
| `attachment_id` | `varchar(36)` | NULL, FK → `attachment.id` | ON DELETE SET NULL |
| `published` | `varchar(50)` | default `"not_published"` | `not_published` / `published` |
| `xero_invoice_id` | `varchar(36)` | default `""` | Xero invoice ID after publish |
| `created_at` | `datetime` | auto, NOT NULL | Record creation timestamp |
| `updated_at` | `datetime` | auto, NOT NULL | Last modification timestamp |

**Indexes:** `entity_id`, `uploaded_by`
**Default ordering:** `-created_at` (newest first)

---

### `bill_line_item`

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | `varchar(36)` | PK, default uuid4 | Unique line item identifier |
| `bill_id` | `varchar(36)` | FK → `bill.id`, ON DELETE CASCADE | Parent bill |
| `description` | `varchar(255)` | NOT NULL | Line item description |
| `amount` | `decimal(10,2)` | NOT NULL | Line item amount |
| `xero_account_id` | `varchar(100)` | default `""` | Xero account code |
| `xero_account_name` | `varchar(150)` | default `""` | Xero account display name |

**Cascade:** Deleting a bill deletes all its line items.

---

### `attachment`

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | `varchar(36)` | PK, default uuid4 | Unique attachment identifier |
| `original_name` | `varchar(200)` | NOT NULL | Original uploaded filename |
| `stored_name` | `varchar(150)` | NOT NULL | UUID-based filename on disk/S3 |
| `path` | `text` | NOT NULL | Storage path (relative) |
| `type` | `varchar(50)` | NOT NULL | MIME type (e.g. `image/jpeg`, `application/pdf`) |
| `size` | `integer` | NOT NULL | File size in bytes |
| `uploaded_by` | `varchar(150)` | NOT NULL | Logical FK → `user.id` |
| `created_at` | `datetime` | auto, NOT NULL | Upload timestamp |

---

### `audit`

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | `varchar(36)` | PK, default uuid4 | Unique audit entry identifier |
| `bill_id` | `varchar(36)` | FK → `bill.id`, ON DELETE CASCADE | Parent bill |
| `action` | `varchar(100)` | NOT NULL | Action type (see values below) |
| `detail` | `text` | default `""` | JSON payload of changed fields or context |
| `date` | `datetime` | auto, NOT NULL | When the action occurred |
| `user_id` | `varchar(36)` | NOT NULL | Logical FK → `user.id` (who did it) |

**Cascade:** Deleting a bill deletes all its audit entries.
**Default ordering:** `date` ASC (chronological)

**Action values:**

| Action | Trigger |
|--------|---------|
| `created` | Bill first saved |
| `edited` | Any field updated (detail = JSON diff) |
| `submitted` | Draft → Submitted |
| `marked_paid` | Submitted → Paid |
| `published_to_xero` | Published to Xero API |
| `attachment_uploaded` | Invoice file attached |
| `attachment_deleted` | Attachment removed |

---

## Status Lifecycle

```
                ┌──────────────┐
                │    DRAFT     │
                │  (editable)  │
                └──────┬───────┘
                       │ POST /bills/{id}/submit
                       │ requires: attachment + complete line items
                       ▼
                ┌──────────────┐
                │  SUBMITTED   │
                │  (editable)  │
                └──────┬───────┘
                       │ POST /bills/{id}/mark-paid
                       │ sets paid_date, locks record
                       ▼
                ┌──────────────┐
                │     PAID     │
                │  (IMMUTABLE) │  ← no edits, no deletes, no role override
                └──────────────┘

  Published flag is independent:
  ┌─────────────────┐     POST /xero/publish/{id}     ┌─────────────┐
  │  not_published   │ ──────────────────────────────► │  published  │
  └─────────────────┘     (any status with complete    └─────────────┘
                           line items + attachment)
```

---

## Cross-Module Relationships (Logical FKs)

These are **not enforced at the database level** to keep Module 1 and Module 2 decoupled. They are validated in application code.

| Module 2 Column | References | Module 1 Table |
|------------------|-----------|----------------|
| `bill.entity_id` | → | `entities.id` |
| `bill.uploaded_by` | → | `user.id` |
| `bill.xero_contact_id` | → | `xero_contact_sync.xero_contact_id` |
| `audit.user_id` | → | `user.id` |
| `attachment.uploaded_by` | → | `user.id` |
| `bill_line_item.xero_account_id` | → | `account_info.xero_account_id` |

---

## SQL (PostgreSQL DDL)

```sql
-- Schema: pettycashv2

CREATE TABLE pettycashv2.attachment (
    id              VARCHAR(36)  PRIMARY KEY,
    original_name   VARCHAR(200) NOT NULL,
    stored_name     VARCHAR(150) NOT NULL,
    path            TEXT         NOT NULL,
    type            VARCHAR(50)  NOT NULL,
    size            INTEGER      NOT NULL,
    uploaded_by     VARCHAR(150) NOT NULL,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE pettycashv2.bill (
    id              VARCHAR(36)    PRIMARY KEY,
    entity_id       VARCHAR(36)    NOT NULL,
    contact         VARCHAR(50)    NOT NULL DEFAULT '',
    xero_contact_id VARCHAR(36)    NOT NULL DEFAULT '',
    status          VARCHAR(50)    NOT NULL DEFAULT 'draft',
    amount          NUMERIC(10,2)  NOT NULL DEFAULT 0,
    description     TEXT           NOT NULL DEFAULT '',
    due_date        TIMESTAMPTZ,
    invoice_date    TIMESTAMPTZ,
    paid_date       TIMESTAMPTZ,
    uploaded_by     VARCHAR(50)    NOT NULL,
    attachment_id   VARCHAR(36)    REFERENCES pettycashv2.attachment(id) ON DELETE SET NULL,
    published       VARCHAR(50)    NOT NULL DEFAULT 'not_published',
    xero_invoice_id VARCHAR(36)    NOT NULL DEFAULT '',
    created_at      TIMESTAMPTZ    NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ    NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_bill_entity_id   ON pettycashv2.bill (entity_id);
CREATE INDEX idx_bill_uploaded_by ON pettycashv2.bill (uploaded_by);
CREATE INDEX idx_bill_status      ON pettycashv2.bill (status);
CREATE INDEX idx_bill_created_at  ON pettycashv2.bill (created_at DESC);

CREATE TABLE pettycashv2.bill_line_item (
    id                VARCHAR(36)   PRIMARY KEY,
    bill_id           VARCHAR(36)   NOT NULL REFERENCES pettycashv2.bill(id) ON DELETE CASCADE,
    description       VARCHAR(255)  NOT NULL,
    amount            NUMERIC(10,2) NOT NULL,
    xero_account_id   VARCHAR(100)  NOT NULL DEFAULT '',
    xero_account_name VARCHAR(150)  NOT NULL DEFAULT ''
);

CREATE INDEX idx_line_item_bill_id ON pettycashv2.bill_line_item (bill_id);

CREATE TABLE pettycashv2.audit (
    id       VARCHAR(36)  PRIMARY KEY,
    bill_id  VARCHAR(36)  NOT NULL REFERENCES pettycashv2.bill(id) ON DELETE CASCADE,
    action   VARCHAR(100) NOT NULL,
    detail   TEXT         NOT NULL DEFAULT '',
    date     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    user_id  VARCHAR(36)  NOT NULL
);

CREATE INDEX idx_audit_bill_id ON pettycashv2.audit (bill_id);
CREATE INDEX idx_audit_date    ON pettycashv2.audit (date);
```
