"""Collect configured due dates while preserving last complete source snapshots."""

from __future__ import annotations

import json
import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Mapping, Protocol

from ..config import AppConfig
from .model import (
    DueBatch,
    DueCoverageStatus,
    DueIssue,
    DueItem,
    DuePrecision,
    DueSourceKey,
)
from .outline import parse_unit_outline_markdown


_STATE_SCHEMA = 1


class DueProvider(Protocol):
    def list_due_items(
        self,
        course_id: str,
        *,
        logical_course_key: str,
        course_label: str,
    ) -> DueBatch: ...


class DueStateError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DueCollection:
    items: tuple[DueItem, ...]
    issues: tuple[DueIssue, ...]
    used_last_good: bool
    publishable: bool
    state_bytes: bytes


def _source_id(provider: str, instance: str, course_id: str) -> str:
    return "|".join((provider, instance.casefold(), course_id))


def _item_to_dict(item: DueItem) -> dict[str, object]:
    return {
        "key": {
            "provider": item.key.provider,
            "instance": item.key.instance,
            "course_id": item.key.course_id,
            "category": item.key.category,
            "remote_id": item.key.remote_id,
        },
        "logical_course_key": item.logical_course_key,
        "course_label": item.course_label,
        "title": item.title,
        "due_at": item.due_at.isoformat(),
        "due_kind": "datetime" if isinstance(item.due_at, datetime) else "date",
        "precision": item.precision.value,
        "points": None if item.points is None else str(item.points),
        "weight_percent": None if item.weight_percent is None else str(item.weight_percent),
    }


def _item_from_dict(raw: object) -> DueItem:
    item_fields = {
        "key", "logical_course_key", "course_label", "title", "due_at",
        "due_kind", "precision", "points", "weight_percent",
    }
    key_fields = {"provider", "instance", "course_id", "category", "remote_id"}
    if (
        not isinstance(raw, dict)
        or set(raw) != item_fields
        or not isinstance(raw.get("key"), dict)
        or set(raw["key"]) != key_fields
    ):
        raise ValueError
    key = raw["key"]
    due_kind = raw.get("due_kind")
    due_text = raw.get("due_at")
    text_fields = ("logical_course_key", "course_label", "title", "precision")
    if (
        due_kind not in {"datetime", "date"}
        or not isinstance(due_text, str)
        or any(not isinstance(raw.get(field), str) for field in text_fields)
        or any(not isinstance(key.get(field), str) for field in key_fields)
        or any(raw.get(field) is not None and not isinstance(raw.get(field), str)
               for field in ("points", "weight_percent"))
    ):
        raise ValueError
    due_at = datetime.fromisoformat(due_text) if due_kind == "datetime" else date.fromisoformat(due_text)
    points = None if raw["points"] is None else Decimal(raw["points"])
    weight = None if raw["weight_percent"] is None else Decimal(raw["weight_percent"])
    if any(value is not None and not value.is_finite() for value in (points, weight)):
        raise ValueError
    return DueItem(
        key=DueSourceKey(
            key["provider"], key["instance"], key["course_id"],
            key["category"], key["remote_id"],
        ),
        logical_course_key=raw["logical_course_key"],
        course_label=raw["course_label"],
        title=raw["title"],
        due_at=due_at,
        precision=DuePrecision(raw["precision"]),
        points=points,
        weight_percent=weight,
    )


def _load_state(path: Path, profile: str) -> dict[str, list[dict[str, object]]]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(payload, dict)
            or set(payload) != {"schema_version", "profile", "sources"}
            or type(payload.get("schema_version")) is not int
            or payload.get("schema_version") != _STATE_SCHEMA
            or payload.get("profile") != profile
            or not isinstance(payload.get("sources"), dict)
        ):
            raise ValueError
        sources: dict[str, list[dict[str, object]]] = {}
        for identity, values in payload["sources"].items():
            if not isinstance(identity, str) or not isinstance(values, list):
                raise ValueError
            decoded = [_item_from_dict(value) for value in values]
            if any(
                _source_id(item.key.provider, item.key.instance, item.key.course_id)
                != identity
                for item in decoded
            ):
                raise ValueError
            sources[identity] = [_item_to_dict(value) for value in decoded]
        return sources
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        OverflowError,
        InvalidOperation,
        json.JSONDecodeError,
    ) as exc:
        raise DueStateError("due state is unreadable or invalid") from exc


