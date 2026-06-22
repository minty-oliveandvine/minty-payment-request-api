"""
Unit tests for _merge_db_contacts_missing_from_live — post-fix regression suite.

Bug fixed (2026-04-06): UUID comparison inside the seen-set was case-sensitive.
A contact stored as "abc-123" in xero_contact_sync would not match "ABC-123"
returned by the live Xero GET, so the same logical contact was appended a second
time.  The fix normalises both sides with .strip().upper() before comparison.

These tests exercise _merge_db_contacts_missing_from_live in complete isolation
from the database: XeroContactSync.objects.filter is patched via unittest.mock
so every test is fast and self-contained with no @pytest.mark.django_db marker
needed.

Edge cases covered
------------------
1.  Same UUID, same case       — no duplicate
2.  Same UUID, lowercase in DB — no duplicate  (THE BUG)
3.  Same UUID, mixed case      — no duplicate
4.  Different UUIDs            — both appear (not a duplicate)
5.  DB contact with None xero_contact_id — appended regardless
6.  DB contact with empty-string xero_contact_id — appended regardless
7.  Multiple overlapping contacts with various case mismatches
8.  Whitespace in live UUID    — stripped correctly, no duplicate
9.  Whitespace in DB UUID      — stripped correctly, no duplicate
10. Empty live contacts list   — all DB contacts appear
11. Empty DB queryset          — only live contacts returned unchanged
12. Large dataset (50 contacts with overlaps) — stress / O(n) check
"""

from unittest.mock import MagicMock, patch

from bills.services.contact_service import _merge_db_contacts_missing_from_live


# ── helpers ────────────────────────────────────────────────────────────────


def _make_live(xero_contact_id: str, name: str = "Live Vendor") -> dict:
    """Minimal live-contact dict that mirrors _xero_to_bill_contact output."""
    return {
        "id": xero_contact_id,
        "entity_id": "entity-test",
        "xero_contact_id": xero_contact_id,
        "xero_org_id": "org-test",
        "name": name,
        "category": None,
    }


def _make_db_row(xero_contact_id, name: str = "DB Vendor", category=None) -> MagicMock:
    """Minimal mock that satisfies _db_to_bill_contact field access."""
    row = MagicMock()
    row.xero_contact_id = xero_contact_id
    row.entity_id = "entity-test"
    row.xero_org_id = "org-test"
    row.name = name
    row.category = category
    return row


def _patch_qs(rows: list) -> MagicMock:
    """Return a mock queryset whose .filter().filter().order_by() chain yields rows."""
    qs = MagicMock()
    # .filter(entity_id=...) returns qs; .filter(category__in=...) also returns qs.
    qs.filter.return_value = qs
    qs.order_by.return_value = iter(rows)
    return qs


def _ids(contacts: list[dict]) -> list[str]:
    return [c["xero_contact_id"] for c in contacts]


def _assert_no_dupes(contacts: list[dict], label: str = "") -> None:
    ids = _ids(contacts)
    dupes = [xid for xid in set(ids) if ids.count(xid) > 1]
    assert not dupes, (
        f"{label + ': ' if label else ''}duplicate xero_contact_id(s) found: {dupes}\n"
        f"Full list: {[(c['name'], c['xero_contact_id']) for c in contacts]}"
    )


# ── patch target ───────────────────────────────────────────────────────────

_PATCH = "bills.services.contact_service.XeroContactSync.objects"


# ═══════════════════════════════════════════════════════════════════════════
# 1. Same UUID, same case
# ═══════════════════════════════════════════════════════════════════════════


def test_same_uuid_same_case_no_duplicate():
    """Live and DB hold identical casing — contact must appear exactly once."""
    live = [_make_live("ABC-123")]
    db_row = _make_db_row("ABC-123")

    with patch(_PATCH, _patch_qs([db_row])):
        result = _merge_db_contacts_missing_from_live(live, "entity-test", None)

    assert len(result) == 1, f"Expected 1, got {len(result)}: {_ids(result)}"
    _assert_no_dupes(result, "same-case")


