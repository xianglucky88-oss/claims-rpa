import sqlite3
from typing import Any

from .errors import IdentityMismatch, ProvenanceError

_CASE_COLUMNS = {
    "source": "TEXT NOT NULL DEFAULT 'simulated'",
    "verified": "INTEGER NOT NULL DEFAULT 0",
    "request_token": "TEXT NOT NULL DEFAULT ''",
    "page_platform": "TEXT NOT NULL DEFAULT ''",
    "page_case_id": "TEXT NOT NULL DEFAULT ''",
    "session_epoch": "INTEGER NOT NULL DEFAULT 0",
    "requested_platform": "TEXT NOT NULL DEFAULT ''",
    "requested_case_id": "TEXT NOT NULL DEFAULT ''",
}
_FAILURE_COLUMNS = {
    "request_token": "TEXT NOT NULL DEFAULT ''",
    "session_epoch": "INTEGER NOT NULL DEFAULT 0",
    "page_platform": "TEXT NOT NULL DEFAULT ''",
    "page_case_id": "TEXT NOT NULL DEFAULT ''",
}


class CaseStore:
    """只负责案件结果、失败分类和可重试审计记录的持久化。

    Every accepted row keeps the identity evidence captured for the request
    that produced it (request token, page platform/case, session epoch) so a
    database change can always be traced back to the page it came from.
    """

    def __init__(self, database: str):
        self.conn = sqlite3.connect(database)
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS cases(
                platform TEXT NOT NULL, case_id TEXT NOT NULL, status TEXT NOT NULL,
                receipt_id TEXT NOT NULL, PRIMARY KEY(platform, case_id)
            );
            CREATE TABLE IF NOT EXISTS failures(
                id INTEGER PRIMARY KEY AUTOINCREMENT, platform TEXT NOT NULL,
                case_id TEXT NOT NULL, category TEXT NOT NULL, error TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)
        self._add_columns("cases", _CASE_COLUMNS)
        self._add_columns("failures", _FAILURE_COLUMNS)
        self.conn.commit()

    def _add_columns(self, table: str, columns: dict[str, str]) -> None:
        existing = {
            row[1] for row in self.conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        for name, ddl in columns.items():
            if name not in existing:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")

    def upsert(self, result: dict[str, Any]) -> bool:
        self._guard(result)
        before = self.conn.total_changes
        self.conn.execute("""
            INSERT INTO cases(
                platform, case_id, status, receipt_id, source, verified,
                request_token, page_platform, page_case_id, session_epoch,
                requested_platform, requested_case_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(platform, case_id) DO UPDATE SET
                status=excluded.status, receipt_id=excluded.receipt_id,
                source=excluded.source, verified=excluded.verified,
                request_token=excluded.request_token,
                page_platform=excluded.page_platform, page_case_id=excluded.page_case_id,
                session_epoch=excluded.session_epoch,
                requested_platform=excluded.requested_platform,
                requested_case_id=excluded.requested_case_id
            WHERE cases.status <> excluded.status
               OR cases.receipt_id <> excluded.receipt_id
               OR cases.source <> excluded.source
               OR cases.verified <> excluded.verified
        """, (
            result["platform"],
            result["case_id"],
            result["status"],
            result["receipt_id"],
            result.get("source", "simulated"),
            int(bool(result.get("verified", False))),
            result.get("request_token", ""),
            result.get("page_platform", ""),
            result.get("page_case_id", ""),
            int(result.get("session_epoch", 0)),
            result.get("requested_platform", result["platform"]),
            result.get("requested_case_id", result["case_id"]),
        ))
        self.conn.commit()
        return self.conn.total_changes > before

    @staticmethod
    def _guard(result: dict[str, Any]) -> None:
        """Refuse rows that are not bound to the platform/case they claim.

        This is the last line of defence for direct callers of ``upsert``: an
        error page must never overwrite a correct saved case, and simulated
        data must never be stored as a verified portal confirmation.
        """
        platform, case_id = result["platform"], result["case_id"]
        page_platform = result.get("page_platform", platform) or platform
        page_case_id = result.get("page_case_id", case_id) or case_id
        if (page_platform, page_case_id) != (platform, case_id):
            raise IdentityMismatch(
                f"refusing to save {platform}/{case_id}: page evidence is "
                f"{page_platform}/{page_case_id}"
            )
        if bool(result.get("verified", False)) and result.get("source", "simulated") != "portal":
            raise ProvenanceError(
                "refusing to save simulated data as a verified portal confirmation"
            )
        if result.get("source", "simulated") == "portal":
            if result.get("request_token", "") == "" or not result.get("page_platform", ""):
                raise IdentityMismatch(
                    "a verified portal row must carry request token and page evidence"
                )

    def record_failure(
        self,
        platform: str,
        case_id: str,
        error: str,
        category: str = "validation",
        *,
        request_token: str = "",
        session_epoch: int = 0,
        page_platform: str = "",
        page_case_id: str = "",
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO failures(
                platform, case_id, category, error,
                request_token, session_epoch, page_platform, page_case_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                platform,
                case_id,
                category,
                error,
                request_token,
                int(session_epoch),
                page_platform,
                page_case_id,
            ),
        )
        self.conn.commit()

    def case_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0]

    def get_case(self, platform: str, case_id: str) -> dict[str, Any] | None:
        cursor = self.conn.execute(
            "SELECT platform, case_id, status, receipt_id, source, verified, "
            "request_token, page_platform, page_case_id, session_epoch "
            "FROM cases WHERE platform=? AND case_id=?",
            (platform, case_id),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        keys = (
            "platform",
            "case_id",
            "status",
            "receipt_id",
            "source",
            "verified",
            "request_token",
            "page_platform",
            "page_case_id",
            "session_epoch",
        )
        return dict(zip(keys, row))

    def failures(self) -> list[dict[str, Any]]:
        cursor = self.conn.execute(
            "SELECT platform, case_id, category, error, request_token, "
            "session_epoch, page_platform, page_case_id FROM failures ORDER BY id"
        )
        keys = (
            "platform",
            "case_id",
            "category",
            "error",
            "request_token",
            "session_epoch",
            "page_platform",
            "page_case_id",
        )
        return [dict(zip(keys, row)) for row in cursor.fetchall()]

    def close(self) -> None:
        self.conn.close()
