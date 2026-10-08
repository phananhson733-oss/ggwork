"""Strict public inputs shared by page, tools, and native worker."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ID = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")]
SHA = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Positive = Annotated[float, Field(gt=0, allow_inf_nan=False)]


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def storable(cls, value):
        def check(item):
            if isinstance(item, str) and ("\x00" in item or any(0xD800 <= ord(c) <= 0xDFFF for c in item)):
                raise ValueError("Invalid text")
            if isinstance(item, dict):
                for k, v in item.items():
                    check(k)
                    check(v)
            elif isinstance(item, list):
                for v in item:
                    check(v)

        check(value)
        return value


class Requirements(StrictInput):
    profile: Literal["highlight", "hook"] = "hook"
    instructions: str = Field(min_length=1, max_length=10000)
    output_count: int = Field(ge=1, le=10, strict=True)
    duration_seconds: float = Field(ge=5, le=180, allow_inf_nan=False)
    aspect_ratio: Literal["9:16", "16:9", "1:1"]
    language: str = Field(default="auto", min_length=1, max_length=32)
    review_plan: bool = False


class Source(StrictInput):
    media_id: ID
    name: str = Field(min_length=1, max_length=255)
    episode: int = Field(ge=1, strict=True)
    relative_path: str = Field(min_length=1, max_length=1024)
    size_bytes: int = Field(ge=1, strict=True)
    sha256: SHA | None = None
    duration_seconds: Positive | None = None
    state: Literal["selected", "received", "verified", "failed"] = "selected"

    @model_validator(mode="after")
    def local_identity(self):
        if self.relative_path.startswith(("/", "\\")) or any(x in ("", ".", "..") for x in self.relative_path.replace("\\", "/").split("/")):
            raise ValueError("Source path must be relative to the authorized grant")
        if self.state == "verified" and (self.sha256 is None or self.duration_seconds is None):
            raise ValueError("Verified source requires hash and duration")
        return self


class Manifest(StrictInput):
    version: int = Field(ge=1, strict=True)
    grant_id: ID
    files: list[Source] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def unique_sources(self):
        for key in ("media_id", "episode", "relative_path"):
            if len({getattr(f, key) for f in self.files}) != len(self.files):
                raise ValueError("Duplicate or ambiguous source identity")
        return self


class SourceDirectory(StrictInput):
    grant_id: ID
    relative_path: str = Field(min_length=1, max_length=1024)

    @model_validator(mode="after")
    def relative_directory(self):
        if self.relative_path != "." and (
            self.relative_path.startswith(("/", "\\")) or any(x in ("", ".", "..") for x in self.relative_path.replace("\\", "/").split("/"))
        ):
            raise ValueError("Directory must be relative to the authorized grant")
        return self


class CreateTask(StrictInput):
    request_id: ID
    title: str = Field(min_length=1, max_length=255)
    requirements: Requirements
    device_id: ID | None = None
    source_manifest: Manifest | None = None
    source_thread_id: ID | None = None
    parent_task_id: ID | None = None
    source_directory: SourceDirectory | None = None

    @model_validator(mode="after")
    def one_source_input(self):
        if self.source_directory and self.source_manifest:
            raise ValueError("Select a directory or a manifest")
        return self


class PrepareTask(StrictInput):
    device_id: ID
    source_manifest: Manifest | None = None
    source_directory: SourceDirectory | None = None

    @model_validator(mode="after")
    def one_source_input(self):
        if self.source_directory and self.source_manifest:
            raise ValueError("Select a directory or a manifest")
        return self


class RegisterDevice(StrictInput):
    name: str = Field(min_length=1, max_length=128)


class DeviceHeartbeat(StrictInput):
    platform: Literal["darwin-arm64", "unsupported"]
    ready: bool
    reasons: list[str] = Field(default_factory=list, max_length=20)
    grants: list[ID] = Field(default_factory=list, max_length=100)
    worker_version: str = Field(min_length=1, max_length=64)


class Claim(StrictInput):
    request_id: ID


class VerifyManifest(StrictInput):
    source_manifest: Manifest


class Result(StrictInput):
    artifact_id: ID
    sha256: SHA
    size_bytes: int = Field(gt=0, strict=True)
    duration_seconds: Positive
    width: int = Field(gt=0, le=16384, strict=True)
    height: int = Field(gt=0, le=16384, strict=True)
    video_codec: Literal["h264", "hevc"]
    audio_codec: Literal["aac"]
    verified: Literal[True]


Stage = Literal["transcribing", "planning", "awaiting_plan", "rendering", "verifying"]


class WorkerReport(StrictInput):
    attempt_id: ID
    fence: int = Field(ge=1, strict=True)
    event_id: ID
    kind: Literal["heartbeat", "stage", "output", "failure", "complete", "stopped"]
    stage: Stage | None = None
    output_id: ID | None = None
    error: str | None = Field(default=None, max_length=2000)
    result: Result | None = None


class RetryTask(StrictInput):
    request_id: ID
    output_ids: list[ID] = Field(default_factory=list, max_length=10)
    stage: Literal["transcribing", "planning"] | None = None


class Settings(StrictInput):
    skill_enabled: bool
