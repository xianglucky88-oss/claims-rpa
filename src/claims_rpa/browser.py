"""Injectable, read-only portal query adapter.

``BrowserAdapter`` keeps the historical dry-run behaviour (deterministic
simulated data) but can be given a controlled local portal.  In portal mode
it performs only read operations: login, search, poll and read the rendered
view.  Every query gets a fresh request token and records the page identity
proof (platform, case id, session epoch) alongside the result.

Session scope: a platform page owns one session which is reused by consecutive
queries on that platform.  If the session expires, one re-login is allowed;
the new epoch invalidates every render from the old one, a new token is issued
and the query is retried.  A failed or non-retryable query discards its pending
render so the next case starts with a clean context.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from .errors import (
    CaseNotFound,
    IdentityMismatch,
    InvalidResult,
    LoginFailed,
    PageStructureChanged,
    QueryError,
    SearchTimeout,
    SessionExpired,
    StaleResult,
)
from .portal import PageView


class BrowserAdapter:
    """外部浏览器端口。

    ``dry_run`` only controls what happens **without** an injected portal:
    deterministic simulated data.  When ``portal`` is provided it is always
    queried (read-only), regardless of ``dry_run``; the pipeline separately
    enforces that simulated rows can only be written in dry-run mode.
    """

    def __init__(
        self,
        dry_run: bool = True,
        portal: Any | None = None,
        *,
        allow_relogin: bool = True,
        max_polls: int = 12,
    ) -> None:
        self.dry_run = dry_run
        self.portal = portal
        self.allow_relogin = allow_relogin
        self.max_polls = max(1, int(max_polls))

    # -- public port ------------------------------------------------------
    def query_case(self, platform: str, case_id: str) -> dict[str, Any]:
        platform = str(platform).strip()
        case_id = str(case_id).strip()
        if not platform or not case_id:
            raise InvalidResult("platform and case_id are required")
        if self.portal is None:
            if not self.dry_run:
                raise RuntimeError(
                    "real browser adapter is not configured; refusing simulated side effects"
                )
            return self._simulated(platform, case_id)
        return self._query_portal(platform, case_id)

    # -- simulated dry-run -------------------------------------------------
    def _simulated(self, platform: str, case_id: str) -> dict[str, Any]:
        return {
            "platform": platform,
            "case_id": case_id,
            "status": "approved",
            "receipt_id": f"R-{case_id}",
            "source": "simulated",
            "verified": False,
            "request_token": f"sim:{platform}:{case_id}",
            "page_platform": platform,
            "page_case_id": case_id,
            "session_epoch": 0,
        }

    # -- local portal ------------------------------------------------------
    def _query_portal(self, platform: str, case_id: str) -> dict[str, Any]:
        page = self.portal.page_for(platform)
        attempts = 2 if self.allow_relogin else 1
        epoch = 0
        token = self._new_token(platform, case_id)
        for range_attempt in range(attempts):
            try:
                epoch = self._ensure_login(page)
                page.search(case_id, token)
                view = self._await_result(page, token, epoch, platform, case_id)
                return self._portal_result(view, platform, case_id)
            except SessionExpired as exc:
                self._attach(exc, token, epoch, platform, case_id)
                page.discard_pending(token)
                if range_attempt + 1 >= attempts:
                    raise
                try:
                    page.login()
                except LoginFailed as login_exc:
                    self._attach(login_exc, token, epoch, platform, case_id)
                    raise login_exc from exc
                token = self._new_token(platform, case_id)
            except QueryError as exc:
                self._attach(exc, token, epoch, platform, case_id)
                page.discard_pending(token)
                if isinstance(exc, (IdentityMismatch, StaleResult, PageStructureChanged)):
                    # The page cannot be trusted for the current request; start
                    # the next case from a clean, freshly logged-in context.
                    if hasattr(page, "reset"):
                        page.reset()
                raise
        raise SearchTimeout("portal query could not be completed")  # pragma: no cover

    def _ensure_login(self, page: Any) -> int:
        if page.is_authenticated():
            return int(page.session_epoch)
        page.login()
        return int(page.session_epoch)

    def _await_result(
        self, page: Any, token: str, epoch: int, platform: str, case_id: str
    ) -> PageView:
        def page_error(
            cls: type[QueryError],
            message: str,
            view: PageView | None = None,
            *,
            evidence_epoch: int | None = None,
        ) -> QueryError:
            exc = cls(message)
            exc.request_token = token
            exc.platform = platform
            exc.case_id = case_id
            exc.session_epoch = epoch if evidence_epoch is None else evidence_epoch
            exc.page_platform = view.platform if view is not None else ""
            exc.page_case_id = view.case_id if view is not None else ""
            return exc

        for _ in range(self.max_polls):
            if not page.is_authenticated():
                raise page_error(
                    SessionExpired, f"session for portal {platform!r} expired during query"
                )
            view = page.current_view()
            if view is not None and view.request_token == token:
                if view.session_epoch != epoch:
                    raise page_error(
                        SessionExpired,
                        "rendered result belongs to an earlier session epoch",
                        view,
                        evidence_epoch=view.session_epoch,
                    )
                if not view.structure_ok:
                    raise page_error(
                        PageStructureChanged,
                        f"portal {platform!r} result page structure changed",
                        view,
                    )
                if view.platform != platform:
                    raise page_error(
                        IdentityMismatch,
                        f"page belongs to platform {view.platform!r}, requested {platform!r}",
                        view,
                    )
                if view.case_id != case_id:
                    raise page_error(
                        IdentityMismatch,
                        f"page shows case {view.case_id!r}, requested {case_id!r}",
                        view,
                    )
                if not view.exists:
                    raise page_error(
                        CaseNotFound,
                        f"case {case_id!r} not found on portal {platform!r}",
                        view,
                    )
                if not view.status:
                    raise page_error(
                        PageStructureChanged,
                        f"status field missing on portal {platform!r} result page",
                        view,
                    )
                return view
            page.poll()
        displayed = page.current_view()
        if displayed is not None and displayed.request_token != token:
            raise page_error(
                StaleResult,
                f"portal {platform!r} only shows an earlier search's result; "
                f"the page did not refresh for {case_id!r}",
                displayed,
            )
        raise page_error(
            SearchTimeout,
            f"portal {platform!r} did not render a fresh result for {case_id!r}; "
            "the page may not have refreshed",
            displayed,
        )

    @staticmethod
    def _portal_result(view: PageView, platform: str, case_id: str) -> dict[str, Any]:
        return {
            "platform": platform,
            "case_id": case_id,
            "status": view.status,
            "receipt_id": view.receipt_id,
            "source": "portal",
            "verified": True,
            "request_token": view.request_token,
            "page_platform": view.platform,
            "page_case_id": view.case_id,
            "session_epoch": view.session_epoch,
        }

    @staticmethod
    def _new_token(platform: str, case_id: str) -> str:
        return f"{platform}:{case_id}:{uuid4().hex}"

    @staticmethod
    def _attach(exc: QueryError, token: str, epoch: int, platform: str, case_id: str) -> None:
        # ``_await_result`` may already have attached the evidence it actually
        # saw (possibly the wrong page); never overwrite that with the request.
        exc.request_token = getattr(exc, "request_token", None) or token
        if getattr(exc, "session_epoch", None) is None:
            exc.session_epoch = int(epoch)
        exc.platform = getattr(exc, "platform", None) or platform
        exc.case_id = getattr(exc, "case_id", None) or case_id
        # page identity evidence stays empty when no page was actually read.
