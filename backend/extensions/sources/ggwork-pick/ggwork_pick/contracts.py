"""Typed business input, separate from prose and runtime authority."""

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)


class SignalInput(StrictInput):
    kind: str = Field(min_length=1, max_length=100)
    source_ref: str = Field(min_length=1, max_length=2048)
    observed_at: date | datetime | None = None
    value: Annotated[str, Field(max_length=4000)] | StrictInt | StrictFloat | None = None
    label: str = Field(default="", max_length=200)
    rank: StrictInt | None = Field(default=None, ge=0, le=1_000_000)
    grade: str = Field(default="", max_length=100)
    note: str = Field(default="", max_length=1000)


class PostedInput(StrictInput):
    """Operator publication records matched by the source. matched=false is "no matching record", not "never posted"."""

    matched: bool
    records: list[Annotated[str, Field(min_length=1, max_length=64)]] = Field(default_factory=list, max_length=50)
    post_count: StrictInt = Field(ge=0, le=1_000_000)
    sched_count: StrictInt = Field(default=0, ge=0, le=1_000_000)
    last_post_on: date | None = None
    accounts: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(default_factory=list, max_length=100)


class DramaInput(StrictInput):
    source: str = Field(min_length=1, max_length=100)
    source_id: str = Field(min_length=1, max_length=256)
    language: str = Field(min_length=1, max_length=40)
    title: str = Field(min_length=1, max_length=500)
    theater: str = Field(default="", max_length=100)
    tags: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(default_factory=list, max_length=20)
    listed_at: date | None = None
    availability: Literal["active", "delisted", "unknown"] = "unknown"
    signals: list[SignalInput] = Field(default_factory=list, max_length=50)
    channel_rules: dict[Literal["youtube", "tiktok", "facebook"], Literal["allowed", "denied", "unknown"]] = Field(default_factory=dict)
    detail_url: str | None = Field(default=None, max_length=2048)
    posted: PostedInput | None = None


class PickConditions(StrictInput):
    theater: str | None = Field(default=None, max_length=100)
    language: str | None = Field(default=None, max_length=40)
    channel: Literal["youtube", "tiktok", "facebook"] | None = None
    query: str | None = Field(default=None, max_length=200)
    tags: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(default_factory=list, max_length=20)
    limit: int = Field(default=5, ge=1, le=20, strict=True)
    exclude_selected: bool = Field(default=True, description="排除个人清单已保存剧目。用户说没选过/排除已选时使用此参数。")
    confirmed_eligible_only: bool = True
    exclude_previous: bool = Field(default=False, description="仅用户明确说换一批且已经绑定旧候选时为true。首次查询和排除已选必须为false。")
    signal_kind: str | None = Field(default=None, max_length=20, description="只要带这类来源信号的剧，如kd=KalosTV日榜；种类见知识资料「信号种类」。")
    sort: Literal["evidence_date", "rank"] = Field(
        default="evidence_date", description="rank=按signal_kind那一类榜单名次升序，必须同时给signal_kind；默认按最近依据日期。"
    )
    exclude_posted: bool = Field(default=False, description="排除团队发布记录里已发过（post_count>0）的剧。用户说账号/团队没发过时使用。")
    posted_account: str | None = Field(default=None, max_length=200, description="排除发布记录里该账号发过的剧，账号名原样传入。")
