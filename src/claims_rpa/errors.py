"""Query failures that callers can distinguish without parsing messages.

Every exception carries a stable ``category`` which is persisted in the
``failures`` table.  ``QueryError`` intentionally subclasses ``RuntimeError``
so the pipeline's existing exception handling still catches it.
"""

from __future__ import annotations


class QueryError(RuntimeError):
    """Base class for portal query failures."""

    category = "browser"


class CaseNotFound(QueryError):
    """The portal rendered a valid result page but the case does not exist."""

    category = "not_found"


class LoginFailed(QueryError):
    """The portal could not be logged in, or re-login after expiry failed."""

    category = "auth"


class SessionExpired(QueryError):
    """The current portal session is no longer valid."""

    category = "auth"


class PageStructureChanged(QueryError):
    """The result page no longer exposes the fields the adapter expects."""

    category = "structure"


class IdentityMismatch(QueryError):
    """The page does not prove it belongs to the requested platform/case."""

    category = "identity"


class StaleResult(QueryError):
    """The rendered result belongs to an earlier search or session."""

    category = "stale"


class SearchTimeout(QueryError):
    """The page never rendered a result for this search (no refresh)."""

    category = "timeout"


class InvalidResult(QueryError):
    """The adapter returned a result that violates the query contract."""

    category = "validation"


class ProvenanceError(QueryError):
    """Simulated data was offered as a real portal confirmation."""

    category = "provenance"
