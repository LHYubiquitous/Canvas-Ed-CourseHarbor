"""Read-only Ed course, lesson and resource adapter.

Ed's API is an unofficial/beta boundary.  This module deliberately keeps raw
payloads, credentials and signed URLs out of the public provider contract.
"""

from __future__ import annotations

import hashlib
import html
import ipaddress
import json
import mimetypes
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from contextlib import contextmanager
from threading import RLock
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlparse, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup

from canvas_ed_mcp.renderers.html import (
    AssetDisposition,
    HtmlAssetResolution,
    RenderedHtml,
    render_page_html,
)
from canvas_ed_mcp.secrets import install_http_redaction_filters
from canvas_ed_mcp.sync.model import RemoteItem, RemoteKind, SourceKey

from .base import (
    CategoryCoverage,
    ContentBatch,
    ContentIssue,
    CourseCandidate,
    CoverageStatus,
    IssueScope,
    ProviderError,
)


DEFAULT_ED_BASE_URL = "https://edstem.org/api"
_EDSTEM_HOST = "edstem.org"
_DEFAULT_ED_STATIC_ASSET_HOSTS = frozenset({"static.au.edusercontent.com"})
_LESSON_SIGNATURE_FIELDS = (
    "id",
    "title",
    "outline",
    "slides",
    "slide_count",
    "created_at",
    "updated_at",
    "module_id",
)
_RESOURCE_SIGNATURE_FIELDS = (
    "id",
    "name",
    "extension",
    "size",
    "updated_at",
    "link",
)
_URL_IN_TEXT = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_VOLATILE_QUERY_NAMES = frozenset(
    {
        "access_token",
        "api_key",
        "credential",
        "expires",
        "expiry",
        "key",
        "policy",
        "se",
        "sig",
        "signature",
        "token",
        "verifier",
    }
)
def _canonical_instance(parsed: Any) -> str:
    host = parsed.hostname.casefold()
    return host if parsed.port in {None, 443} else f"{host}:{parsed.port}"


