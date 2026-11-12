from pathlib import Path
from claims_rpa.application import run_fixture
from claims_rpa.browser import BrowserAdapter
from claims_rpa.sources import CaseSource
from claims_rpa.storage import CaseStore


def test_source_and_browser_have_separate_responsibilities():
    cases = CaseSource().read(str(Path(__file__).parents[1] / "fixtures/cases.json"))
    assert len(cases) == 3
    assert BrowserAdapter().query_case("portal-a", "CASE-1")["status"] == "approved"


def test_store_upsert_is_idempotent(tmp_path):
    store = CaseStore(str(tmp_path / "cases.sqlite3"))
    try:
        result = BrowserAdapter().query_case("portal-a", "CASE-1")
        assert store.upsert(result) is True
        assert store.upsert(result) is False
        assert store.case_count() == 1
    finally:
        store.close()


def test_pipeline_handles_failures_and_repeat_runs(tmp_path):
    fixture = str(Path(__file__).parents[1] / "fixtures/cases.json")
    db = str(tmp_path / "pipeline.sqlite3")
    first = run_fixture(fixture, db, True)
    second = run_fixture(fixture, db, True)
    assert first == {"processed": 3, "changed": 2, "failed": 1, "cases": 2, "dry_run": 1}
    assert second["changed"] == 0
