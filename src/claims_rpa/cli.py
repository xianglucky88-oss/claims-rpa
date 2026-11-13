import argparse
import json

from .application import run_fixture
from .portal import LocalPortal


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--fixture", required=True)
    p.add_argument("--database", required=True)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument(
        "--portal-fixture",
        help="JSON file with controlled local platforms; queries become read-only portal reads",
    )
    p.add_argument("--no-relogin", action="store_true", help="do not retry after session expiry")
    p.add_argument("--max-polls", type=int, default=12, help="polls before a missing refresh fails")
    a = p.parse_args()
    portal = LocalPortal.from_fixture(a.portal_fixture) if a.portal_fixture else None
    print(
        json.dumps(
            run_fixture(
                a.fixture,
                a.database,
                a.dry_run,
                portal,
                allow_relogin=not a.no_relogin,
                max_polls=a.max_polls,
            ),
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
