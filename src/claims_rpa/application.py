from .browser import BrowserAdapter
from .pipeline import ClaimsPipeline
from .sources import CaseSource
from .storage import CaseStore


def run_fixture(
    fixture: str,
    database: str,
    dry_run: bool = True,
    portal=None,
    *,
    allow_relogin: bool = True,
    max_polls: int = 12,
) -> dict[str, int]:
    source = CaseSource()
    store = CaseStore(database)
    try:
        adapter = BrowserAdapter(
            dry_run=dry_run, portal=portal, allow_relogin=allow_relogin, max_polls=max_polls
        )
        return ClaimsPipeline(source.read(fixture), adapter.query_case, store).run(dry_run)
    finally:
        store.close()
