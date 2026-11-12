import json
from pathlib import Path
from typing import Any


class CaseSource:
    """只负责读取案件队列。"""

    def read(self, path: str) -> list[Any]:
        cases = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(cases, list):
            raise ValueError("case fixture must contain a JSON list")
        return cases
