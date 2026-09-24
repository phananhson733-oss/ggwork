"""Errors of the RealShort feed client; every message is safe to show operators.

A message names the HTTP status, the fixed error word RealShort sent, the resource and the field path. It never
carries a token, the bypass secret, a response body or a field value (plan 5.5; the same rule as sync._safe_error).
The one exception is RowTooLargeError: plan 5.5 asks for the row's primary key, which is an identifier.

This module imports nothing from the extension, so sync.py can alias its FeedError to the one here later without an
import cycle (sync.py imports the client in P2-5c).
"""


class FeedError(Exception):
    """Base class; `status` is the HTTP status when a response caused it, `resource` the resource asked for."""

    def __init__(self, message: str, *, status: int | None = None, resource: str | None = None):
        super().__init__(message)
        self.status = status
        self.resource = resource


class ConfigError(FeedError):
    """Wrong or missing token, a bad base URL, or a redirect from deployment protection: retrying cannot help."""


class ContractError(FeedError):
    """A response that does not have the shape RealShort 816ca2e promises (envelope, manifest keys, cursor chain)."""


class DriftError(FeedError):
    """The source moved under the pull: 409 source_changed, source_busy after the manifest, or an echo that differs.
    The whole pull starts again with a new as_of (plan 5.2 step 3)."""


class AsOfExpiredError(FeedError):
    """as_of fell out of RealShort's 30-minute window (400 reason=as_of), or this client stopped at 25 minutes."""


class SourceReadError(FeedError):
    """503 read_failed twice in a row: RealShort could not read its database; not drift."""


class RowTooLargeError(FeedError):
    """500 row_too_large: one row is over 4 MB with its envelope; never retried (plan 5.5)."""

    def __init__(self, message: str, *, resource: str, key: tuple[str | int, ...] | None, status: int = 500):
        super().__init__(message, status=status, resource=resource)
        self.key = key


class BusyError(FeedError):
    """503 source_busy on the manifest: wait Retry-After seconds, pick a new as_of, ask again."""

    def __init__(self, message: str, *, retry_after: int, status: int = 503, resource: str | None = "manifest"):
        super().__init__(message, status=status, resource=resource)
        self.retry_after = retry_after


class BusyTimeout(FeedError):
    """The manifest stayed busy past the 1200-second budget (plan 5.1); `waited` is the seconds actually slept."""

    def __init__(self, message: str, *, waited: int, status: int = 503, resource: str | None = "manifest"):
        super().__init__(message, status=status, resource=resource)
        self.waited = waited


class FeedConnectionError(FeedError):
    """The request never got a response (connect, read or write failure); the message names the exception class only."""
