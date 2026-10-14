"""Versioned, non-secret configuration schema and atomic TOML persistence."""

from __future__ import annotations

import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 only
    import tomli as tomllib  # type: ignore[no-redef]

import tomli_w
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .paths import validate_profile_name
from .validation import validate_portable_component, validate_provider_instance


CURRENT_CONFIG_SCHEMA = 1
_MIME_FILTER_RE = re.compile(
    r"[a-z0-9!#$&^_.+-]+/(?:[a-z0-9!#$&^_.+-]+|\*)\Z"
)


class ConfigLoadError(RuntimeError):
    """A categorized, non-secret config loading failure."""

    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CanvasPlatformConfig(StrictModel):
    enabled: bool = False
    base_url: str | None = None
    credential: str | None = None

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlparse(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Canvas base_url must be an HTTPS origin/path without credentials or query")
        return value.rstrip("/")

    @model_validator(mode="after")
    def require_enabled_fields(self) -> "CanvasPlatformConfig":
        if self.enabled and (not self.base_url or not self.credential):
            raise ValueError("enabled Canvas requires base_url and credential reference")
        return self


class EdPlatformConfig(StrictModel):
    enabled: bool = False
    region: str = "global"
    credential: str | None = None

    @model_validator(mode="after")
    def require_enabled_credential(self) -> "EdPlatformConfig":
        if self.enabled and not self.credential:
            raise ValueError("enabled Ed requires a credential reference")
        return self


class GradescopePlatformConfig(StrictModel):
    """Independent optional provider; incomplete opt-in fails only on invocation."""

    enabled: bool = False
    email_credential: str | None = None
    password_credential: str | None = None


class PlatformsConfig(StrictModel):
    canvas: CanvasPlatformConfig = Field(default_factory=CanvasPlatformConfig)
    ed: EdPlatformConfig = Field(default_factory=EdPlatformConfig)
    gradescope: GradescopePlatformConfig = Field(default_factory=GradescopePlatformConfig)


class ContentSelection(StrictModel):
    files: bool = True
    pages: bool = True
    lessons: bool = True
    resources: bool = True


class DownloadPolicy(StrictModel):
    max_file_bytes: int | None = Field(default=100 * 1024 * 1024, gt=0)
    include_extensions: tuple[str, ...] = ()
    exclude_extensions: tuple[str, ...] = (".mp4", ".mov")
    include_mime: tuple[str, ...] = ()
    exclude_mime: tuple[str, ...] = ()

    @field_validator("include_extensions", "exclude_extensions")
    @classmethod
    def normalize_extensions(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized: list[str] = []
        for value in values:
            clean = value.strip().casefold()
            if not clean:
                continue
            if not clean.startswith("."):
                clean = "." + clean
            if "/" in clean or "\\" in clean:
                raise ValueError("extension filters cannot contain path separators")
            if clean not in normalized:
                normalized.append(clean)
        return tuple(normalized)

    @field_validator("include_mime", "exclude_mime")
    @classmethod
    def normalize_mime_filters(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized: list[str] = []
        for value in values:
            clean = value.strip().casefold()
            if not _MIME_FILTER_RE.fullmatch(clean):
                raise ValueError(
                    "MIME filters must be type/subtype or type/* without parameters"
                )
            if clean not in normalized:
                normalized.append(clean)
        return tuple(normalized)


class CourseSource(StrictModel):
    provider: Literal["canvas", "ed"]
    instance: str = Field(min_length=1)
    course_id: str = Field(min_length=1)
    code: str | None = None
    name: str | None = None
    term: str | None = None

    @field_validator("instance", "course_id")
    @classmethod
    def validate_identity_text(cls, value: str, info: Any) -> str:
        if (
            value != value.strip()
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
        ):
            raise ValueError(f"{info.field_name} must be a safe canonical string")
        if info.field_name == "instance":
            validate_provider_instance(value)
        return value


class RetiredCourseSource(StrictModel):
    """Persistent ownership evidence for a detached remote source."""

    source: CourseSource
    former_course_key: str = Field(min_length=1)

    @field_validator("former_course_key")
    @classmethod
    def validate_former_key(cls, value: str) -> str:
        if value != value.strip() or any(
            ord(character) < 32 or ord(character) == 127 for character in value
        ):
            raise ValueError("former_course_key must be a safe non-empty string")
        return value


class CourseConfig(StrictModel):
    key: str = Field(min_length=1)
    folder: str = Field(min_length=1)
    confirmed_at: datetime
    sources: tuple[CourseSource, ...] = Field(min_length=1)
    content: ContentSelection = Field(default_factory=ContentSelection)
    policy: DownloadPolicy = Field(default_factory=DownloadPolicy)

    @field_validator("folder")
    @classmethod
    def validate_folder(cls, value: str) -> str:
        clean = value.strip()
        if clean in {"", ".", ".."} or "/" in clean or "\\" in clean:
            raise ValueError("course folder must be one path component")
        validate_portable_component(clean, field="course folder")
        return clean

    @field_validator("key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        if value != value.strip() or any(
            ord(character) < 32 or ord(character) == 127 for character in value
        ):
            raise ValueError("course key must be a safe non-empty string")
        return value

    @field_validator("confirmed_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("confirmed_at must include a timezone")
        return value

    @model_validator(mode="after")
    def require_unique_sources(self) -> "CourseConfig":
        identities = [
            (source.provider, source.instance.casefold(), source.course_id)
            for source in self.sources
        ]
        if len(identities) != len(set(identities)):
            raise ValueError("course sources must be unique by provider, instance and course_id")
        providers = [source.provider for source in self.sources]
        if len(providers) != len(set(providers)):
            raise ValueError("a logical course can contain at most one source per provider")
        return self


class AppConfig(StrictModel):
    schema_version: Literal[1] = CURRENT_CONFIG_SCHEMA
    profile: str = "default"
    output_root: Path
    platforms: PlatformsConfig = Field(default_factory=PlatformsConfig)
    courses: tuple[CourseConfig, ...] = ()
    retired_sources: tuple[RetiredCourseSource, ...] = ()

    @field_validator("profile")
    @classmethod
    def validate_profile(cls, value: str) -> str:
        return validate_profile_name(value)

    @field_validator("output_root")
    @classmethod
    def require_absolute_output_root(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("output_root must be an absolute user-confirmed path")
        return value

    @model_validator(mode="after")
    def validate_course_scope(self) -> "AppConfig":
        keys = [course.key for course in self.courses]
        folders = [course.folder.casefold() for course in self.courses]
        if len(keys) != len(set(keys)):
            raise ValueError("course keys must be unique")
        if len(folders) != len(set(folders)):
            raise ValueError("course folders must be unique within a profile")
        enabled = {
            provider
            for provider in ("canvas", "ed")
            if getattr(self.platforms, provider).enabled
        }
        source_owners: dict[tuple[str, str, str], str] = {}
        for course in self.courses:
            for source in course.sources:
                if source.provider not in enabled:
                    raise ValueError(
                        f"course {course.key!r} references disabled provider {source.provider!r}"
                    )
                identity = (source.provider, source.instance.casefold(), source.course_id)
                owner = source_owners.get(identity)
                if owner is not None:
                    raise ValueError(
                        f"remote source is assigned to more than one logical course: {owner!r} and {course.key!r}"
                    )
                source_owners[identity] = course.key
        retired_identities: set[tuple[str, str, str]] = set()
        for retired in self.retired_sources:
            identity = (
                retired.source.provider,
                retired.source.instance.casefold(),
                retired.source.course_id,
            )
            if identity in retired_identities:
                raise ValueError("retired remote source identities must be unique")
            if identity in source_owners:
                raise ValueError("a retired remote source requires explicit migration before reattachment")
            retired_identities.add(identity)
        return self


def _schema_version(data: dict[str, Any]) -> Any:
    return data.get("schema_version")


def load_config(path: Path) -> AppConfig:
    """Load one config; unknown/corrupt schemas fail closed."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ConfigLoadError("unreadable", f"Cannot read config {path}: {exc}") from exc
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ConfigLoadError("invalid_toml", f"Config is not valid TOML: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigLoadError("invalid_shape", "Config root must be a TOML table")
    if _schema_version(data) != CURRENT_CONFIG_SCHEMA:
        raise ConfigLoadError(
            "unsupported_schema",
            f"Unsupported config schema {_schema_version(data)!r}; expected {CURRENT_CONFIG_SCHEMA}",
        )
    try:
        return AppConfig.model_validate(data)
    except ValueError as exc:
        raise ConfigLoadError("invalid_config", f"Config validation failed: {exc}") from exc


def save_config(config: AppConfig, path: Path) -> None:
    """Atomically save non-secret config without mutating an existing file on failure."""
    payload = config.model_dump(mode="json", exclude_none=True)
    document = tomli_w.dumps(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", newline="\n", dir=path.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(document)
            handle.flush()
            os.fsync(handle.fileno())
        # Prove the exact serialized bytes remain loadable before commit.
        load_config(temporary)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
