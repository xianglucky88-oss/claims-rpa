from .browser import BrowserAdapter
from .pipeline import ClaimsPipeline
from .sources import CaseSource
from .storage import CaseStore


def run_fixture(fixture: str, database: str, dry_run: bool = True) -> dict[str, int]:
    source = CaseSource()
    store = CaseStore(database)
    try:
        adapter = BrowserAdapter(dry_run=dry_run)
        return ClaimsPipeline(source.read(fixture), adapter.query_case, store).run(dry_run)
    finally:
        store.close()
