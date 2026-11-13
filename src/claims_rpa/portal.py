"""Controlled, read-only local portal pages used for offline verification.

The adapter only needs a tiny page port: log in, start a query, poll the
rendered view and (optionally) discard a pending search.  ``LocalPortalPage``
implements that port with a deterministic *logical* clock so delayed renders,
missing refreshes and session expiry can be reproduced without a browser.

The page deliberately exposes no submit/update/delete operations: portal
traffic in this project is read-only.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .errors import IdentityMismatch, LoginFailed, SessionExpired


@dataclass(frozen=True)
class PageView:
    """What the page currently displays, including its identity evidence."""

    platform: str
    case_id: str
    exists: bool
    status: str
    receipt_id: str
    request_token: str
    session_epoch: int
    structure_ok: bool = True


@dataclass
class _PendingRender:
    due_tick: int
    sequence: int
    view: PageView


class LocalPortalPage:
    """A scriptable portal page with an in-memory search result.

    A search enqueues a render; the render only becomes visible after enough
    ``poll()`` calls.  Until then the page keeps displaying its previous
    content, which models a browser that has not refreshed yet.
    """

    def __init__(
        self,
        platform: str,
        cases: Mapping[str, Mapping[str, Any] | None] | None = None,
        *,
        login_ok: bool = True,
        structure_ok: bool = True,
    ) -> None:
        self.platform = platform
        self._cases = dict(cases or {})
        self._login_ok = login_ok
        self._structure_ok = structure_ok
        self._authenticated = False
        self._session_epoch = 0
        self._tick = 0
        self._sequence = 0
        self._pending: list[_PendingRender] = []
        self._displayed: PageView | None = None
        self._frozen = False
        self._next_override: dict[str, Any] | None = None
        self._search_delay = 0
        self._expire_after_polls: int | None = None
        self._last_request_token = ""

    # -- session ---------------------------------------------------------
    @property
    def session_epoch(self) -> int:
        return self._session_epoch

    def is_authenticated(self) -> bool:
        return self._authenticated

    def login(self) -> int:
        """Open a new session.  Any render from the previous epoch is dropped."""
        if not self._login_ok:
            raise LoginFailed(f"login rejected by portal {self.platform!r}")
        self._authenticated = True
        self._session_epoch += 1
        self._pending.clear()
        return self._session_epoch

    def expire(self) -> None:
        """Invalidate the session while keeping the last rendered content."""
        self._authenticated = False

    def reset(self) -> None:
        """End the session and drop all page content, forcing a rebuild.

        Used when a page's evidence cannot be trusted for the current request;
        the next query logs in again and starts from a blank result area.
        """
        self._authenticated = False
        self._pending.clear()
        self._displayed = None
        self._next_override = None
        self._expire_after_polls = None

    def expire_after_polls(self, polls: int) -> None:
        """Expire the session while a query waits for its render.

        ``polls`` is the number of page polls the adapter may perform before
        the session dies (0 means the very next poll).
        """
        self._expire_after_polls = max(0, int(polls))

    def set_search_delay(self, delay: int) -> None:
        """Default number of polls before a searched render appears."""
        self._search_delay = max(0, int(delay))

    def set_login_ok(self, ok: bool) -> None:
        self._login_ok = ok

    # -- read-only page controls ----------------------------------------
    def set_structure_ok(self, ok: bool) -> None:
        self._structure_ok = ok

    def freeze(self, frozen: bool = True) -> None:
        """When frozen the page never refreshes, so searches stay pending."""
        self._frozen = frozen

    def mispresent_next(
        self,
        *,
        platform: str | None = None,
        case_id: str | None = None,
        structure_ok: bool | None = None,
    ) -> None:
        """Make the next promoted render carry wrong evidence.

        This models a portal page that renders another platform's/case's
        result (or a changed layout) while keeping the current search token.
        """
        override: dict[str, Any] = {}
        if platform is not None:
            override["platform"] = platform
        if case_id is not None:
            override["case_id"] = case_id
        if structure_ok is not None:
            override["structure_ok"] = structure_ok
        self._next_override = override

    # -- search ----------------------------------------------------------
    def search(self, case_id: str, request_token: str, *, delay: int | None = None) -> None:
        if not self._authenticated:
            raise SessionExpired(f"session for portal {self.platform!r} is expired")
        case = self._cases.get(case_id)
        exists = case is not None
        view = PageView(
            platform=self.platform,
            case_id=case_id,
            exists=exists,
            status=str(case.get("status", "")) if case else "",
            receipt_id=str(case.get("receipt_id", "")) if case else "",
            request_token=request_token,
            session_epoch=self._session_epoch,
            structure_ok=self._structure_ok,
        )
        self._sequence += 1
        self._last_request_token = request_token
        delay = self._search_delay if delay is None else delay
        self._pending.append(_PendingRender(self._tick + max(delay, 1), self._sequence, view))

    def discard_pending(self, request_token: str) -> None:
        self._pending = [p for p in self._pending if p.view.request_token != request_token]

    def poll(self) -> None:
        self._tick += 1
        if self._expire_after_polls is not None:
            if self._expire_after_polls <= 0:
                self._expire_after_polls = None
                self._authenticated = False
                self._pending.clear()
                return
            self._expire_after_polls -= 1
        if self._frozen:
            return
        ready = [p for p in self._pending if p.due_tick <= self._tick]
        if not ready:
            return
        self._pending = [p for p in self._pending if p.due_tick > self._tick]
        selected = ready[-1]
        view = selected.view
        if self._next_override is not None:
            view = replace(view, **self._next_override)
            self._next_override = None
        self._displayed = view

    def current_view(self) -> PageView | None:
        return self._displayed

    def deliver(self, view: PageView) -> None:
        """Force a render onto the page (used to reproduce late delivery).

        Intended for tests: an earlier request's view can arrive after a newer
        search has already started, which models a stale browser render.
        """
        self._displayed = view

    # -- diagnostics ------------------------------------------------------
    @property
    def pending_count(self) -> int:
        return len(self._pending)

    @property
    def last_request_token(self) -> str:
        """Token of the most recent search (for test-side page simulation)."""
        return self._last_request_token


class LocalPortal:
    """Two-or-more local platforms, each with its own independent session."""

    def __init__(self, pages: Iterable[LocalPortalPage]) -> None:
        self._pages: dict[str, LocalPortalPage] = {}
        for page in pages:
            if page.platform in self._pages:
                raise ValueError(f"duplicate local platform {page.platform!r}")
            self._pages[page.platform] = page

    def page_for(self, platform: str) -> LocalPortalPage:
        try:
            return self._pages[platform]
        except KeyError as exc:
            raise IdentityMismatch(f"no local portal configured for platform {platform!r}") from exc

    @classmethod
    def from_fixture(cls, path: str) -> LocalPortal:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        pages = data.get("pages") if isinstance(data, dict) else None
        if not isinstance(pages, dict):
            raise ValueError("local portal fixture must contain a 'pages' object")
        built: list[LocalPortalPage] = []
        for platform, spec in pages.items():
            if not isinstance(spec, dict):
                raise ValueError(f"page {platform!r} must be an object")
            built.append(
                LocalPortalPage(
                    str(platform),
                    spec.get("cases", {}),
                    login_ok=bool(spec.get("login_ok", True)),
                    structure_ok=bool(spec.get("structure_ok", True)),
                )
            )
        return cls(built)
