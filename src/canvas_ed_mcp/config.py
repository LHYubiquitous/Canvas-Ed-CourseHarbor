"""Versioned, non-secret configuration schema and atomic TOML persistence."""

from __future__ import annotations

import os
import re
import tempfile
from datetime import date, datetime
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
from .initial import InitialSyncMode
from .validation import validate_portable_component, validate_provider_instance


CURRENT_CONFIG_SCHEMA = 3
_UNLIMITED_FILE_BYTES = "unlimited"
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


class LauncherConfig(StrictModel):
    enabled: bool = True
    directory: Path | None = None

    @field_validator("directory")
    @classmethod
    def require_absolute_directory(cls, value: Path | None) -> Path | None:
        if value is not None and not value.is_absolute():
            raise ValueError("launcher directory must be absolute")
        return value


class GradescopeDueMapping(StrictModel):
    logical_course_key: str = Field(min_length=1)
    course_id: str = Field(min_length=1)
    required: bool = False

    @field_validator("logical_course_key", "course_id")
    @classmethod
    def validate_mapping_text(cls, value: str) -> str:
        if value != value.strip() or any(ord(character) < 32 for character in value):
            raise ValueError("Gradescope due mapping must use safe exact identifiers")
        return value


class TeachingPeriod(StrictModel):
    label: str = Field(min_length=1)
    start_date: date
    end_date: date

    @field_validator("label")
    @classmethod
    def validate_label(cls, value: str) -> str:
        if value != value.strip() or any(ord(character) < 32 for character in value):
            raise ValueError("teaching-period label must be safe non-empty text")
        return value

    @model_validator(mode="after")
    def validate_range(self) -> "TeachingPeriod":
        if self.end_date < self.start_date:
            raise ValueError("teaching-period end date cannot precede its start")
        return self


class DueReportConfig(StrictModel):
    enabled: bool = True
    timezone: Literal["Australia/Sydney"] = "Australia/Sydney"
    semester_label: str | None = None
    teaching_start: date | None = None
    teaching_periods: tuple[TeachingPeriod, ...] = ()
    unit_outline_files: tuple[Path, ...] = ()
    required_outline_files: tuple[Path, ...] = ()
    gradescope_courses: tuple[GradescopeDueMapping, ...] = ()
    outline_title_overrides: tuple[tuple[str, str], ...] = ()

    @field_validator("semester_label")
    @classmethod
    def validate_semester_label(cls, value: str | None) -> str | None:
        if value is None:
            return None
        clean = value.strip()
        if not clean or any(ord(character) < 32 for character in clean):
            raise ValueError("semester label must be safe non-empty text")
        return clean

    @field_validator("unit_outline_files", "required_outline_files")
    @classmethod
    def require_absolute_outline_files(cls, values: tuple[Path, ...]) -> tuple[Path, ...]:
        if any(not value.is_absolute() for value in values):
            raise ValueError("Unit Outline files must use absolute paths")
        return values

    @model_validator(mode="after")
    def require_unique_gradescope_mappings(self) -> "DueReportConfig":
        logical = [item.logical_course_key for item in self.gradescope_courses]
        remote = [item.course_id for item in self.gradescope_courses]
        if len(logical) != len(set(logical)) or len(remote) != len(set(remote)):
            raise ValueError("Gradescope due mappings must be one-to-one")
        periods = sorted(self.teaching_periods, key=lambda item: item.start_date)
        if any(current.start_date <= previous.end_date for previous, current in zip(periods, periods[1:])):
            raise ValueError("teaching periods cannot overlap")
        overrides: list[tuple[str, str]] = []
        for logical_key, title in self.outline_title_overrides:
            if (
                not logical_key or logical_key != logical_key.strip()
                or not title or title != title.strip()
                or any(ord(character) < 32 for character in logical_key + title)
            ):
                raise ValueError("outline title overrides require safe exact text")
            overrides.append((logical_key, title.casefold()))
        if len(overrides) != len(set(overrides)):
            raise ValueError("outline title overrides must be unique")
        if not set(self.required_outline_files).issubset(self.unit_outline_files):
            raise ValueError("required Unit Outline files must also be configured outline files")
        return self


