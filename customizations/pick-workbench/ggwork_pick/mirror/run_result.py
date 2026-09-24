"""What a mirror run ends with (plan 5.2 step 12; P2-5c): the run record's columns and its details_json.

details_json reaches every signed-in user as it is (GET /api/pick/sync; U37), so only safe things go in: version ids,
times, counts, gate paths and row identifiers (U49), fixed reason codes, and error texts that name a class, an HTTP
status or a field path. safe_error is the only way an exception becomes text here.

The run record's status is only ever success or failed (frontend/src/core/pick/api.ts:58; the catch-up schedule counts
success): a paired publish, a degraded one and a v1 fallback are all success, and details_json.outcome and reason say
which (U11). Everything here is frozen: each step returns a new outcome with more details.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field, replace

from pydantic import ValidationError

from ggwork_pick.mirror.connection import MirrorConnectionError
from ggwork_pick.mirror.errors import FeedError
from ggwork_pick.mirror.feed_shape import Manifest
from ggwork_pick.mirror.gate_result import GateError
from ggwork_pick.mirror.publish import MirrorPublishError
from ggwork_pick.mirror.versions import MirrorBuildError, describe_error
from ggwork_pick.mirror.writer import MirrorRecordError
from ggwork_pick.sync import FeedError as V1FeedError
from ggwork_pick.sync import _safe_error as v1_safe_error

SUCCESS, FAILED = "success", "failed"
# details_json.outcome
PAIRED, DEGRADED, FALLBACK_V1 = "paired", "degraded", "fallback_v1"
NOT_PUBLISHED, LOCK_BUSY, CANCELLED, INTERNAL = "failed", "lock_busy", "cancelled", "error"
# control.last_failure codes (U11; publish.FAILURE_REASONS), besides degraded:<gate or error class>
CAPACITY, V1, DRIFT, BUSY = "capacity", "v1", "drift", "busy"
# Where a drift was seen (details_json.drift.stages)
AT_MANIFEST, AT_V1, AT_V2 = "manifest", "v1", "v2"
ERROR_MAX = 500
CANCELLED_ERROR = "同步被中止（进程停止或取消）"
LOCK_BUSY_ERROR = "镜像锁被占用（回填或清理进行中）"
# Exceptions whose text is written for operators already: status, class, fixed word, field path, never a value.
_SAFE_TEXT = (FeedError, V1FeedError, MirrorBuildError, MirrorRecordError, MirrorPublishError, MirrorConnectionError, GateError)


def safe_error(exc: BaseException) -> str:
    """exc as text every signed-in user may read: its own text when it is written that way, else its class and SQLSTATE."""
    if isinstance(exc, ValidationError):
        return v1_safe_error(exc)
    if isinstance(exc, _SAFE_TEXT) or type(exc) is ValueError:  # a bare ValueError: this extension's fixed texts
        return str(exc)[:ERROR_MAX]
    return describe_error(exc)


def ms(seconds: float) -> int:
    return round(seconds * 1000)


def warning_codes(manifest: Manifest) -> list[str]:
    """manifest.meta.warnings as their codes only (P2-8b shows them as they are)."""
    warnings = manifest.meta.get("warnings")
    return [item["code"] for item in warnings if isinstance(item, Mapping) and isinstance(item.get("code"), str)] if isinstance(warnings, list) else []


@dataclass(frozen=True)
class Retry:
    """This attempt drifted and another may follow (plan 5.2 step 3): where the drift was seen."""

    stage: str


@dataclass(frozen=True)
class RunOutcome:
    """How one run ended: the run record's columns, whether and how the failure was counted, and details_json."""

    status: str
    outcome: str
    reason: str | None = None
    error: str | None = None
    rows: int | None = None
    catalog_batch_id: str | None = None
    knowledge_batch_id: str | None = None
    source_as_of: str | None = None
    consecutive_failures: int | None = None
    drift_stage: str | None = None
    manifest: Manifest | None = field(default=None, repr=False, compare=False)
    details: Mapping = field(default_factory=dict)

    def with_details(self, **parts) -> "RunOutcome":
        return replace(self, details={**self.details, **parts})

    def merged(self, section: str, **values) -> "RunOutcome":
        """details[section] with values added (stages, gates, scrub_hits), as a new outcome."""
        return self.with_details(**{section: {**self.details.get(section, {}), **values}})

    def with_stages(self, **millis: int) -> "RunOutcome":
        return self.merged("stages", **millis)

    def finish_values(self, *, alert_after: int) -> dict:
        """PickRepository.finish_sync_run's keywords: the columns, then details_json with outcome, reason and alert."""
        alert = self.consecutive_failures is not None and self.consecutive_failures >= alert_after
        closing = {"outcome": self.outcome, "reason": self.reason, "consecutive_failures": self.consecutive_failures, "alert": alert}
        values = {"status": self.status, "details_json": {"mode": "mirror", **self.details, **closing}}
        if self.error is not None:
            values = {**values, "error": self.error[:ERROR_MAX]}
        if self.status != SUCCESS:
            return values
        published = {
            "rows": self.rows,
            "catalog_batch_id": self.catalog_batch_id,
            "knowledge_batch_id": self.knowledge_batch_id,
            "source_as_of": self.source_as_of,
        }
        return {**values, **published}


def failed(outcome: str, *, reason: str | None = None, error: str | None = None, count: int | None = None, **details) -> RunOutcome:
    return RunOutcome(FAILED, outcome, reason=reason, error=error, consecutive_failures=count, details=details)


def internal_error_values(exc: BaseException) -> dict:
    """finish_sync_run's keywords for a run that ended on something unexpected (a bug): recorded, then raised."""
    return {"status": FAILED, "error": f"同步内部错误：{type(exc).__name__}", "details_json": {"mode": "mirror", "outcome": INTERNAL}}


def cancelled_values() -> dict:
    """finish_sync_run's keywords for a cancelled run: failed, never counted (U11)."""
    return {"status": FAILED, "error": CANCELLED_ERROR, "details_json": {"mode": "mirror", "outcome": CANCELLED}}
