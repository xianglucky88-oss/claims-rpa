from collections.abc import Callable, Iterable
from typing import Any

from .contract import validate_query_result
from .errors import QueryError
from .storage import CaseStore

_VALIDATION_ERRORS = (KeyError, TypeError, ValueError)
_QUERY_ERRORS = (QueryError, RuntimeError, OSError)


class ClaimsPipeline:
    """只编排案件校验、浏览器适配器和结果存储；单条失败不会中断批次。

    The adapter's raw result is always passed through the query contract
    before ``store.upsert``.  A result whose platform/case evidence belongs to
    another request is rejected, so an error page can never overwrite a
    previously saved correct case.
    """

    def __init__(
        self,
        cases: Iterable[Any],
        query: Callable[[str, str], dict[str, Any]],
        store: CaseStore,
    ):
        self.cases = cases
        self.query = query
        self.store = store

    def run(self, dry_run: bool = True) -> dict[str, int]:
        processed = failed = changed = 0
        for item in self.cases:
            processed += 1
            platform = case_id = ""
            try:
                platform = str(item["platform"]).strip()
                case_id = str(item["case_id"]).strip()
                if not platform or not case_id:
                    raise ValueError("platform and case_id are required")
                raw = self.query(platform, case_id)
                result = validate_query_result(raw, platform, case_id, dry_run=dry_run)
                result["requested_platform"] = platform
                result["requested_case_id"] = case_id
                changed += int(self.store.upsert(result))
            except (KeyError, TypeError, ValueError, RuntimeError, OSError) as exc:
                failed += 1
                category = self._category(exc)
                if isinstance(item, dict):
                    platform = platform or str(item.get("platform", ""))
                    case_id = case_id or str(item.get("case_id", ""))
                self.store.record_failure(
                    platform,
                    case_id,
                    str(exc),
                    category,
                    request_token=str(getattr(exc, "request_token", "")),
                    session_epoch=int(getattr(exc, "session_epoch", 0)),
                    page_platform=str(getattr(exc, "page_platform", "")),
                    page_case_id=str(getattr(exc, "page_case_id", "")),
                )
        return {
            "processed": processed,
            "changed": changed,
            "failed": failed,
            "cases": self.store.case_count(),
            "dry_run": int(dry_run),
        }

    @staticmethod
    def _category(exc: Exception) -> str:
        if isinstance(exc, QueryError):
            return exc.category
        if isinstance(exc, _VALIDATION_ERRORS):
            return "validation"
        return "browser"
