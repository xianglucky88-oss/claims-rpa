class BrowserAdapter:
    """外部浏览器端口。dry-run 使用确定性模拟；真实模式必须由实际适配器实现。"""

    def __init__(self, dry_run: bool = True) -> None:
        self.dry_run = dry_run

    def query_case(self, platform: str, case_id: str) -> dict[str, str]:
        if not self.dry_run:
            raise RuntimeError("real browser adapter is not configured; refusing simulated side effects")
        return {
            "platform": platform,
            "case_id": case_id,
            "status": "approved",
            "receipt_id": f"R-{case_id}",
        }
