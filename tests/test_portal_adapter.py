"""Local, credential-free portal integration tests.

The scenarios mix delayed renders, identical case ids on different platforms,
pages that do not refresh, page identity mismatches and session expiry.
After every run the persisted rows are checked against the page evidence of
the request that produced them, and a previously correct row must survive an
error page.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from claims_rpa.application import run_fixture
from claims_rpa.browser import BrowserAdapter
from claims_rpa.contract import validate_query_result
from claims_rpa.errors import (
    CaseNotFound,
    IdentityMismatch,
    LoginFailed,
    PageStructureChanged,
    ProvenanceError,
    StaleResult,
)
from claims_rpa.pipeline import ClaimsPipeline
from claims_rpa.portal import LocalPortal, PageView
from claims_rpa.storage import CaseStore

ROOT = Path(__file__).parents[1]
PORTAL_FIXTURE = str(ROOT / "fixtures/local_portal.json")
CASE_FIXTURE = str(ROOT / "fixtures/portal_cases.json")


def _portal() -> LocalPortal:
    return LocalPortal.from_fixture(PORTAL_FIXTURE)


def _raises(exc_type, call, *args, **kwargs):
    """Return the raised exception (compatible with pytest and the shim)."""
    try:
        call(*args, **kwargs)
    except exc_type as exc:
        return exc
    raise AssertionError(f"expected {exc_type.__name__} was not raised")


def _run(cases, adapter, store, dry_run=False):
    return ClaimsPipeline(cases, adapter.query_case, store).run(dry_run)


def _db_rows(path: str) -> dict[tuple[str, str], dict]:
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(
            "SELECT platform, case_id, status, receipt_id, source, verified, "
            "page_platform, page_case_id, session_epoch, request_token FROM cases"
        ).fetchall()
    finally:
        conn.close()
    keys = (
        "platform",
        "case_id",
        "status",
        "receipt_id",
        "source",
        "verified",
        "page_platform",
        "page_case_id",
        "session_epoch",
        "request_token",
    )
    return {(row[0], row[1]): dict(zip(keys, row)) for row in rows}


def _failures(path: str) -> list[dict]:
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(
            "SELECT platform, case_id, category, page_platform, page_case_id FROM failures"
        ).fetchall()
    finally:
        conn.close()
    keys = ("platform", "case_id", "category", "page_platform", "page_case_id")
    return [dict(zip(keys, row)) for row in rows]


def test_two_local_platforms_and_consecutive_cases_are_tracked(tmp_path):
    db = str(tmp_path / "portal.sqlite3")
    summary = run_fixture(CASE_FIXTURE, db, dry_run=False, portal=_portal())
    assert summary == {"processed": 4, "changed": 4, "failed": 0, "cases": 4, "dry_run": 0}
    rows = _db_rows(db)
    assert len(rows) == 4
    for (platform, case_id), row in rows.items():
        assert row["source"] == "portal"
        assert row["verified"] == 1
        # every persisted row is bound to the page that rendered it
        assert (platform, case_id) == (row["page_platform"], row["page_case_id"])
        assert row["session_epoch"] >= 1
        assert row["request_token"].startswith(f"{platform}:{case_id}:")
    assert rows[("portal-a", "CASE-1")]["status"] == "approved"
    assert rows[("portal-a", "CASE-1")]["receipt_id"] == "A-1"
    assert rows[("portal-b", "CASE-2")]["status"] == "approved"
    assert rows[("portal-b", "CASE-2")]["receipt_id"] == "B-2"


def test_same_case_id_on_two_platforms_stays_separate(tmp_path):
    db = str(tmp_path / "same-id.sqlite3")
    portal = LocalPortal.from_fixture(PORTAL_FIXTURE)
    adapter = BrowserAdapter(dry_run=False, portal=portal)
    store = CaseStore(db)
    try:
        a = adapter.query_case("portal-a", "CASE-1")
        b = adapter.query_case("portal-b", "CASE-1")
        assert a["status"] == "approved" and a["receipt_id"] == "A-1"
        assert b["status"] == "pending" and b["receipt_id"] == "B-1"
        assert store.upsert(a) and store.upsert(b)
        saved_a = store.get_case("portal-a", "CASE-1")
        saved_b = store.get_case("portal-b", "CASE-1")
    finally:
        store.close()
    assert saved_a["page_platform"] == "portal-a"
    assert saved_b["page_platform"] == "portal-b"
    assert saved_a["receipt_id"] != saved_b["receipt_id"]


def test_delayed_old_result_is_not_written_for_the_next_case(tmp_path):
    """A previous search renders late, after a newer search started."""
    db = str(tmp_path / "delayed.sqlite3")
    portal = LocalPortal.from_fixture(PORTAL_FIXTURE)
    page = portal.page_for("portal-a")
    adapter = BrowserAdapter(dry_run=False, portal=portal, max_polls=4)
    store = CaseStore(db)
    try:
        assert store.upsert(adapter.query_case("portal-a", "CASE-1"))
        # The old CASE-1 view is still what the browser shows when CASE-2 runs.
        old = PageView("portal-a", "CASE-1", True, "approved", "A-1", "old-token", page.session_epoch)
        page.deliver(old)
        page.freeze()
        summary = _run([{"platform": "portal-a", "case_id": "CASE-2"}], adapter, store)
        assert summary["changed"] == 0 and summary["failed"] == 1
        failures = store.failures()
        assert failures[-1]["category"] == "stale"
        # the audit row names the request and the stale page it refused
        assert failures[-1]["case_id"] == "CASE-2"
        assert failures[-1]["page_case_id"] == "CASE-1"
        # the previously saved correct CASE-1 row is untouched by the failed query
        saved = store.get_case("portal-a", "CASE-1")
        assert (saved["status"], saved["receipt_id"]) == ("approved", "A-1")
        assert store.get_case("portal-a", "CASE-2") is None
        # the stale page was reset, so the next case starts clean
        page.freeze(False)
        assert store.upsert(adapter.query_case("portal-a", "CASE-2"))
        assert store.get_case("portal-a", "CASE-2")["status"] == "pending"
    finally:
        store.close()


def test_late_render_from_expired_session_is_rejected(tmp_path):
    db = str(tmp_path / "late.sqlite3")
    portal = LocalPortal.from_fixture(PORTAL_FIXTURE)
    page = portal.page_for("portal-a")
    page.login()
    adapter = BrowserAdapter(dry_run=False, portal=portal, max_polls=4)
    store = CaseStore(db)
    try:
        stale = PageView(
            "portal-a", "CASE-2", True, "approved", "A-2",
            "token-from-old-epoch", page.session_epoch - 1,
        )
        page.deliver(stale)
        page.freeze()
        exc = _raises((StaleResult, LoginFailed), adapter.query_case, "portal-a", "CASE-2")
        # nothing is written for CASE-2 and the stale page is never echoed
        assert store.get_case("portal-a", "CASE-2") is None
        assert exc.request_token.startswith("portal-a:CASE-2:")
        assert exc.request_token != "token-from-old-epoch"
        page.freeze(False)
        assert store.upsert(adapter.query_case("portal-a", "CASE-2"))
        assert store.get_case("portal-a", "CASE-2")["status"] == "pending"
    finally:
        store.close()


def test_page_missing_refresh_does_not_write_previous_case(tmp_path):
    db = str(tmp_path / "nofresh.sqlite3")
    portal = LocalPortal.from_fixture(PORTAL_FIXTURE)
    adapter = BrowserAdapter(dry_run=False, portal=portal, max_polls=3)
    store = CaseStore(db)
    try:
        assert store.upsert(adapter.query_case("portal-a", "CASE-1"))
        portal.page_for("portal-a").freeze()
        exc = _raises(Exception, adapter.query_case, "portal-a", "CASE-2")
        assert exc.category in {"timeout", "stale"}
        _run([{"platform": "portal-a", "case_id": "CASE-2"}], adapter, store)
        assert store.get_case("portal-a", "CASE-1")["status"] == "approved"
        assert store.get_case("portal-a", "CASE-2") is None
        assert store.failures()[-1]["category"] in {"timeout", "stale"}
        # the next case is still queryable after unfreezing
        portal.page_for("portal-a").freeze(False)
        assert store.upsert(adapter.query_case("portal-a", "CASE-2"))
        assert store.get_case("portal-a", "CASE-2")["status"] == "pending"
    finally:
        store.close()


def test_expired_session_relogins_once_with_a_new_epoch():
    portal = LocalPortal.from_fixture(PORTAL_FIXTURE)
    page = portal.page_for("portal-a")
    adapter = BrowserAdapter(dry_run=False, portal=portal, max_polls=12)
    page.set_search_delay(2)
    page.expire_after_polls(1)  # session dies while the first render is pending
    result = adapter.query_case("portal-a", "CASE-1")
    # recovery used exactly one re-login: epoch 1 for the first session, 2 after
    assert page.session_epoch == 2
    assert result["source"] == "portal" and result["verified"] is True
    assert result["session_epoch"] == 2
    assert result["status"] == "approved"


def test_expiry_without_relogin_fails_as_auth_and_next_case_still_runs(tmp_path):
    portal = LocalPortal.from_fixture(PORTAL_FIXTURE)
    page = portal.page_for("portal-a")
    adapter = BrowserAdapter(dry_run=False, portal=portal, max_polls=12, allow_relogin=False)
    page.set_search_delay(2)
    page.expire_after_polls(1)
    store = CaseStore(db := str(tmp_path / "expiry.sqlite3"))
    try:
        summary = _run([{"platform": "portal-a", "case_id": "CASE-1"}], adapter, store)
        assert summary["failed"] == 1 and summary["changed"] == 0
        assert store.failures()[-1]["category"] == "auth"
        assert store.get_case("portal-a", "CASE-1") is None
        # the next case re-logs in on its own and succeeds
        page.set_search_delay(0)
        assert store.upsert(adapter.query_case("portal-a", "CASE-1"))
        assert store.get_case("portal-a", "CASE-1")["status"] == "approved"
    finally:
        store.close()


def test_relogin_failure_is_reported_as_auth():
    portal = LocalPortal.from_fixture(PORTAL_FIXTURE)
    page = portal.page_for("portal-a")
    adapter = BrowserAdapter(dry_run=False, portal=portal, max_polls=12, allow_relogin=False)
    page.login()
    page.expire()
    page.set_login_ok(False)
    exc = _raises(LoginFailed, adapter.query_case, "portal-a", "CASE-1")
    assert exc.category == "auth"


def test_login_failure_is_distinguishable_and_does_not_stop_next_case(tmp_path):
    db = str(tmp_path / "login.sqlite3")
    portal = LocalPortal.from_fixture(PORTAL_FIXTURE)
    portal.page_for("portal-a").set_login_ok(False)
    summary = run_fixture(CASE_FIXTURE, db, dry_run=False, portal=portal)
    # portal-a cases fail with auth; portal-b cases still succeed
    assert summary["processed"] == 4
    assert summary["failed"] == 2
    assert summary["cases"] == 2
    categories = {row["category"] for row in _failures(db)}
    assert categories == {"auth"}
    assert {row["platform"] for row in _failures(db)} == {"portal-a"}


def test_case_not_found_and_structure_change_have_distinct_categories():
    portal = LocalPortal.from_fixture(PORTAL_FIXTURE)
    adapter = BrowserAdapter(dry_run=False, portal=portal)
    exc = _raises(CaseNotFound, adapter.query_case, "portal-a", "CASE-404")
    assert exc.category == "not_found"

    portal.page_for("portal-b").set_structure_ok(False)
    exc = _raises(PageStructureChanged, adapter.query_case, "portal-b", "CASE-2")
    assert exc.category == "structure"


def test_page_identity_mismatch_is_never_persisted(tmp_path):
    db = str(tmp_path / "identity.sqlite3")
    portal = LocalPortal.from_fixture(PORTAL_FIXTURE)
    page = portal.page_for("portal-a")
    adapter = BrowserAdapter(dry_run=False, portal=portal)
    store = CaseStore(db)
    try:
        assert store.upsert(adapter.query_case("portal-a", "CASE-1"))
        page.mispresent_next(platform="portal-b")
        summary = _run([{"platform": "portal-a", "case_id": "CASE-2"}], adapter, store)
        assert summary["changed"] == 0 and summary["failed"] == 1
        # the wrong page did not overwrite the saved CASE-1
        assert store.get_case("portal-a", "CASE-1")["receipt_id"] == "A-1"
        assert store.get_case("portal-a", "CASE-2") is None
        assert store.failures()[-1]["category"] == "identity"
        # after the bad page, the following case works
        assert store.upsert(adapter.query_case("portal-a", "CASE-3"))
        assert store.get_case("portal-a", "CASE-3")["status"] == "rejected"
    finally:
        store.close()


def test_page_showing_other_case_with_matching_search_token_is_rejected():
    portal = LocalPortal.from_fixture(PORTAL_FIXTURE)
    page = portal.page_for("portal-a")
    adapter = BrowserAdapter(dry_run=False, portal=portal)
    page.mispresent_next(case_id="CASE-1")
    _raises(IdentityMismatch, adapter.query_case, "portal-a", "CASE-2")
    # the next search is clean
    assert adapter.query_case("portal-a", "CASE-2")["status"] == "pending"


def test_contract_rejects_wrong_identity_and_simulated_confirmation():
    portal = LocalPortal.from_fixture(PORTAL_FIXTURE)
    adapter = BrowserAdapter(dry_run=False, portal=portal)
    result = adapter.query_case("portal-a", "CASE-1")
    assert validate_query_result(result, "portal-a", "CASE-1", dry_run=False)["verified"] is True
    _raises(IdentityMismatch, validate_query_result, result, "portal-b", "CASE-1", dry_run=False)

    simulated = BrowserAdapter(dry_run=True).query_case("portal-a", "CASE-1")
    assert simulated["source"] == "simulated" and simulated["verified"] is False
    # keyword-only dry_run needs an explicit call
    try:
        validate_query_result(simulated, "portal-a", "CASE-1", dry_run=False)
    except ProvenanceError as exc:
        assert exc.category == "provenance"
    else:  # pragma: no cover
        raise AssertionError("simulated confirmation was accepted as real")
    assert validate_query_result(simulated, "portal-a", "CASE-1", dry_run=True)["source"] == "simulated"


def test_dry_run_simulation_is_marked_in_the_database(tmp_path):
    db = str(tmp_path / "dry.sqlite3")
    summary = run_fixture(str(ROOT / "fixtures/cases.json"), db, True)
    assert summary["changed"] == 2 and summary["failed"] == 1
    rows = _db_rows(db)
    # simulated approved values are stored, but never as verified portal data
    assert {row["source"] for row in rows.values()} == {"simulated"}
    assert {row["verified"] for row in rows.values()} == {0}


def test_dry_run_simulated_approved_cannot_be_written_as_real_confirmation(tmp_path):
    db = str(tmp_path / "promote.sqlite3")
    store = CaseStore(db)
    try:
        simulated = BrowserAdapter(dry_run=True).query_case("portal-a", "CASE-1")
        try:
            validate_query_result(simulated, "portal-a", "CASE-1", dry_run=False)
        except ProvenanceError:
            pass
        else:  # pragma: no cover
            raise AssertionError("simulated approved was accepted as a real confirmation")
        # the portal adapter, by contrast, is verified real data
        real = BrowserAdapter(dry_run=False, portal=_portal()).query_case("portal-a", "CASE-1")
        saved = validate_query_result(real, "portal-a", "CASE-1", dry_run=False)
    finally:
        store.close()
    assert saved["verified"] is True and saved["source"] == "portal"


def test_audit_trail_matches_every_case_change(tmp_path):
    """Each accepted row's token/epoch can be traced to its own request."""
    db = str(tmp_path / "audit.sqlite3")
    portal = LocalPortal.from_fixture(PORTAL_FIXTURE)
    portal.page_for("portal-a").set_search_delay(1)  # delayed renders, not frozen
    summary = run_fixture(CASE_FIXTURE, db, dry_run=False, portal=portal)
    assert summary["failed"] == 0
    rows = _db_rows(db)
    assert len(rows) == 4
    assert {row["page_platform"] for row in rows.values()} == {"portal-a", "portal-b"}
    for (platform, case_id), row in rows.items():
        assert row["page_platform"] == platform
        assert row["page_case_id"] == case_id
        assert row["request_token"].startswith(f"{platform}:{case_id}:")