def _state_bytes(profile: str, sources: Mapping[str, list[dict[str, object]]]) -> bytes:
    return (
        json.dumps(
            {"schema_version": _STATE_SCHEMA, "profile": profile, "sources": sources},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _save_state(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def collect_configured_due_items(
    config: AppConfig,
    providers: Mapping[tuple[str, str], DueProvider],
    state_path: Path,
) -> DueCollection:
    """Refresh exact configured sources; partial/failed refreshes retain last-good data."""
    cached = _load_state(state_path, config.profile)
    next_cached = dict(cached)
    output: list[DueItem] = []
    issues: list[DueIssue] = []
    used_last_good = False
    publishable = True
    changed = False
    for course in config.courses:
        for source in course.sources:
            if source.provider not in {"canvas", "ed"}:
                continue
            identity = _source_id(source.provider, source.instance, source.course_id)
            label = source.code or course.folder or course.key
            try:
                provider = providers[(source.provider, source.instance)]
                batch = provider.list_due_items(
                    source.course_id,
                    logical_course_key=course.key,
                    course_label=label,
                )
            except Exception:
                batch = DueBatch(
                    source.provider,
                    source.instance,
                    source.course_id,
                    DueCoverageStatus.FAILED,
                    (),
                    (DueIssue("source_request_failed", retry_required=True),),
                )
            issues.extend(batch.issues)
            if batch.coverage is DueCoverageStatus.COMPLETE:
                serialized = [_item_to_dict(item) for item in batch.items]
                if next_cached.get(identity) != serialized:
                    next_cached[identity] = serialized
                    changed = True
                output.extend(batch.items)
                continue
            prior = cached.get(identity)
            if prior is not None:
                decoded = tuple(_item_from_dict(item) for item in prior)
                if any(item.logical_course_key != course.key for item in decoded):
                    raise DueStateError("due state source owner is invalid")
                output.extend(decoded)
                used_last_good = True
            else:
                output.extend(batch.items)
                publishable = False
    courses_by_key = {course.key: course for course in config.courses}
    gradescope = providers.get(("gradescope", "gradescope.com"))
    for mapping in config.daily_use.due.gradescope_courses:
        course = courses_by_key[mapping.logical_course_key]
        identity = _source_id("gradescope", "gradescope.com", mapping.course_id)
        label = next((source.code for source in course.sources if source.code), course.folder)
        try:
            if gradescope is None:
                raise KeyError("gradescope provider unavailable")
            batch = gradescope.list_due_items(
                mapping.course_id,
                logical_course_key=course.key,
                course_label=label,
            )
        except Exception:
            batch = DueBatch(
                "gradescope", "gradescope.com", mapping.course_id,
                DueCoverageStatus.FAILED, (),
                (DueIssue("source_request_failed", retry_required=True),),
            )
        issues.extend(batch.issues)
        if batch.coverage is DueCoverageStatus.COMPLETE:
            serialized = [_item_to_dict(item) for item in batch.items]
            if next_cached.get(identity) != serialized:
                next_cached[identity] = serialized
                changed = True
            output.extend(batch.items)
        elif identity in cached:
            decoded = tuple(_item_from_dict(item) for item in cached[identity])
            if any(item.logical_course_key != course.key for item in decoded):
                raise DueStateError("due state source owner is invalid")
            output.extend(decoded)
            used_last_good = True
        else:
            output.extend(batch.items)
            if mapping.required:
                publishable = False
    course_keys: dict[str, tuple[str, str]] = {}
    ambiguous_labels: set[str] = set()
    for course in config.courses:
        labels = {course.folder, *(source.code for source in course.sources if source.code)}
        canonical_label = next((source.code for source in course.sources if source.code), course.folder)
        for label in labels:
            normalized = label.casefold()
            mapping = (course.key, canonical_label)
            if normalized in course_keys and course_keys[normalized] != mapping:
                ambiguous_labels.add(normalized)
            else:
                course_keys[normalized] = mapping
    for label in ambiguous_labels:
        course_keys.pop(label, None)
    label_match = re.search(r"\b(20\d{2})\b", config.daily_use.due.semester_label or "")
    default_year = int(label_match.group(1)) if label_match else datetime.now().year
    outline_items: list[DueItem] = []
    for outline_path in config.daily_use.due.unit_outline_files:
        source_id = hashlib.sha256(str(outline_path.resolve()).encode("utf-8")).hexdigest()[:24]
        identity = _source_id("unit-outline", "local", source_id)
        try:
            batch = parse_unit_outline_markdown(
                outline_path.read_text(encoding="utf-8"),
                default_year=default_year,
                course_keys=course_keys,
                source_id=source_id,
            )
        except Exception:
            batch = DueBatch(
                "unit-outline", "local", source_id, DueCoverageStatus.FAILED, (),
                (DueIssue("outline_read_failed", retry_required=True),),
            )
        issues.extend(batch.issues)
        if batch.coverage is DueCoverageStatus.COMPLETE:
            serialized = [_item_to_dict(item) for item in batch.items]
            if next_cached.get(identity) != serialized:
                next_cached[identity] = serialized
                changed = True
            outline_items.extend(batch.items)
        elif identity in cached:
            decoded = tuple(_item_from_dict(item) for item in cached[identity])
            if any(item.logical_course_key not in courses_by_key for item in decoded):
                raise DueStateError("due state source owner is invalid")
            outline_items.extend(decoded)
            used_last_good = True
        else:
            outline_items.extend(batch.items)
            if outline_path in config.daily_use.due.required_outline_files:
                publishable = False
    if outline_items:
        confirmed_override_keys = {
            (logical_key, title.casefold())
            for logical_key, title in config.daily_use.due.outline_title_overrides
        }
        available_outline_keys = {
            (item.logical_course_key, item.title.casefold()) for item in outline_items
        }
        exact_outline_keys = confirmed_override_keys & available_outline_keys
        output = [
            item for item in output
            if (item.logical_course_key, item.title.casefold()) not in exact_outline_keys
        ]
        output.extend(outline_items)
    payload = _state_bytes(config.profile, next_cached)
    if changed or (not state_path.exists() and next_cached):
        _save_state(state_path, payload)
    return DueCollection(tuple(output), tuple(issues), used_last_good, publishable, payload)