class DailyUseConfig(StrictModel):
    launchers: LauncherConfig = Field(default_factory=LauncherConfig)
    due: DueReportConfig = Field(default_factory=DueReportConfig)


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
    initial_mode: InitialSyncMode = InitialSyncMode.BASELINE_FROM_NOW
    reconcile_generation: int = 0

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

    @field_validator("reconcile_generation")
    @classmethod
    def validate_reconcile_generation(cls, value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("reconcile_generation must be a non-negative integer")
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
    schema_version: Literal[3] = CURRENT_CONFIG_SCHEMA
    profile: str = "default"
    output_root: Path
    platforms: PlatformsConfig = Field(default_factory=PlatformsConfig)
    courses: tuple[CourseConfig, ...] = ()
    retired_sources: tuple[RetiredCourseSource, ...] = ()
    daily_use: DailyUseConfig = Field(default_factory=DailyUseConfig)

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
        gradescope_mappings = self.daily_use.due.gradescope_courses
        if gradescope_mappings and not self.platforms.gradescope.enabled:
            raise ValueError("Gradescope due mappings require the optional provider")
        known_keys = set(keys)
        if any(mapping.logical_course_key not in known_keys for mapping in gradescope_mappings):
            raise ValueError("Gradescope due mappings require an exact configured logical course")
        if any(
            logical_key not in known_keys
            for logical_key, _ in self.daily_use.due.outline_title_overrides
        ):
            raise ValueError("outline title overrides require an exact configured logical course")
        return self


def _schema_version(data: dict[str, Any]) -> Any:
    return data.get("schema_version")


def _migrate_config_to_current(data: dict[str, Any], schema: int) -> None:
    """Upgrade supported historical documents without changing runtime behavior."""
    if schema == 1:
        data.setdefault("daily_use", {})
    if schema in {1, 2}:
        courses = data.get("courses", ())
        if isinstance(courses, list):
            for course in courses:
                if not isinstance(course, dict):
                    continue
                sources = course.get("sources", ())
                if isinstance(sources, list):
                    for source in sources:
                        if isinstance(source, dict):
                            source.setdefault("reconcile_generation", 0)
        retired = data.get("retired_sources", ())
        if isinstance(retired, list):
            for item in retired:
                if not isinstance(item, dict):
                    continue
                source = item.get("source")
                if isinstance(source, dict):
                    source.setdefault("reconcile_generation", 0)
        data["schema_version"] = CURRENT_CONFIG_SCHEMA


def load_config_bytes(raw: bytes) -> AppConfig:
    """Validate one exact config byte snapshot; unknown/corrupt schemas fail closed."""
    if not isinstance(raw, bytes):
        raise TypeError("raw config must be bytes")
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ConfigLoadError("invalid_toml", f"Config is not valid TOML: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigLoadError("invalid_shape", "Config root must be a TOML table")
    schema = _schema_version(data)
    if schema not in {1, 2, CURRENT_CONFIG_SCHEMA}:
        raise ConfigLoadError(
            "unsupported_schema",
            f"Unsupported config schema {_schema_version(data)!r}; expected {CURRENT_CONFIG_SCHEMA}",
        )
    _migrate_config_to_current(data, schema)
    courses = data.get("courses", ())
    if isinstance(courses, list):
        for course in courses:
            if not isinstance(course, dict):
                continue
            policy = course.get("policy")
            if (
                isinstance(policy, dict)
                and policy.get("max_file_bytes") == _UNLIMITED_FILE_BYTES
            ):
                policy["max_file_bytes"] = None
    try:
        return AppConfig.model_validate(data)
    except ValueError as exc:
        raise ConfigLoadError("invalid_config", f"Config validation failed: {exc}") from exc


def load_config(path: Path) -> AppConfig:
    """Load one config; unknown/corrupt schemas fail closed."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ConfigLoadError("unreadable", f"Cannot read config {path}: {exc}") from exc
    return load_config_bytes(raw)


def _config_payload(config: AppConfig) -> dict[str, Any]:
    payload = config.model_dump(mode="json", exclude_none=True)
    serialized_courses = payload.get("courses", [])
    for index, course in enumerate(config.courses):
        if course.policy.max_file_bytes is None:
            serialized_courses[index].setdefault("policy", {})[
                "max_file_bytes"
            ] = _UNLIMITED_FILE_BYTES
    return payload


def save_config(config: AppConfig, path: Path) -> None:
    """Atomically save non-secret config without mutating an existing file on failure."""
    payload = _config_payload(config)
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


def save_new_config(config: AppConfig, path: Path) -> bool:
    """Atomically create config; return whether post-commit cleanup is pending."""
    payload = _config_payload(config)
    document = tomli_w.dumps(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    committed = False
    cleanup_pending = False
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", newline="\n", dir=path.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(document)
            handle.flush()
            os.fsync(handle.fileno())
        load_config(temporary)
        os.link(temporary, path)
        committed = True
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                if not committed:
                    raise
                cleanup_pending = True
    return cleanup_pending