def test_portal_surface_is_read_only():
    """Portal queries must not expose submit/update/delete actions."""
    page = _portal().page_for("portal-a")
    forbidden = {"submit", "update", "delete", "cancel", "modify", "write", "save"}
    public = {name for name in dir(page) if not name.startswith("_")}
    assert not (public & forbidden)


def test_interleaved_platforms_stale_pages_freeze_and_expiry(tmp_path):
    """One batch where every failure mode happens next to healthy queries."""
    db = str(tmp_path / "interleaved.sqlite3")
    portal = LocalPortal.from_fixture(PORTAL_FIXTURE)
    page_a = portal.page_for("portal-a")
    page_b = portal.page_for("portal-b")
    adapter = BrowserAdapter(dry_run=False, portal=portal, max_polls=4)
    store = CaseStore(db)

    cases = [
        {"platform": "portal-a", "case_id": "CASE-1"},
        {"platform": "portal-b", "case_id": "CASE-1"},  # same id, other platform
        {"platform": "portal-a", "case_id": "CASE-2"},
        {"platform": "portal-a", "case_id": "CASE-3"},
        {"platform": "portal-b", "case_id": "CASE-2"},
    ]
    try:
        # 1) healthy portal-a CASE-1
        assert store.upsert(adapter.query_case("portal-a", "CASE-1"))
        # 2) same case id on portal-b is resolved by portal-b's own page
        assert store.upsert(adapter.query_case("portal-b", "CASE-1"))
        # 3) portal-a freezes while showing an old CASE-1 render -> no write
        old = PageView("portal-a", "CASE-1", True, "approved", "A-1", "old-token", page_a.session_epoch)
        page_a.deliver(old)
        page_a.freeze()
        assert store.upsert(adapter.query_case("portal-b", "CASE-2"))
        summary = _run([{"platform": "portal-a", "case_id": "CASE-2"}], adapter, store)
        assert summary["changed"] == 0 and summary["failed"] == 1
        # 4) portal-b expires mid-search; recovery re-logs in for CASE-4
        page_b.set_search_delay(2)
        page_b.expire_after_polls(1)
        assert store.upsert(adapter.query_case("portal-b", "CASE-4"))
        page_b.set_search_delay(0)
        # 5) portal-a page was reset after the stale page; CASE-3 now works
        page_a.freeze(False)
        assert store.upsert(adapter.query_case("portal-a", "CASE-3"))

        rows = _db_rows(db)
        # every saved row matches the page that produced it, incl. same-id rows
        for (platform, case_id), row in rows.items():
            assert (row["page_platform"], row["page_case_id"]) == (platform, case_id)
            assert row["source"] == "portal" and row["verified"] == 1
        assert set(rows) == {
            ("portal-a", "CASE-1"),
            ("portal-a", "CASE-3"),
            ("portal-b", "CASE-1"),
            ("portal-b", "CASE-2"),
            ("portal-b", "CASE-4"),
        }
        assert rows[("portal-a", "CASE-1")]["receipt_id"] == "A-1"
        assert rows[("portal-b", "CASE-1")]["receipt_id"] == "B-1"
        # the failed CASE-2 never replaced anything and is only in failures
        assert store.get_case("portal-a", "CASE-2") is None
        assert store.failures()[-1]["category"] == "stale"
    finally:
        store.close()