def _signature(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _canonicalize_signed_urls(value: object) -> object:
    if isinstance(value, dict):
        return {
            str(key): _canonicalize_signed_urls(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_canonicalize_signed_urls(item) for item in value]
    if isinstance(value, str):
        def replace(match: re.Match[str]) -> str:
            parsed = urlsplit(html.unescape(match.group(0)))
            host = parsed.hostname or ""
            if ":" in host and not host.startswith("["):
                host = f"[{host}]"
            netloc = host if parsed.port is None else f"{host}:{parsed.port}"
            query = urlencode(
                [
                    (key, item)
                    for key, item in parse_qsl(parsed.query, keep_blank_values=True)
                    if key.casefold() not in _VOLATILE_QUERY_NAMES
                    and not key.casefold().startswith(("x-amz-", "x-goog-"))
                ],
                doseq=True,
            )
            return urlunsplit((parsed.scheme, netloc, parsed.path, query, ""))

        return _URL_IN_TEXT.sub(replace, value)
    return value


def _semantic_lesson_signature(lesson: dict[str, Any]) -> str:
    return _signature(
        _canonicalize_signed_urls(
            {field: lesson.get(field) for field in _LESSON_SIGNATURE_FIELDS}
        )
    )


def _resource_signature(resource: dict[str, Any]) -> str:
    return _signature({field: resource.get(field) for field in _RESOURCE_SIGNATURE_FIELDS})


def _required_id(value: object, label: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ProviderError("schema_drift", f"Ed {label} has no stable id")
    result = str(value)
    if not result or result != result.strip() or any(
        ord(char) < 32 or ord(char) == 127 for char in result
    ):
        raise ProviderError("schema_drift", f"Ed {label} has no stable id")
    return result


def _optional_text(value: object, label: str, *, allow_integer: bool = False) -> str:
    if value is None:
        return ""
    allowed = isinstance(value, str) or (
        allow_integer and isinstance(value, int) and not isinstance(value, bool)
    )
    if not allowed:
        raise ProviderError("schema_drift", f"Ed {label} is invalid")
    result = str(value)
    if any(ord(char) < 32 or ord(char) == 127 for char in result):
        raise ProviderError("schema_drift", f"Ed {label} is invalid")
    return result


def _optional_markup(value: object, label: str) -> str:
    """Accept normal HTML formatting whitespace while rejecting unsafe controls."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ProviderError("schema_drift", f"Ed {label} is invalid")
    if any(
        (ord(char) < 32 and char not in "\t\n\r") or ord(char) == 127
        for char in value
    ):
        raise ProviderError("schema_drift", f"Ed {label} is invalid")
    return value


def _ed_xml(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    try:
        return _render_xml_node(ET.fromstring(value))
    except ET.ParseError:
        return f"<pre>{html.escape(value)}</pre>"


def _xml_inner(node: ET.Element) -> str:
    pieces = [html.escape(node.text or "")]
    for child in node:
        pieces.append(_render_xml_node(child))
        pieces.append(html.escape(child.tail or ""))
    return "".join(pieces)


def _render_xml_node(node: ET.Element) -> str:
    content = _xml_inner(node)
    tag = node.tag
    if tag == "document":
        return content
    if tag == "paragraph":
        return f"<p>{content or '&nbsp;'}</p>"
    if tag == "bold":
        return f"<strong>{content}</strong>"
    if tag == "italic":
        return f"<em>{content}</em>"
    if tag == "underline":
        return f"<span>{content}</span>"
    if tag == "sub":
        return f"<sub>{content}</sub>"
    if tag == "heading":
        try:
            level = min(max(int(node.attrib.get("level", "2")), 1), 6)
        except ValueError:
            level = 2
        return f"<h{level}>{content}</h{level}>"
    if tag == "list":
        list_tag = "ol" if node.attrib.get("style") in {"ordered", "number"} else "ul"
        return f"<{list_tag}>{content}</{list_tag}>"
    if tag == "list-item":
        return f"<li>{content}</li>"
    if tag == "break":
        return "<br>"
    if tag == "code":
        return f"<code>{content}</code>"
    if tag == "pre":
        return f"<pre>{content}</pre>"
    if tag == "blockquote":
        return f"<blockquote>{content}</blockquote>"
    if tag == "image":
        src = html.escape(node.attrib.get("src", ""), quote=True)
        return f'<img src="{src}" alt="Ed lesson image">'
    if tag == "link":
        href = html.escape(node.attrib.get("href", ""), quote=True)
        return f'<a href="{href}">{content or href}</a>'
    if tag == "file":
        href = html.escape(node.attrib.get("url", ""), quote=True)
        label = html.escape(node.attrib.get("filename", "Attachment"))
        return f'<p>Attachment: <a href="{href}">{label}</a></p>'
    if tag == "math":
        latex = node.attrib.get("latex") or node.text or ""
        return f'<span class="math">{html.escape(latex)}</span>'
    return f"<span>{content}</span>"


def _lesson_body(lesson: dict[str, Any]) -> tuple[str, tuple[str, ...]]:
    slides = lesson.get("slides")
    if not isinstance(slides, list):
        raise ProviderError("schema_drift", "Ed lesson slides are not a list")
    try:
        ordered = sorted(slides, key=lambda slide: int(slide.get("index", 0)))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ProviderError("schema_drift", "Ed lesson slide metadata is invalid") from exc
    pieces: list[str] = []
    issues: list[str] = []

    def add_issue(code: str) -> None:
        if code not in issues:
            issues.append(code)

    for position, slide in enumerate(ordered, 1):
        if not isinstance(slide, dict):
            raise ProviderError("schema_drift", "Ed lesson slide is not an object")
        slide_type = _optional_text(slide.get("type"), "lesson slide type") or "document"
        slide_title = _optional_text(slide.get("title"), "lesson slide title")
        title = html.escape(slide_title or f"Slide {position}")
        display_type = slide_type
        if slide_type == "html":
            body = _optional_markup(slide.get("html"), "lesson slide HTML")
        elif slide_type == "pdf":
            file_url = _optional_text(slide.get("file_url"), "lesson PDF URL")
            if file_url:
                href = html.escape(file_url, quote=True)
                body = f'<p><a href="{href}">Open PDF attachment</a></p>'
            else:
                body = ""
        elif slide_type == "workspace-partition":
            body = "<p>Interactive Ed workspace content is available in the source lesson.</p>"
        elif slide_type in {"code", "document", "quiz"}:
            content = slide.get("content")
            if content is None:
                content = slide.get("passage")
            if content is not None and not isinstance(content, str):
                raise ProviderError("schema_drift", "Ed lesson slide content is invalid")
            body = _ed_xml(content)
        else:
            content = slide.get("content")
            if content is None:
                content = slide.get("passage")
            if content is not None and not isinstance(content, str):
                raise ProviderError("schema_drift", "Ed lesson slide content is invalid")
            if isinstance(content, str) and content.strip():
                body = _ed_xml(content)
                add_issue("generic_slide_rendered")
            else:
                body = "<p>This Ed slide type is not supported.</p>"
                add_issue("unsupported_slide_kind")
            display_type = "unsupported"
        if not body.strip():
            body = "<p>This Ed slide is empty or not supported.</p>"
        pieces.append(
            f'<section class="slide"><p>Slide {position} | {html.escape(display_type)}</p>'
            f"<h2>{title}</h2>{body}</section>"
        )
    return "".join(pieces), tuple(issues)


class EdProvider:
    provider_name = "ed"
    supported_kinds = frozenset({RemoteKind.LESSON, RemoteKind.RESOURCE})

    def __init__(
        self,
        token: str,
        *,
        base_url: str = DEFAULT_ED_BASE_URL,
        client: httpx.Client | None = None,
        asset_client: httpx.Client | None = None,
        allowed_asset_hosts: frozenset[str] | None = None,
        max_inline_image_bytes: int = 25 * 1024 * 1024,
    ) -> None:
        normalized = base_url.rstrip("/")
        parsed = urlparse(normalized)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Ed base URL must be an HTTPS URL without credentials or query")
        if not token:
            raise ValueError("Ed token is required")
        if (
            isinstance(max_inline_image_bytes, bool)
            or not isinstance(max_inline_image_bytes, int)
            or max_inline_image_bytes <= 0
        ):
            raise ValueError("max_inline_image_bytes must be positive")
        if client is not None and client is asset_client:
            raise ValueError("Ed API and asset clients must be separate")
        if asset_client is not None:
            unsafe_headers = {"authorization", "cookie", "proxy-authorization"}
            if any(name in asset_client.headers for name in unsafe_headers):
                raise ValueError("Ed asset client cannot have credential headers")
            if asset_client.auth is not None or asset_client.cookies:
                raise ValueError("Ed asset client cannot have auth or cookies")
            if any(asset_client.event_hooks.values()):
                raise ValueError("Ed asset client cannot have event hooks")

        if allowed_asset_hosts is None:
            default_hosts = {parsed.hostname}
            if parsed.hostname == _EDSTEM_HOST:
                default_hosts.update(_DEFAULT_ED_STATIC_ASSET_HOSTS)
            configured_asset_hosts = frozenset(default_hosts)
        else:
            configured_asset_hosts = allowed_asset_hosts
        if not isinstance(configured_asset_hosts, frozenset) or not configured_asset_hosts:
            raise ValueError("allowed_asset_hosts must be a non-empty frozenset")
        normalized_asset_hosts: set[str] = set()
        for host in configured_asset_hosts:
            if (
                not isinstance(host, str)
                or not host
                or host != host.casefold()
                or any(character in host for character in "/:@?#[]")
            ):
                raise ValueError("allowed asset hosts must be canonical hostnames")
            normalized_asset_hosts.add(host)

        install_http_redaction_filters()
        self.base_url = normalized
        self.instance = _canonical_instance(parsed)
        self._headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        self._client = client or httpx.Client(timeout=30.0, follow_redirects=False)
        self._asset_client = asset_client or httpx.Client(timeout=30.0, follow_redirects=True)
        self._allowed_asset_hosts = frozenset(normalized_asset_hosts)
        self._max_inline_image_bytes = max_inline_image_bytes
        self._lesson_snapshots: dict[SourceKey, dict[str, Any]] = {}
        self._lesson_items: dict[SourceKey, RemoteItem] = {}
        self._resource_items: dict[SourceKey, RemoteItem] = {}
        self._cache_lock = RLock()

    def close(self) -> None:
        with self._cache_lock:
            self._lesson_snapshots.clear()
            self._lesson_items.clear()
            self._resource_items.clear()
        self._asset_client.close()
        self._client.close()

    def _json(self, endpoint: str) -> dict[str, Any]:
        try:
            response = self._client.get(
                f"{self.base_url}{endpoint}",
                headers=self._headers,
                follow_redirects=False,
            )
        except httpx.HTTPError:
            raise ProviderError("request_failed", "Ed API request failed") from None
        if response.status_code == 401:
            raise ProviderError("authentication_failed", "Ed API authentication failed")
        if response.status_code == 403:
            raise ProviderError("authorization_failed", "Ed API request was not authorized")
        try:
            response.raise_for_status()
        except httpx.HTTPError:
            raise ProviderError("request_failed", "Ed API request failed") from None
        try:
            payload = response.json()
        except ValueError:
            raise ProviderError("schema_drift", "Ed API response is not valid JSON") from None
        if not isinstance(payload, dict):
            raise ProviderError("schema_drift", "Ed API response is not an object")
        return payload

    def list_courses(self) -> tuple[CourseCandidate, ...]:
        payload = self._json("/user")
        wrappers = payload.get("courses")
        if not isinstance(wrappers, list):
            raise ProviderError("schema_drift", "Ed user response has no course list")
        output: list[CourseCandidate] = []
        for wrapper in wrappers:
            course = wrapper.get("course") if isinstance(wrapper, dict) else None
            if not isinstance(course, dict) or course.get("id") is None:
                raise ProviderError("schema_drift", "Ed course entry has no stable id")
            course_id = _required_id(course["id"], "course entry")
            year = _optional_text(course.get("year"), "course year", allow_integer=True).strip()
            session = _optional_text(course.get("session"), "course session").strip()
            term = " ".join(part for part in (year, session) if part) or None
            code = _optional_text(course.get("code"), "course code")
            name = _optional_text(course.get("name"), "course name")
            output.append(
                CourseCandidate(
                    provider="ed",
                    instance=self.instance,
                    course_id=course_id,
                    code=code,
                    name=name or code or course_id,
                    term=term,
                )
            )
        return tuple(output)

    def list_due_items(
        self,
        course_id: str,
        *,
        logical_course_key: str,
        course_label: str,
    ):
        """Return dated Ed lessons without inventing unavailable point values."""
        from datetime import datetime

        from ..due.model import (
            DueBatch,
            DueCoverageStatus,
            DueIssue,
            DueItem,
            DuePrecision,
            DueSourceKey,
        )

        payload = self._json(f"/courses/{quote(course_id, safe='')}/lessons")
        lessons = payload.get("lessons")
        if not isinstance(lessons, list):
            raise ProviderError("schema_drift", "Ed lessons response has no lesson list")
        items: list[DueItem] = []
        issues: list[DueIssue] = []
        for index, raw in enumerate(lessons):
            try:
                if not isinstance(raw, dict) or raw.get("id") is None:
                    raise ValueError
                raw_due = raw.get("effective_due_at") or raw.get("due_at")
                if raw_due is None:
                    continue
                if not isinstance(raw_due, str):
                    raise ValueError
                due_at = datetime.fromisoformat(raw_due.replace("Z", "+00:00"))
                if due_at.tzinfo is None:
                    raise ValueError
                title = raw.get("title") or raw.get("name")
                if not isinstance(title, str) or not title.strip():
                    raise ValueError
                items.append(
                    DueItem(
                        key=DueSourceKey(
                            "ed", self.instance, course_id, "lesson",
                            _required_id(raw["id"], "lesson"),
                        ),
                        logical_course_key=logical_course_key,
                        course_label=course_label,
                        title=title.strip(),
                        due_at=due_at,
                        precision=DuePrecision.DATETIME,
                    )
                )
            except ValueError:
                issues.append(DueIssue("item_schema_drift", item_index=index))
        return DueBatch(
            provider="ed",
            instance=self.instance,
            course_id=course_id,
            coverage=DueCoverageStatus.PARTIAL if issues else DueCoverageStatus.COMPLETE,
            items=tuple(items),
            issues=tuple(issues),
        )

    def list_items(
        self,
        course_id: str,
        *,
        kinds: frozenset[RemoteKind],
    ) -> ContentBatch:
        if not isinstance(course_id, str) or not course_id.strip():
            raise ValueError("course_id must be a non-empty string")
        if not isinstance(kinds, frozenset) or not kinds.issubset(self.supported_kinds):
            raise ValueError("Ed supports only lesson and resource content")
        encoded_course_id = quote(course_id, safe="")
        output: list[RemoteItem] = []
        issues: list[ContentIssue] = []
        coverage: list[CategoryCoverage] = []
        staged_resources: dict[SourceKey, RemoteItem] | None = None
        staged_lessons: dict[SourceKey, RemoteItem] | None = None
        staged_snapshots: dict[SourceKey, dict[str, Any]] | None = None

        for kind in (RemoteKind.RESOURCE, RemoteKind.LESSON):
            if kind not in kinds:
                continue
            try:
                if kind is RemoteKind.RESOURCE:
                    found, found_issues, resource_items = self._list_resources(
                        course_id, encoded_course_id
                    )
                    staged_resources = resource_items
                else:
                    found, found_issues, lesson_items, lesson_snapshots = self._list_lessons(
                        course_id, encoded_course_id
                    )
                    staged_lessons = lesson_items
                    staged_snapshots = lesson_snapshots
            except ProviderError as error:
                if error.category == "authentication_failed":
                    raise
                issues.append(self._category_issue(course_id, kind, error))
                coverage.append(CategoryCoverage(kind, CoverageStatus.FAILED))
                continue

            output.extend(found)
            issues.extend(found_issues)
            coverage.append(
                CategoryCoverage(
                    kind,
                    CoverageStatus.PARTIAL if found_issues else CoverageStatus.COMPLETE,
                )
            )

        with self._cache_lock:
            if staged_resources is not None:
                self._replace_course_cache(self._resource_items, course_id, staged_resources)
            if staged_lessons is not None and staged_snapshots is not None:
                self._replace_course_cache(self._lesson_items, course_id, staged_lessons)
                self._replace_course_cache(self._lesson_snapshots, course_id, staged_snapshots)

        return ContentBatch(
            provider=self.provider_name,
            instance=self.instance,
            course_id=course_id,
            requested_kinds=kinds,
            coverage=tuple(coverage),
            items=tuple(output),
            issues=tuple(issues),
        )

    @staticmethod
    def _replace_course_cache(
        cache: dict[SourceKey, Any],
        course_id: str,
        replacement: dict[SourceKey, Any],
    ) -> None:
        for key in tuple(cache):
            if key.course_id == course_id:
                del cache[key]
        cache.update(replacement)

    def _category_issue(
        self,
        course_id: str,
        kind: RemoteKind,
        error: ProviderError,
    ) -> ContentIssue:
        is_schema = error.category == "schema_drift"
        is_authorization = error.category == "authorization_failed"
        return ContentIssue(
            provider=self.provider_name,
            instance=self.instance,
            course_id=course_id,
            kind=kind,
            scope=IssueScope.CATEGORY,
            code=(
                "category_schema_drift"
                if is_schema
                else "category_authorization_failed"
                if is_authorization
                else "category_request_failed"
            ),
            category=error.category,
            retry_required=not (is_schema or is_authorization),
        )

    def _item_issue(
        self,
        course_id: str,
        kind: RemoteKind,
        error: ProviderError,
        *,
        item_index: int,
        remote_id: str | None = None,
        detail: bool = False,
    ) -> ContentIssue:
        if error.category == "authorization_failed" and detail:
            code = "item_detail_forbidden"
        elif error.category == "request_failed" and detail:
            code = "item_detail_failed"
        else:
            code = "item_schema_drift"
        return ContentIssue(
            provider=self.provider_name,
            instance=self.instance,
            course_id=course_id,
            kind=kind,
            scope=IssueScope.ITEM,
            code=code,
            category=error.category,
            retry_required=error.category == "request_failed",
            remote_id=remote_id,
            item_index=item_index,
        )

    def _duplicate_issue(
        self,
        course_id: str,
        kind: RemoteKind,
        *,
        item_index: int,
        remote_id: str,
    ) -> ContentIssue:
        return ContentIssue(
            provider=self.provider_name,
            instance=self.instance,
            course_id=course_id,
            kind=kind,
            scope=IssueScope.ITEM,
            code="duplicate_remote_id",
            category="schema_drift",
            retry_required=False,
            remote_id=remote_id,
            item_index=item_index,
        )

    def _list_resources(
        self, course_id: str, encoded_course_id: str
    ) -> tuple[list[RemoteItem], list[ContentIssue], dict[SourceKey, RemoteItem]]:
        resources = self._json(f"/courses/{encoded_course_id}/resources").get("resources")
        if not isinstance(resources, list):
            raise ProviderError("schema_drift", "Ed resources response has no resource list")
        output: list[RemoteItem] = []
        issues: list[ContentIssue] = []
        staged: dict[SourceKey, RemoteItem] = {}
        seen_ids: set[str] = set()
        for index, resource in enumerate(resources):
            remote_id: str | None = None
            try:
                if not isinstance(resource, dict) or resource.get("id") is None:
                    raise ProviderError("schema_drift", "Ed resource entry has no stable id")
                remote_id = _required_id(resource["id"], "resource entry")
                if remote_id in seen_ids:
                    issues.append(
                        self._duplicate_issue(
                            course_id,
                            RemoteKind.RESOURCE,
                            item_index=index,
                            remote_id=remote_id,
                        )
                    )
                    continue
                seen_ids.add(remote_id)
                staff_only = resource.get("staff_only")
                if staff_only is not None and not isinstance(staff_only, bool):
                    raise ProviderError("schema_drift", "Ed resource staff-only flag is invalid")
                link = resource.get("link")
                if link is not None and not isinstance(link, str):
                    raise ProviderError("schema_drift", "Ed resource link is invalid")
                if staff_only is True or link:
                    continue
                name = _optional_text(resource.get("name"), "resource name") or f"resource_{remote_id}"
                extension = _optional_text(resource.get("extension"), "resource extension")
                if extension and not name.casefold().endswith(extension.casefold()):
                    name += extension
                raw_size = resource.get("size")
                if raw_size is None:
                    size = None
                elif isinstance(raw_size, bool) or not isinstance(raw_size, int) or raw_size < 0:
                    raise ProviderError("schema_drift", "Ed resource size is invalid")
                else:
                    size = raw_size
                item = RemoteItem(
                    key=SourceKey(
                        provider="ed",
                        instance=self.instance,
                        course_id=course_id,
                        kind=RemoteKind.RESOURCE,
                        remote_id=remote_id,
                    ),
                    signature=_resource_signature(resource),
                    display_name=name,
                    group=_optional_text(resource.get("category"), "resource category") or "Ed Resources",
                    size_bytes=size,
                    media_type=mimetypes.guess_type(name)[0],
                )
            except (ProviderError, ValueError) as caught:
                error = (
                    caught
                    if isinstance(caught, ProviderError)
                    else ProviderError("schema_drift", "Ed resource entry is invalid")
                )
                issues.append(
                    self._item_issue(
                        course_id,
                        RemoteKind.RESOURCE,
                        error,
                        item_index=index,
                        remote_id=remote_id,
                    )
                )
                continue
            staged[item.key] = item
            output.append(item)
        return output, issues, staged

    def _list_lessons(
        self, course_id: str, encoded_course_id: str
    ) -> tuple[
        list[RemoteItem],
        list[ContentIssue],
        dict[SourceKey, RemoteItem],
        dict[SourceKey, dict[str, Any]],
    ]:
        summaries = self._json(f"/courses/{encoded_course_id}/lessons").get("lessons")
        if not isinstance(summaries, list):
            raise ProviderError("schema_drift", "Ed lessons response has no lesson list")
        output: list[RemoteItem] = []
        issues: list[ContentIssue] = []
        staged_items: dict[SourceKey, RemoteItem] = {}
        staged_snapshots: dict[SourceKey, dict[str, Any]] = {}
        seen_ids: set[str] = set()
        for index, summary in enumerate(summaries):
            remote_id: str | None = None
            try:
                if not isinstance(summary, dict) or summary.get("id") is None:
                    raise ProviderError("schema_drift", "Ed lesson entry has no stable id")
                remote_id = _required_id(summary["id"], "lesson entry")
                if remote_id in seen_ids:
                    issues.append(
                        self._duplicate_issue(
                            course_id,
                            RemoteKind.LESSON,
                            item_index=index,
                            remote_id=remote_id,
                        )
                    )
                    continue
                seen_ids.add(remote_id)
            except (ProviderError, ValueError) as caught:
                error = (
                    caught
                    if isinstance(caught, ProviderError)
                    else ProviderError("schema_drift", "Ed lesson entry is invalid")
                )
                issues.append(
                    self._item_issue(
                        course_id,
                        RemoteKind.LESSON,
                        error,
                        item_index=index,
                        remote_id=remote_id,
                    )
                )
                continue

            try:
                payload = self._json(f"/lessons/{quote(remote_id, safe='')}")
                lesson = payload.get("lesson")
                if not isinstance(lesson, dict) or lesson.get("id") is None:
                    raise ProviderError("schema_drift", "Ed lesson detail has no stable id")
                if _required_id(lesson["id"], "lesson detail") != remote_id:
                    raise ProviderError("schema_drift", "Ed lesson detail id does not match")
                title = _optional_text(lesson.get("title"), "lesson title") or "Ed Lesson"
                _lesson_body(lesson)
                key = SourceKey(
                    provider="ed",
                    instance=self.instance,
                    course_id=course_id,
                    kind=RemoteKind.LESSON,
                    remote_id=remote_id,
                )
                item = RemoteItem(
                    key=key,
                    signature=_semantic_lesson_signature(lesson),
                    display_name=f"{title}.html",
                    group="Ed Lessons",
                    media_type="text/html",
                )
            except (ProviderError, ValueError) as caught:
                error = (
                    caught
                    if isinstance(caught, ProviderError)
                    else ProviderError("schema_drift", "Ed lesson detail is invalid")
                )
                if error.category == "authentication_failed":
                    raise
                issues.append(
                    self._item_issue(
                        course_id,
                        RemoteKind.LESSON,
                        error,
                        item_index=index,
                        remote_id=remote_id,
                        detail=True,
                    )
                )
                continue
            staged_items[item.key] = item
            staged_snapshots[item.key] = lesson
            output.append(item)
        return output, issues, staged_items, staged_snapshots

    def _validate_item(self, item: RemoteItem, kind: RemoteKind) -> None:
        if not isinstance(item, RemoteItem):
            raise ValueError("item must be a RemoteItem")
        key = item.key
        if (
            key.provider != self.provider_name
            or key.instance != self.instance
            or key.kind is not kind
        ):
            raise ValueError("item does not belong to this Ed provider operation")

    @contextmanager
    def open_binary(
        self,
        item: RemoteItem,
        *,
        chunk_size: int = 64 * 1024,
    ) -> Iterator[Iterator[bytes]]:
        self._validate_item(item, RemoteKind.RESOURCE)
        if isinstance(chunk_size, bool) or not isinstance(chunk_size, int) or chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        discovered = self._resource_items.get(item.key)
        if discovered != item:
            raise ValueError("resource item was not produced by current Ed discovery")
        remote_id = quote(item.key.remote_id, safe="")
        filename = quote(item.display_name, safe="") or "file"
        endpoint = f"{self.base_url}/resources/{remote_id}/download/{filename}"
        try:
            with self._client.stream(
                "POST", endpoint, headers=self._headers, follow_redirects=False
            ) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise ProviderError("request_failed", "Ed resource redirect is invalid")
                    asset_url = urljoin(str(response.url), location)
                    with self._asset_stream(asset_url) as asset_response:
                        asset_response.raise_for_status()
                        yield asset_response.iter_bytes(chunk_size=chunk_size)
                    return
                response.raise_for_status()
                yield response.iter_bytes(chunk_size=chunk_size)
        except ProviderError:
            raise
        except httpx.HTTPError:
            raise ProviderError("request_failed", "Ed resource download failed") from None

    def render_html(self, item: RemoteItem) -> RenderedHtml:
        self._validate_item(item, RemoteKind.LESSON)
        if self._lesson_items.get(item.key) != item:
            raise ProviderError("stale_item", "Ed lesson item is not current")
        lesson = self._lesson_snapshots.get(item.key)
        if lesson is None:
            raise ProviderError("stale_item", "Ed lesson snapshot is unavailable")
        if _semantic_lesson_signature(lesson) != item.signature:
            raise ProviderError("stale_item", "Ed lesson changed after discovery")

        body, slide_issues = _lesson_body(lesson)
        soup = BeautifulSoup(body, "html.parser")
        references: list[str] = []
        for image in soup.find_all("img"):
            reference = image.get("src")
            if isinstance(reference, str) and reference and reference not in references:
                references.append(reference)
        resolutions = tuple(self._resolve_image(reference) for reference in references)
        rendered = render_page_html(
            title=str(lesson.get("title") or "Ed Lesson"),
            public_source_url=f"{self.base_url}/lessons/{quote(item.key.remote_id, safe='')}",
            body_html=body,
            assets=resolutions,
        )
        merged_issues = tuple(dict.fromkeys((*slide_issues, *rendered.issue_codes)))
        return RenderedHtml(rendered.document, rendered.retry_required, merged_issues)

    @contextmanager
    def _asset_stream(
        self,
        url: str,
        *,
        redirects_remaining: int = 3,
    ) -> Iterator[httpx.Response]:
        self._validate_asset_url(url)
        with self._asset_client.stream("GET", url, follow_redirects=False) as response:
            if response.is_redirect:
                if redirects_remaining == 0:
                    raise ProviderError("unsafe_redirect", "Ed asset redirect limit exceeded")
                location = response.headers.get("location")
                if not location:
                    raise ProviderError("request_failed", "Ed asset redirect is invalid")
                with self._asset_stream(
                    urljoin(str(response.url), location),
                    redirects_remaining=redirects_remaining - 1,
                ) as redirected:
                    yield redirected
                return
            yield response

    def _validate_asset_url(self, url: str) -> None:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").casefold()
        if (
            parsed.scheme != "https"
            or not hostname
            or parsed.username
            or parsed.password
            or hostname not in self._allowed_asset_hosts
            or hostname == "localhost"
        ):
            raise ProviderError("unsafe_redirect", "Ed asset URL is unsafe")
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            return
        if not address.is_global:
            raise ProviderError("unsafe_redirect", "Ed asset URL is unsafe")

    def _resolve_image(self, reference: str) -> HtmlAssetResolution:
        parsed = urlparse(reference)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            return HtmlAssetResolution(
                original_reference=reference,
                disposition=AssetDisposition.MISSING_IMAGE,
                issue_code="unsafe_image_url",
            )
        try:
            with self._asset_stream(reference) as response:
                response.raise_for_status()
                media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().casefold()
                if not media_type.startswith("image/"):
                    return HtmlAssetResolution(
                        original_reference=reference,
                        disposition=AssetDisposition.MISSING_IMAGE,
                        issue_code="not_an_image",
                    )
                raw_length = response.headers.get("content-length")
                if raw_length is not None:
                    try:
                        declared_length = int(raw_length)
                    except ValueError:
                        declared_length = -1
                    if declared_length < 0 or declared_length > self._max_inline_image_bytes:
                        return HtmlAssetResolution(
                            original_reference=reference,
                            disposition=AssetDisposition.MISSING_IMAGE,
                            issue_code="image_size_limit_exceeded",
                        )
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > self._max_inline_image_bytes:
                        return HtmlAssetResolution(
                            original_reference=reference,
                            disposition=AssetDisposition.MISSING_IMAGE,
                            issue_code="image_size_limit_exceeded",
                        )
                    chunks.append(chunk)
                return HtmlAssetResolution(
                    original_reference=reference,
                    disposition=AssetDisposition.INLINE_IMAGE,
                    content=b"".join(chunks),
                    media_type=media_type,
                )
        except ProviderError as exc:
            return HtmlAssetResolution(
                original_reference=reference,
                disposition=AssetDisposition.MISSING_IMAGE,
                retry_required=exc.category == "request_failed",
                issue_code=(
                    "unsafe_image_url"
                    if exc.category == "unsafe_redirect"
                    else "image_request_failed"
                ),
            )
        except httpx.HTTPError:
            return HtmlAssetResolution(
                original_reference=reference,
                disposition=AssetDisposition.MISSING_IMAGE,
                retry_required=True,
                issue_code="image_request_failed",
            )
