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
