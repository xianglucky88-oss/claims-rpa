from collections.abc import Callable, Iterable
from typing import Any
from .storage import CaseStore


class ClaimsPipeline:
    """只编排案件校验、浏览器适配器和结果存储；单条失败不会中断批次。"""

    def __init__(self, cases: Iterable[Any], query: Callable[[str, str], dict[str, str]], store: CaseStore):
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
                changed += int(self.store.upsert(self.query(platform, case_id)))
            except (KeyError, TypeError, ValueError, RuntimeError, OSError) as exc:
                failed += 1
                category = "validation" if isinstance(exc, (KeyError, TypeError, ValueError)) else "browser"
                if isinstance(item, dict):
                    platform = platform or str(item.get("platform", ""))
                    case_id = case_id or str(item.get("case_id", ""))
                self.store.record_failure(platform, case_id, str(exc), category)
        return {"processed": processed, "changed": changed, "failed": failed, "cases": self.store.case_count(), "dry_run": int(dry_run)}
