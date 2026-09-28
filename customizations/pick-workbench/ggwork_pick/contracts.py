"""Typed business input, separate from prose and runtime authority."""

import json
import re
from datetime import date, datetime
from typing import Annotated, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, model_serializer, model_validator
from pydantic_core import PydanticCustomError

# NUL and lone surrogates. PostgreSQL text refuses NUL; asyncpg, psycopg and sqlite3 encode strictly and cannot
# send a lone surrogate, which is what Python's JSON decoder makes of an unpaired "\ud800" escape.
UNSTORABLE_TEXT = re.compile("[\x00\ud800-\udfff]")
REPLACEMENT_CHARACTER = "\ufffd"
# ggwp_drama_versions.identity and ggwp_selections.identity are String(512).
IDENTITY_MAX_LENGTH = 512


def _unstorable_at(value) -> list | None:
    """The key path to the first string holding unstorable text, or None. Dict keys are strings too."""
    if isinstance(value, str):
        return [] if UNSTORABLE_TEXT.search(value) else None
    if isinstance(value, dict):
        pairs = value.items()
    elif isinstance(value, list | tuple):
        pairs = enumerate(value)
    else:
        return None
    for key, item in pairs:
        if isinstance(key, str) and UNSTORABLE_TEXT.search(key):
            return [key]
        found = _unstorable_at(item)
        if found is not None:
            return [key, *found]
    return None


def unstorable_path(value) -> str | None:
    """Where value holds NUL or a lone surrogate, printable in an error: the offending text itself is never echoed."""
    found = _unstorable_at(value)
    if found is None:
        return None
    return ".".join(UNSTORABLE_TEXT.sub("?", str(key)) for key in found) or "value"


def storable(value):
    """A copy of value with NUL and lone surrogates replaced by U+FFFD, dict keys included.

    For text that reaches a table without passing StrictInput: feed metadata, ids from the model provider, notes
    quoting the model's answer. Refusing those would lose a whole sync or an answer's notes over one character.
    """
    if isinstance(value, str):
        return UNSTORABLE_TEXT.sub(REPLACEMENT_CHARACTER, value)
    if isinstance(value, dict):
        return {storable(key): storable(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return type(value)(storable(item) for item in value)
    return value


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)

    @model_validator(mode="before")
    @classmethod
    def _storable_text(cls, data):
        # SQLite stores both characters; PostgreSQL would fail the write with a 500. Refuse them as input instead.
        where = unstorable_path(data)
        if where is not None:
            raise PydanticCustomError("unstorable_text", "{where} 含 NUL 字符或孤立代理项，无法保存", {"where": where})
        return data


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

    @property
    def identity(self) -> str:
        return json.dumps([self.source, self.source_id, self.language], ensure_ascii=False, separators=(",", ":"))

    @model_validator(mode="after")
    def _identity_fits(self):
        # Escaping can push an identity past the column (a quote in source_id counts twice); plain values stay under 410.
        if len(self.identity) > IDENTITY_MAX_LENGTH:
            raise PydanticCustomError("identity_too_long", "identity（来源、来源ID、语种）超过 {limit} 个字符", {"limit": IDENTITY_MAX_LENGTH})
        return self


class PickConditions(StrictInput):
    theater: str | None = Field(
        default=None, max_length=100, description="剧场名，如ReelShort、ShortMax、KalosTV；不是地区或国家。剧库里没有的剧场会被拒绝并列出可选剧场。"
    )
    language: str | None = Field(
        default=None,
        max_length=40,
        description="语种代码，如en、ko、ja。剧库没有地区字段：用户说美国/US/北美等地区时按语种近似（美国=en），回答里说明是按语种近似。",
    )
    channel: Literal["youtube", "tiktok", "facebook"] | None = None
    query: str | None = Field(default=None, max_length=200)
    tags: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(default_factory=list, max_length=20)
    limit: int = Field(default=5, ge=1, le=20, strict=True)
    exclude_selected: bool = Field(default=True, description="排除个人清单已保存剧目。用户说没选过/排除已选时使用此参数。")
    confirmed_eligible_only: bool = True
    exclude_previous: bool = Field(default=False, description="仅用户明确说换一批且已经绑定旧候选时为true。首次查询和排除已选必须为false。")
    signal_kind: str | None = Field(default=None, max_length=20, description="只要带这类来源信号的剧，如kd=KalosTV日榜；种类见知识资料「信号种类」。")
    sort: Literal["evidence_date", "rank"] = Field(
        default="evidence_date", description="rank=只看signal_kind那张榜最新一期上榜的剧并按名次升序，必须同时给signal_kind；默认按最近依据日期。"
    )
    exclude_posted: bool = Field(default=False, description="排除团队发布记录里已发过（post_count>0）的剧。用户说账号/团队没发过时使用。")
    posted_account: str | None = Field(default=None, max_length=200, description="排除发布记录里该账号发过的剧，账号名原样传入。")
    hot_only: bool = Field(
        default=False,
        description="只要带热门依据的剧：剧场侧的榜单、评级、剧单或运营备注（kd/kw/qc/qr/sm/smd/mg/fh/sh/gh/gn/ghh/dbn）。"
        "ReelShort本站行为（clk出站、bill预估订单、gsc搜索）不算。用户要“热门/上过榜”但没指定哪张榜时用它；指定某张榜用signal_kind。",
    )

    # Added after cards were stored: kept out of conditions_json while at their default, so a card that does not use
    # them has the shape it always had (the frontend parses conditions strictly) and so does a repeated call's hash.
    OMIT_AT_DEFAULT: ClassVar[tuple[str, ...]] = ("hot_only",)

    @model_serializer(mode="wrap")
    def _omit_new_fields_at_default(self, handler):
        dumped = handler(self)
        if isinstance(dumped, dict):
            for name in self.OMIT_AT_DEFAULT:
                if name in dumped and getattr(self, name) == type(self).model_fields[name].get_default(call_default_factory=True):
                    del dumped[name]
        return dumped

    def requested(self) -> dict:
        """The fields the caller set, to merge over a bound card's conditions for 换一批. model_dump leaves out an
        OMIT_AT_DEFAULT field at its default even when set, which would lose an explicit hot_only=false meant to lift a
        hot parent's filter; the stored form still leaves it out."""
        explicit = {name: getattr(self, name) for name in self.OMIT_AT_DEFAULT if name in self.model_fields_set}
        return {**self.model_dump(exclude_unset=True), **explicit}

    @property
    def filters_posted(self) -> bool:
        return self.exclude_posted or bool(self.posted_account)