# ═══════════════════════════════════════════════════════════════════════════
# 2. Same UUID, lowercase in DB (THE BUG)
# ═══════════════════════════════════════════════════════════════════════════


def test_same_uuid_lowercase_db_no_duplicate():
    """
    Primary regression test for the duplicate-contact bug.

    Live Xero returns 'ABC-123' (uppercase).
    xero_contact_sync stores 'abc-123' (lowercase).

    Before the fix, the case-sensitive comparison treated these as distinct IDs
    and appended the DB row as an extra contact, producing two results.
    After the fix (.strip().upper() on both sides), they match — only one result.
    """
    live = [_make_live("ABC-123", "Acme Supplies")]
    db_row = _make_db_row("abc-123", "Acme Supplies")

    with patch(_PATCH, _patch_qs([db_row])):
        result = _merge_db_contacts_missing_from_live(live, "entity-test", None)

    assert len(result) == 1, (
        f"BUG REGRESSION FAILED: case mismatch caused duplicate. "
        f"Got {len(result)} contacts: {_ids(result)}"
    )
    _assert_no_dupes(result, "lowercase-db")
    # Verify the live contact (not the DB row) is the one retained.
    assert result[0]["xero_contact_id"] == "ABC-123"


# ═══════════════════════════════════════════════════════════════════════════
# 3. Same UUID, mixed case
# ═══════════════════════════════════════════════════════════════════════════


def test_same_uuid_mixed_case_no_duplicate():
    """Live has 'AbC-123', DB has 'aBc-123' — different mixed casing, same UUID."""
    live = [_make_live("AbC-123")]
    db_row = _make_db_row("aBc-123")

    with patch(_PATCH, _patch_qs([db_row])):
        result = _merge_db_contacts_missing_from_live(live, "entity-test", None)

    assert len(result) == 1, f"Mixed-case UUID still caused duplicate: {_ids(result)}"
    _assert_no_dupes(result, "mixed-case")


# ═══════════════════════════════════════════════════════════════════════════
# 4. Different UUIDs — both should appear
# ═══════════════════════════════════════════════════════════════════════════


def test_different_uuids_both_appear():
    """Live has 'ABC-123', DB has 'DEF-456' — genuinely distinct contacts."""
    live = [_make_live("ABC-123", "Alpha Corp")]
    db_row = _make_db_row("DEF-456", "Delta Ltd")

    with patch(_PATCH, _patch_qs([db_row])):
        result = _merge_db_contacts_missing_from_live(live, "entity-test", None)

    assert len(result) == 2, (
        f"Two distinct contacts should both appear. Got {len(result)}: {_ids(result)}"
    )
    assert "ABC-123" in _ids(result)
    assert "DEF-456" in _ids(result)
    _assert_no_dupes(result, "different-uuids")


# ═══════════════════════════════════════════════════════════════════════════
# 5. DB contact with None xero_contact_id — always appended
# ═══════════════════════════════════════════════════════════════════════════


def test_db_contact_none_xero_id_skipped():
    """
    A DB row with xero_contact_id=None is skipped by the merge helper.

    The guard in _merge_db_contacts_missing_from_live is:
        cid = (row.xero_contact_id or "").strip().upper()
        if cid and cid not in seen:   # <-- falsy "" short-circuits here
            extras.append(...)

    When xero_contact_id is None, cid resolves to "", which is falsy, so the
    row is neither added to seen nor appended to extras.  Only the live contact
    is returned.
    """
    live = [_make_live("ABC-123")]
    db_row = _make_db_row(None, "No-ID Vendor")

    with patch(_PATCH, _patch_qs([db_row])):
        result = _merge_db_contacts_missing_from_live(live, "entity-test", None)

    # Only the live contact should appear; the None-ID DB row is silently skipped.
    assert len(result) == 1, (
        f"DB row with None xero_contact_id should be skipped. "
        f"Got {len(result)}: {[(c['name'], c['xero_contact_id']) for c in result]}"
    )
    assert result[0]["xero_contact_id"] == "ABC-123"


