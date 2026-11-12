import sqlite3


class CaseStore:
    """只负责案件结果、失败分类和可重试审计记录的持久化。"""

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
        self.conn.commit()

    def upsert(self, result: dict[str, str]) -> bool:
        before = self.conn.total_changes
        self.conn.execute("""
            INSERT INTO cases(platform, case_id, status, receipt_id) VALUES (?, ?, ?, ?)
            ON CONFLICT(platform, case_id) DO UPDATE SET status=excluded.status, receipt_id=excluded.receipt_id
            WHERE cases.status <> excluded.status OR cases.receipt_id <> excluded.receipt_id
        """, (result["platform"], result["case_id"], result["status"], result["receipt_id"]))
        self.conn.commit()
        return self.conn.total_changes > before

    def record_failure(self, platform: str, case_id: str, error: str, category: str = "validation") -> None:
        self.conn.execute(
            "INSERT INTO failures(platform, case_id, category, error) VALUES (?, ?, ?, ?)",
            (platform, case_id, category, error),
        )
        self.conn.commit()

    def case_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0]

    def close(self) -> None:
        self.conn.close()
