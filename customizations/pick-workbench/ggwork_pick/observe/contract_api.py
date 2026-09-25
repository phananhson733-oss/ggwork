"""The observation radar's gateway bodies (plan TR-33): POST /api/pick/obs/decisions and /api/pick/sync's obs key.

Part of the shared data contract; see contract.py for the rules the four modules follow and the document they match.
"""

from typing import Annotated, Literal, Union

from pydantic import Field, StrictBool, StrictInt, model_validator

from ggwork_pick.contracts import StrictInput
from ggwork_pick.observe.contract import (
    CHANNELS,
    MAX_ROW_ID,
    AmbiguityVerdict,
    BannerLevel,
    Channel,
    DecisionKind,
    Frozen,
    Identity,
    Mode,
    SetId,
    ShortText,
    Stamp,
    StatusCode,
    Text,
    TrendGeo,
    refuse,
)

# ---- decisions (D12, D24): POST /api/pick/obs/decisions ----------------------------------------------------------
DecisionId = Annotated[StrictInt, Field(ge=1, le=MAX_ROW_ID)]


class _DecisionBase(StrictInput):
    kind: DecisionKind
    request_id: str = Field(min_length=1, max_length=128)
    note: str = Field(default="", max_length=500)


class AliasConfirm(_DecisionBase):
    kind: Literal["alias_confirm"]
    alias_id: DecisionId


class AliasReject(_DecisionBase):
    kind: Literal["alias_reject"]
    alias_id: DecisionId


class AliasPair(_DecisionBase):
    kind: Literal["alias_pair"]
    old_identity: Identity
    new_identity: Identity

    @model_validator(mode="after")
    def _two(self):
        refuse("obs_alias_pair", None if self.old_identity != self.new_identity else "配对的是两个不同的身份")
        return self


class CorrespondenceConfirm(_DecisionBase):
    kind: Literal["correspondence_confirm"]
    identity: Identity
    platform: ShortText
    normalized_title: Text


class CorrespondenceRevoke(_DecisionBase):
    kind: Literal["correspondence_revoke"]
    identity: Identity


class WatchAdd(_DecisionBase):
    kind: Literal["watch_add"]
    identity: Identity
    geo: TrendGeo
    active: StrictBool


class WatchPause(_DecisionBase):
    kind: Literal["watch_pause"]
    identity: Identity
    geo: TrendGeo | None
    paused: StrictBool


class AmbiguityOverride(_DecisionBase):
    kind: Literal["ambiguity_override"]
    identity: Identity
    verdict: AmbiguityVerdict


class AlertIrrelevant(_DecisionBase):
    kind: Literal["alert_irrelevant"]
    alert_id: DecisionId


DECISION_MODELS = (
    AliasConfirm, AliasReject, AliasPair, CorrespondenceConfirm, CorrespondenceRevoke, WatchAdd, WatchPause, AmbiguityOverride, AlertIrrelevant,
)  # fmt: skip
Decision = Annotated[Union[DECISION_MODELS], Field(discriminator="kind")]  # noqa: UP007

# ---- /api/pick/sync's obs key (D10, TR-25) -------------------------------------------------------------------------


class ObsBanner(Frozen):
    code: StatusCode
    level: BannerLevel


class ObsChannelStatus(Frozen):
    channel: Channel
    live_set_id: SetId | None
    live_published_at: Stamp | None
    latest_set_id: SetId | None
    latest_published_at: Stamp | None
    latest_mode: Mode | None
    last_run_at: Stamp | None
    banners: list[ObsBanner]


class ObsSyncStatus(Frozen):
    checked_at: Stamp
    channels: list[ObsChannelStatus]

    @model_validator(mode="after")
    def _both(self):
        refuse("obs_sync", None if tuple(c.channel for c in self.channels) == CHANNELS else "channels 依次是 trends、gsc")
        return self


class ObsSyncError(Frozen):
    """The gateway could not read the status; it names the exception class only."""

    error: Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_.]{0,99}$")]


SyncObs = Union[ObsSyncStatus, ObsSyncError]  # noqa: UP007