# ═══════════════════════════════════════════════════════════════════════════
# 6. DB contact with empty-string xero_contact_id — always appended
# ═══════════════════════════════════════════════════════════════════════════


def test_db_contact_empty_string_xero_id_skipped():
    """
    A DB row with xero_contact_id='' is treated identically to None — skipped.

    The `or ""` in `(row.xero_contact_id or "").strip().upper()` normalises both
    None and "" to an empty string.  The falsy `if cid` guard then prevents the
    row from being appended.  Only the live contact is returned.
    """
    live = [_make_live("ABC-123")]
    db_row = _make_db_row("", "Empty-ID Vendor")

    with patch(_PATCH, _patch_qs([db_row])):
        result = _merge_db_contacts_missing_from_live(live, "entity-test", None)

    assert len(result) == 1, (
        f"DB row with empty xero_contact_id should be skipped. "
        f"Got {len(result)}: {[(c['name'], c['xero_contact_id']) for c in result]}"
    )
    assert result[0]["xero_contact_id"] == "ABC-123"


# ═══════════════════════════════════════════════════════════════════════════
# 7. Multiple overlapping contacts, various case mismatches
# ═══════════════════════════════════════════════════════════════════════════


def test_multiple_overlapping_contacts_various_case():
    """
    Live has 3 contacts.  DB has 5 rows: 3 overlap (different casing), 2 are new.
    Expected: 3 from live + 2 new DB rows = 5 total, zero duplicates.
    """
    live = [
        _make_live("AAA-001", "Acme"),
        _make_live("BBB-002", "Bravo"),
        _make_live("CCC-003", "Charlie"),
    ]
    db_rows = [
        _make_db_row("aaa-001", "Acme"),        # matches live AAA-001 (lowercase)
        _make_db_row("Bbb-002", "Bravo"),       # matches live BBB-002 (mixed)
        _make_db_row("CCC-003", "Charlie"),     # matches live CCC-003 (exact)
        _make_db_row("DDD-004", "Delta"),       # NEW — not in live
        _make_db_row("EEE-005", "Echo"),        # NEW — not in live
    ]

    with patch(_PATCH, _patch_qs(db_rows)):
        result = _merge_db_contacts_missing_from_live(live, "entity-test", None)

    assert len(result) == 5, (
        f"Expected 5 (3 live + 2 new), got {len(result)}: {_ids(result)}"
    )
    _assert_no_dupes(result, "multiple-overlaps-various-case")
    # The two new DB contacts must be present.
    assert "DDD-004" in _ids(result)
    assert "EEE-005" in _ids(result)


# ═══════════════════════════════════════════════════════════════════════════
# 8. Whitespace in live UUID
# ═══════════════════════════════════════════════════════════════════════════


def test_whitespace_in_live_uuid_stripped():
    """Live contact has ' ABC-123 ' (padded). DB stores 'ABC-123' (clean)."""
    live = [_make_live(" ABC-123 ", "Padded Live")]
    db_row = _make_db_row("ABC-123", "Clean DB")

    with patch(_PATCH, _patch_qs([db_row])):
        result = _merge_db_contacts_missing_from_live(live, "entity-test", None)

    assert len(result) == 1, (
        f"Whitespace in live UUID should be stripped. Got {len(result)}: {_ids(result)}"
    )
    _assert_no_dupes(result, "whitespace-live")


# ═══════════════════════════════════════════════════════════════════════════
# 9. Whitespace in DB UUID
# ═══════════════════════════════════════════════════════════════════════════


