import json


def execute(platform: str, case_id: str) -> dict:
    # Keep browser actions deterministic and credential-free for local verification.
    return {"steps": ["open_login_page", "query_case", "download_receipt"], "platform": platform, "case_id": case_id, "status": "approved"}


if __name__ == "__main__":
    print(json.dumps(execute("fixture-platform", "CASE-1")))