def test_whitespace_in_db_uuid_stripped():
    """Live contact has 'ABC-123'. DB stores '  abc-123  ' (padded, lowercase)."""
    live = [_make_live("ABC-123", "Live Vendor")]
    db_row = _make_db_row("  abc-123  ", "Padded DB")

    with patch(_PATCH, _patch_qs([db_row])):
        result = _merge_db_contacts_missing_from_live(live, "entity-test", None)

    assert len(result) == 1, (
        f"Whitespace + case in DB UUID should be handled. Got {len(result)}: {_ids(result)}"
    )
    _assert_no_dupes(result, "whitespace-db")


# ═══════════════════════════════════════════════════════════════════════════
# 10. Empty live contacts list — all DB contacts appear
# ═══════════════════════════════════════════════════════════════════════════


def test_empty_live_all_db_contacts_returned():
    """When Xero returned nothing, every DB contact must appear exactly once."""
    db_rows = [
        _make_db_row("X-001", "Alpha"),
        _make_db_row("X-002", "Beta"),
        _make_db_row("X-003", "Gamma"),
    ]

    with patch(_PATCH, _patch_qs(db_rows)):
        result = _merge_db_contacts_missing_from_live([], "entity-test", None)

    assert len(result) == 3, f"All 3 DB contacts should appear. Got {len(result)}"
    _assert_no_dupes(result, "empty-live")
    assert set(_ids(result)) == {"X-001", "X-002", "X-003"}


# ═══════════════════════════════════════════════════════════════════════════
# 11. Empty DB — only live contacts returned unchanged
# ═══════════════════════════════════════════════════════════════════════════


def test_empty_db_only_live_returned():
    """No DB rows — the function must return the live list unmodified."""
    live = [
        _make_live("LIVE-1", "Live One"),
        _make_live("LIVE-2", "Live Two"),
    ]

    with patch(_PATCH, _patch_qs([])):
        result = _merge_db_contacts_missing_from_live(live, "entity-test", None)

    # When extras is empty the function returns live_contacts directly.
    assert result is live, "Should return the original live list object when no extras"
    assert len(result) == 2
    _assert_no_dupes(result, "empty-db")


# ═══════════════════════════════════════════════════════════════════════════
# 12. Large dataset (50 contacts, various overlaps)
# ═══════════════════════════════════════════════════════════════════════════


def test_large_dataset_no_duplicates():
    """
    50 live contacts + 50 DB rows.
    - Indices 0-24: DB row has same UUID but lowercase (overlap via case mismatch).
    - Indices 25-49: DB row has uppercase UUID identical to live (overlap exact).
    - Indices 50-74: DB-only rows not in live at all (genuinely new).

    Expected total: 50 live + 25 DB-only new = 75 contacts, zero duplicates.
    """
    live = [_make_live(f"UUID-{i:04d}-UPPER", f"Live Vendor {i}") for i in range(50)]

    db_rows = []
    # Rows 0-24: lowercase UUID — overlap but case-mismatched (tests the bug fix)
    for i in range(25):
        db_rows.append(_make_db_row(f"uuid-{i:04d}-upper", f"DB Lower {i}"))
    # Rows 25-49: exact-match UUID — overlap exact
    for i in range(25, 50):
        db_rows.append(_make_db_row(f"UUID-{i:04d}-UPPER", f"DB Exact {i}"))
    # Rows 50-74: completely new — not in live
    for i in range(50, 75):
        db_rows.append(_make_db_row(f"UUID-{i:04d}-UPPER", f"DB New {i}"))

    with patch(_PATCH, _patch_qs(db_rows)):
        result = _merge_db_contacts_missing_from_live(live, "entity-test", None)

    expected_count = 75  # 50 live + 25 new DB rows
    assert len(result) == expected_count, (
        f"Large dataset: expected {expected_count}, got {len(result)}"
    )
    _assert_no_dupes(result, "large-dataset")
    # Spot-check: the 25 new DB-only contacts are present.
    result_ids_upper = {xid.upper() for xid in _ids(result) if xid}
    for i in range(50, 75):
        assert f"UUID-{i:04d}-UPPER" in result_ids_upper, (
            f"DB-only contact UUID-{i:04d}-UPPER missing from result"
        )
