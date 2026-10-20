"""Canvas course catalog adapter with opaque, same-instance pagination."""

from __future__ import annotations

import hashlib
import html
import ipaddress
import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from urllib.parse import (
    parse_qsl,
    quote,
    urlencode,
    urljoin,
    urlparse,
    urlsplit,
    urlunsplit,
)

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


_FILE_REFERENCE = re.compile(r"/files/(\d+)(?:/download)?", re.IGNORECASE)
_URL_IN_TEXT = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_VOLATILE_QUERY_NAMES = frozenset(
    {
        "access_token", "api_key", "credential", "expires", "expiry", "key",
        "policy", "se", "sig", "signature", "token", "verifier",
    }
)
_MAX_INLINE_IMAGE_BYTES = 25 * 1024 * 1024
_CANVAS_USER_CONTENT_SUFFIX = ".canvas-user-content.com"
_INSCLOUDGATE_SUFFIX = ".inscloudgate.net"


def _origin(url: str | httpx.URL) -> tuple[str, str, int | None]:
    parsed = urlparse(str(url))
    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme.casefold() == "https" else 80
    return parsed.scheme.casefold(), (parsed.hostname or "").casefold(), port


def _signature(value: object) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _canonicalize_signed_urls(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): _canonicalize_signed_urls(item) for key, item in value.items()}
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
                sorted(
                    (
                        (key, item)
                        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
                        if key.casefold() not in _VOLATILE_QUERY_NAMES
                        and not key.casefold().startswith(("x-amz-", "x-goog-"))
                    ),
                    key=lambda pair: (pair[0], pair[1]),
                ),
                doseq=True,
            )
            return urlunsplit((parsed.scheme, netloc, parsed.path, query, ""))

        return _URL_IN_TEXT.sub(replace, value)
    return value


def _required_id(value: object, label: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ProviderError("schema_drift", f"Canvas {label} has no stable id")
    result = str(value)
    if not result or result != result.strip() or any(
        ord(character) < 32 or ord(character) == 127 for character in result
    ):
        raise ProviderError("schema_drift", f"Canvas {label} has no stable id")
    return result


def _safe_text(value: object, label: str, *, fallback: str | None = None) -> str:
    if value is None and fallback is not None:
        return fallback
    if not isinstance(value, str):
        raise ProviderError("schema_drift", f"Canvas {label} is invalid")
    if value != value.strip() or any(
        ord(character) < 32 or ord(character) == 127 for character in value
    ):
        raise ProviderError("schema_drift", f"Canvas {label} is invalid")
    return value or (fallback or "")


def _file_signature(metadata: dict[str, Any]) -> str:
    return _signature(
        _canonicalize_signed_urls(
            {
                key: metadata.get(key)
                for key in (
                    "id", "filename", "display_name", "size", "updated_at",
                    "modified_at", "content-type", "url",
                )
            }
        )
    )


def _page_signature(page: dict[str, Any]) -> str:
    return _signature(
        _canonicalize_signed_urls(
            {
                key: page.get(key)
                for key in ("page_id", "url", "title", "updated_at", "body")
            }
        )
    )


class _CanvasCatalogProvider:
    provider_name = "canvas"

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        normalized = base_url.rstrip("/")
        parsed = urlparse(normalized)
        if parsed.scheme != "https" or not parsed.hostname or parsed.query or parsed.fragment:
            raise ValueError("Canvas base URL must be an HTTPS URL without query or fragment")
        if not token:
            raise ValueError("Canvas token is required")
        install_http_redaction_filters()
        self.base_url = normalized
        host = parsed.hostname.casefold()
        self.instance = host if parsed.port in {None, 443} else f"{host}:{parsed.port}"
        self._origin = _origin(normalized)
        self._client = client or httpx.Client(timeout=30.0, follow_redirects=False)
        self._headers = {"Authorization": f"Bearer {token}"}

    def close(self) -> None:
        self._client.close()

    def list_courses(self) -> tuple[CourseCandidate, ...]:
        url: str | httpx.URL = f"{self.base_url}/courses"
        params: dict[str, object] | None = {
            "enrollment_state[]": "active",
            "include[]": "term",
            "per_page": 100,
        }
        courses: list[CourseCandidate] = []
        seen_urls: set[str] = set()
        while url:
            url_text = str(url)
            if url_text in seen_urls:
                raise ProviderError("pagination_loop", "Canvas pagination repeated a URL")
            seen_urls.add(url_text)
            if _origin(url) != self._origin:
                raise ProviderError(
                    "unsafe_pagination", "Canvas pagination left the configured instance"
                )
            try:
                response = self._client.get(url, params=params, headers=self._headers)
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise ProviderError("request_failed", "Canvas course listing failed") from exc
            if not isinstance(payload, list):
                raise ProviderError("schema_drift", "Canvas courses response is not a list")
            for item in payload:
                if not isinstance(item, dict) or item.get("id") is None:
                    raise ProviderError("schema_drift", "Canvas course entry has no stable id")
                term = item.get("term")
                term_name = term.get("name") if isinstance(term, dict) else None
                course_id = str(item["id"])
                courses.append(
                    CourseCandidate(
                        provider="canvas",
                        instance=self.instance,
                        course_id=course_id,
                        code=str(item.get("course_code") or ""),
                        name=str(item.get("name") or item.get("course_code") or course_id),
                        term=str(term_name) if term_name is not None else None,
                    )
                )
            next_link = response.links.get("next")
            url = next_link.get("url") if next_link else ""
            params = None
        return tuple(courses)

    def list_due_items(
        self,
        course_id: str,
        *,
        logical_course_key: str,
        course_label: str,
    ):
        """Return dated Canvas assignments; undated entries are intentionally omitted."""
        from datetime import datetime
        from decimal import Decimal, InvalidOperation

        from ..due.model import (
            DueBatch,
            DueCoverageStatus,
            DueIssue,
            DueItem,
            DuePrecision,
            DueSourceKey,
        )

        if not course_id.strip():
            raise ValueError("course_id must be non-empty")
        payload = self._api_list(
            f"{self.base_url}/courses/{quote(course_id, safe='')}/assignments",
            {"order_by": "due_at", "per_page": 100},
        )
        items: list[DueItem] = []
        issues: list[DueIssue] = []
        for index, raw in enumerate(payload):
            try:
                if not isinstance(raw, dict) or raw.get("id") is None:
                    raise ValueError
                raw_due = raw.get("due_at")
                if raw_due is None:
                    continue
                if not isinstance(raw_due, str):
                    raise ValueError
                due_at = datetime.fromisoformat(raw_due.replace("Z", "+00:00"))
                if due_at.tzinfo is None:
                    raise ValueError
                title = raw.get("name")
                if not isinstance(title, str) or not title.strip():
                    raise ValueError
                raw_points = raw.get("points_possible")
                if isinstance(raw_points, bool):
                    raise ValueError
                points = None if raw_points is None else Decimal(str(raw_points))
                if points is not None and points < 0:
                    raise ValueError
                category = "quiz" if raw.get("is_quiz_assignment") is True else "assignment"
                items.append(
                    DueItem(
                        key=DueSourceKey(
                            "canvas", self.instance, course_id, category,
                            _required_id(raw["id"], "assignment"),
                        ),
                        logical_course_key=logical_course_key,
                        course_label=course_label,
                        title=title.strip(),
                        due_at=due_at,
                        precision=DuePrecision.DATETIME,
                        points=points,
                    )
                )
            except (ValueError, InvalidOperation):
                issues.append(DueIssue("item_schema_drift", item_index=index))
        return DueBatch(
            provider="canvas",
            instance=self.instance,
            course_id=course_id,
            coverage=DueCoverageStatus.PARTIAL if issues else DueCoverageStatus.COMPLETE,
            items=tuple(items),
            issues=tuple(issues),
        )


class CanvasProvider(_CanvasCatalogProvider):
    """Canvas FILE/PAGE adapter; raw metadata remains discovery-bound."""

    supported_kinds = frozenset({RemoteKind.FILE, RemoteKind.PAGE})

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        client: httpx.Client | None = None,
        asset_client: httpx.Client | None = None,
        allowed_asset_hosts: frozenset[str] | None = None,
        max_inline_image_bytes: int = _MAX_INLINE_IMAGE_BYTES,
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
            raise ValueError("Canvas base URL must be an HTTPS URL without credentials or query")
        if not token:
            raise ValueError("Canvas token is required")
        install_http_redaction_filters()
        if client is not None and client is asset_client:
            raise ValueError("Canvas API and asset clients must be separate")
        if asset_client is not None:
            unsafe_headers = {"authorization", "cookie", "proxy-authorization"}
            if any(name in asset_client.headers for name in unsafe_headers):
                raise ValueError("Canvas asset client cannot have credential headers")
            if asset_client.auth is not None or asset_client.cookies:
                raise ValueError("Canvas asset client cannot have auth or cookies")
            if any(asset_client.event_hooks.values()):
                raise ValueError("Canvas asset client cannot have event hooks")
        if (
            isinstance(max_inline_image_bytes, bool)
            or not isinstance(max_inline_image_bytes, int)
            or max_inline_image_bytes <= 0
        ):
            raise ValueError("max_inline_image_bytes must be positive")

        self.base_url = normalized
        host = parsed.hostname.casefold()
        self.instance = host if parsed.port in {None, 443} else f"{host}:{parsed.port}"
        self._origin = _origin(normalized)
        self._public_origin = f"https://{self.instance}"
        if allowed_asset_hosts is None:
            allowed_asset_hosts = frozenset({host})
        if not isinstance(allowed_asset_hosts, frozenset) or not allowed_asset_hosts:
            raise ValueError("Canvas asset hosts must be a non-empty frozenset")
        normalized_hosts: set[str] = {host}
        for allowed_host in allowed_asset_hosts:
            if (
                not isinstance(allowed_host, str)
                or not allowed_host
                or allowed_host != allowed_host.casefold()
                or any(character in allowed_host for character in "/\\:?#@[]")
                or allowed_host == "localhost"
            ):
                raise ValueError("Canvas asset hosts must be exact canonical hostnames")
            try:
                ipaddress.ip_address(allowed_host)
            except ValueError:
                pass
            else:
                raise ValueError("Canvas external asset hosts cannot be IP literals")
            normalized_hosts.add(allowed_host)
        self._allowed_asset_hosts = frozenset(normalized_hosts)
        self._client = client or httpx.Client(timeout=30.0, follow_redirects=False)
        self._asset_client = asset_client or httpx.Client(
            timeout=30.0, follow_redirects=False
        )
        self._headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        self._max_inline_image_bytes = max_inline_image_bytes
        self._file_items: dict[SourceKey, RemoteItem] = {}
        self._file_metadata: dict[SourceKey, dict[str, Any]] = {}
        self._page_items: dict[SourceKey, RemoteItem] = {}
        self._page_snapshots: dict[SourceKey, dict[str, Any]] = {}

    def close(self) -> None:
        self._file_items.clear()
        self._file_metadata.clear()
        self._page_items.clear()
        self._page_snapshots.clear()
        try:
            self._asset_client.close()
        finally:
            self._client.close()

    def _api_url(self, endpoint: str | httpx.URL) -> str:
        value = str(endpoint)
        url = value if urlparse(value).scheme else urljoin(f"{self.base_url}/", value)
        if _origin(url) != self._origin:
            raise ProviderError("unsafe_pagination", "Canvas API URL left the configured instance")
        return url

    def _api_response(
        self,
        endpoint: str | httpx.URL,
        *,
        params: dict[str, object] | None = None,
    ) -> httpx.Response:
        url = self._api_url(endpoint)
        try:
            response = self._client.get(url, params=params, headers=self._headers)
        except httpx.HTTPError:
            raise ProviderError("request_failed", "Canvas API request failed") from None
        if response.status_code == 401:
            raise ProviderError("authentication_failed", "Canvas API authentication failed")
        if response.status_code == 403:
            raise ProviderError("authorization_failed", "Canvas API request was not authorized")
        try:
            response.raise_for_status()
        except httpx.HTTPError:
            raise ProviderError("request_failed", "Canvas API request failed") from None
        return response

    def _api_json(self, endpoint: str | httpx.URL) -> object:
        response = self._api_response(endpoint)
        try:
            return response.json()
        except ValueError:
            raise ProviderError("schema_drift", "Canvas API response is not valid JSON") from None

    def _api_list(
        self,
        endpoint: str | httpx.URL,
        params: dict[str, object] | None = None,
    ) -> list[object]:
        url: str | httpx.URL = endpoint
        output: list[object] = []
        seen: set[str] = set()
        while url:
            canonical = self._api_url(url)
            if canonical in seen:
                raise ProviderError("pagination_loop", "Canvas pagination repeated a URL")
            seen.add(canonical)
            response = self._api_response(canonical, params=params)
            params = None
            try:
                payload = response.json()
            except ValueError:
                raise ProviderError("schema_drift", "Canvas API response is not valid JSON") from None
            if not isinstance(payload, list):
                raise ProviderError("schema_drift", "Canvas list response is not a list")
            output.extend(payload)
            next_link = response.links.get("next")
            url = next_link.get("url") if next_link else ""
        return output

    def list_courses(self) -> tuple[CourseCandidate, ...]:
        payload = self._api_list(
            f"{self.base_url}/courses",
            {
                "enrollment_state[]": "active",
                "include[]": "term",
                "per_page": 100,
            },
        )
        courses: list[CourseCandidate] = []
        for item in payload:
            if not isinstance(item, dict) or item.get("id") is None:
                raise ProviderError("schema_drift", "Canvas course entry has no stable id")
            term = item.get("term")
            term_name = term.get("name") if isinstance(term, dict) else None
            course_id = _required_id(item["id"], "course entry")
            courses.append(
                CourseCandidate(
                    provider="canvas",
                    instance=self.instance,
                    course_id=course_id,
                    code=str(item.get("course_code") or ""),
                    name=str(item.get("name") or item.get("course_code") or course_id),
                    term=str(term_name) if term_name is not None else None,
                )
            )
        return tuple(courses)

    def _file_item(
        self, metadata: dict[str, Any], *, course_id: str, group: str
    ) -> RemoteItem:
        remote_id = _required_id(metadata.get("id"), "file")
        name = _safe_text(
            metadata.get("display_name") or metadata.get("filename"),
            "file name",
            fallback=f"canvas_file_{remote_id}",
        )
        size = metadata.get("size")
        if isinstance(size, bool) or (
            size is not None and (not isinstance(size, int) or size < 0)
        ):
            raise ProviderError("schema_drift", "Canvas file size is invalid")
        media = metadata.get("content-type")
        if media is not None and not isinstance(media, str):
            raise ProviderError("schema_drift", "Canvas file media type is invalid")
        return RemoteItem(
            SourceKey("canvas", self.instance, course_id, RemoteKind.FILE, remote_id),
            _file_signature(metadata),
            name,
            group=group,
            size_bytes=size,
            media_type=media,
        )

    def _page_item(
        self, page: dict[str, Any], *, course_id: str, group: str
    ) -> RemoteItem:
        _required_id(page.get("page_id"), "page")
        remote_id = _safe_text(page.get("url"), "page URL")
        title = _safe_text(page.get("title"), "page title", fallback="Canvas Page")
        body = page.get("body")
        if body is not None and not isinstance(body, str):
            raise ProviderError("schema_drift", "Canvas page body is invalid")
        return RemoteItem(
            SourceKey("canvas", self.instance, course_id, RemoteKind.PAGE, remote_id),
            _page_signature(page),
            f"{title}.html",
            group=group,
            media_type="text/html",
        )

    def _issue(
        self,
        *,
        course_id: str,
        kind: RemoteKind,
        code: str,
        item_index: int | None,
        category: str | None = None,
        retry_required: bool | None = None,
    ) -> ContentIssue:
        resolved_category = category or (
            "schema_drift" if code == "item_schema_drift" else code
        )
        resolved_retry = (
            retry_required
            if retry_required is not None
            else resolved_category not in {"item_schema_drift", "schema_drift"}
        )
        return ContentIssue(
            provider="canvas",
            instance=self.instance,
            course_id=course_id,
            kind=kind,
            scope=IssueScope.ITEM if item_index is not None else IssueScope.CATEGORY,
            code=code,
            category=resolved_category,
            retry_required=resolved_retry,
            item_index=item_index,
        )

    def _replace_course_cache(
        self,
        target: dict[SourceKey, Any],
        course_id: str,
        staged: dict[SourceKey, Any],
    ) -> None:
        for key in tuple(target):
            if key.course_id == course_id:
                del target[key]
        target.update(staged)

    def list_items(
        self,
        course_id: str,
        *,
        kinds: frozenset[RemoteKind],
    ) -> ContentBatch:
        if not isinstance(course_id, str) or not course_id.strip():
            raise ValueError("course_id must be a non-empty string")
        if not isinstance(kinds, frozenset) or not kinds.issubset(self.supported_kinds):
            raise ValueError("Canvas supports only file and page content")
        if not kinds:
            return ContentBatch("canvas", self.instance, course_id, kinds, (), (), ())

        encoded_course = quote(course_id, safe="")
        staged_items: list[RemoteItem] = []
        staged_files: dict[SourceKey, dict[str, Any]] = {}
        staged_file_items: dict[SourceKey, RemoteItem] = {}
        staged_pages: dict[SourceKey, dict[str, Any]] = {}
        staged_page_items: dict[SourceKey, RemoteItem] = {}
        issues: list[ContentIssue] = []
        failed: set[RemoteKind] = set()
        item_index = 0

        try:
            modules = self._api_list(
                f"{self.base_url}/courses/{encoded_course}/modules"
            )
        except ProviderError as exc:
            if exc.category == "authentication_failed":
                raise
            modules = []
            if RemoteKind.PAGE in kinds:
                failed.add(RemoteKind.PAGE)
                issues.append(
                    self._issue(
                        course_id=course_id,
                        kind=RemoteKind.PAGE,
                        code=exc.category,
                        item_index=None,
                    )
                )

        for module in modules:
            if failed == set(kinds):
                break
            if not isinstance(module, dict):
                if RemoteKind.PAGE in kinds:
                    failed.add(RemoteKind.PAGE)
                    issues.append(
                        self._issue(
                            course_id=course_id,
                            kind=RemoteKind.PAGE,
                            code="schema_drift",
                            item_index=None,
                        )
                    )
                break
            try:
                module_id = _required_id(module.get("id"), "module")
                group = _safe_text(
                    module.get("name"), "module name", fallback="Canvas Module"
                )
                module_items = self._api_list(
                    f"{self.base_url}/courses/{encoded_course}/modules/"
                    f"{quote(module_id, safe='')}/items"
                )
            except ProviderError as exc:
                if RemoteKind.PAGE in kinds:
                    failed.add(RemoteKind.PAGE)
                    issues.append(
                        self._issue(
                            course_id=course_id,
                            kind=RemoteKind.PAGE,
                            code=exc.category,
                            item_index=None,
                        )
                    )
                break

            for module_item in module_items:
                current_index = item_index
                item_index += 1
                if not isinstance(module_item, dict):
                    continue
                item_type = module_item.get("type")
                if (
                    item_type == "File"
                    and RemoteKind.FILE in kinds
                    and RemoteKind.FILE not in failed
                ):
                    try:
                        endpoint = _safe_text(module_item.get("url"), "file API URL")
                        metadata = self._api_json(endpoint)
                        if not isinstance(metadata, dict):
                            raise ProviderError(
                                "schema_drift", "Canvas file response is invalid"
                            )
                        item = self._file_item(
                            metadata, course_id=course_id, group=group
                        )
                    except ProviderError as exc:
                        if exc.category == "authentication_failed":
                            raise
                        issues.append(
                            self._issue(
                                course_id=course_id,
                                kind=RemoteKind.FILE,
                                code=(
                                    "item_schema_drift"
                                    if exc.category == "schema_drift"
                                    else "item_request_failed"
                                ),
                                category=exc.category,
                                retry_required=exc.category not in {
                                    "schema_drift", "authorization_failed"
                                },
                                item_index=current_index,
                            )
                        )
                        continue
                    if item.key not in staged_file_items:
                        staged_file_items[item.key] = item
                        staged_files[item.key] = dict(metadata)
                        staged_items.append(item)
                elif (
                    item_type == "Page"
                    and RemoteKind.PAGE in kinds
                    and RemoteKind.PAGE not in failed
                ):
                    try:
                        endpoint = _safe_text(module_item.get("url"), "page API URL")
                        page = self._api_json(endpoint)
                        if not isinstance(page, dict):
                            raise ProviderError(
                                "schema_drift", "Canvas page response is invalid"
                            )
                        item = self._page_item(page, course_id=course_id, group=group)
                    except ProviderError as exc:
                        if exc.category == "authentication_failed":
                            raise
                        issues.append(
                            self._issue(
                                course_id=course_id,
                                kind=RemoteKind.PAGE,
                                code=(
                                    "item_schema_drift"
                                    if exc.category == "schema_drift"
                                    else "item_request_failed"
                                ),
                                category=exc.category,
                                retry_required=exc.category not in {
                                    "schema_drift", "authorization_failed"
                                },
                                item_index=current_index,
                            )
                        )
                        continue
                    if item.key not in staged_page_items:
                        staged_page_items[item.key] = item
                        staged_pages[item.key] = dict(page)
                        staged_items.append(item)

        if RemoteKind.FILE in kinds and RemoteKind.FILE not in failed:
            try:
                all_files = self._api_list(
                    f"{self.base_url}/courses/{encoded_course}/files",
                    {"sort": "updated_at", "order": "desc", "per_page": 100},
                )
            except ProviderError as exc:
                if not staged_file_items:
                    failed.add(RemoteKind.FILE)
                issues.append(
                    self._issue(
                        course_id=course_id,
                        kind=RemoteKind.FILE,
                        code=exc.category,
                        item_index=None,
                        retry_required=exc.category != "authorization_failed",
                    )
                )
            else:
                for metadata in all_files:
                    current_index = item_index
                    item_index += 1
                    try:
                        if not isinstance(metadata, dict):
                            raise ProviderError(
                                "schema_drift", "Canvas file entry is invalid"
                            )
                        item = self._file_item(
                            metadata,
                            course_id=course_id,
                            group="Course Files",
                        )
                    except ProviderError:
                        issues.append(
                            self._issue(
                                course_id=course_id,
                                kind=RemoteKind.FILE,
                                code="item_schema_drift",
                                item_index=current_index,
                            )
                        )
                        continue
                    if item.key not in staged_file_items:
                        staged_file_items[item.key] = item
                        staged_files[item.key] = dict(metadata)
                        staged_items.append(item)

        coverage: list[CategoryCoverage] = []
        for kind in sorted(kinds, key=lambda value: value.value):
            if kind in failed:
                status = CoverageStatus.FAILED
            elif any(issue.kind is kind for issue in issues):
                status = CoverageStatus.PARTIAL
            else:
                status = CoverageStatus.COMPLETE
            coverage.append(CategoryCoverage(kind, status))

        if RemoteKind.FILE in kinds and RemoteKind.FILE not in failed:
            self._replace_course_cache(
                self._file_items, course_id, staged_file_items
            )
            self._replace_course_cache(
                self._file_metadata, course_id, staged_files
            )
        if RemoteKind.PAGE in kinds and RemoteKind.PAGE not in failed:
            self._replace_course_cache(
                self._page_items, course_id, staged_page_items
            )
            self._replace_course_cache(
                self._page_snapshots, course_id, staged_pages
            )

        return ContentBatch(
            provider="canvas",
            instance=self.instance,
            course_id=course_id,
            requested_kinds=kinds,
            coverage=tuple(coverage),
            items=tuple(staged_items),
            issues=tuple(issues),
        )

    def _validate_item(self, item: RemoteItem, kind: RemoteKind) -> None:
        if not isinstance(item, RemoteItem) or (
            item.key.provider != "canvas"
            or item.key.instance != self.instance
            or item.key.kind is not kind
        ):
            raise ProviderError(
                "forged_item", "Canvas item was not discovered by this provider"
            )

    def _validate_asset_url(
        self,
        url: str,
        *,
        allow_canvas_user_content: bool = False,
        allow_inscloudgate: bool = False,
    ) -> tuple[str, str, int | None]:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").casefold()
        trusted_host = hostname in self._allowed_asset_hosts or (
            allow_canvas_user_content
            and hostname.endswith(_CANVAS_USER_CONTENT_SUFFIX)
        ) or (
            allow_inscloudgate
            and hostname.endswith(_INSCLOUDGATE_SUFFIX)
        )
        if (
            parsed.scheme != "https"
            or not hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
            or hostname == "localhost"
            or not trusted_host
            or parsed.port not in {None, 443}
        ):
            raise ProviderError("unsafe_redirect", "Canvas asset URL is unsafe")
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            return _origin(url)
        if not address.is_global:
            raise ProviderError("unsafe_redirect", "Canvas asset URL is unsafe")
        return _origin(url)

    @contextmanager
    def _asset_stream(
        self,
        url: str,
        *,
        delegated_origin: tuple[str, str, int | None] | None = None,
        inscloudgate_delegation_used: bool = False,
        redirects_remaining: int = 3,
    ) -> Iterator[httpx.Response]:
        delegated_host = delegated_origin[1] if delegated_origin is not None else ""
        current_origin = self._validate_asset_url(
            url,
            allow_canvas_user_content=delegated_host.endswith(
                _CANVAS_USER_CONTENT_SUFFIX
            ),
            allow_inscloudgate=delegated_host.endswith(_INSCLOUDGATE_SUFFIX),
        )
        if delegated_origin is not None and current_origin != delegated_origin:
            raise ProviderError(
                "unsafe_redirect", "Canvas asset redirect changed origin"
            )
        try:
            with self._asset_client.stream(
                "GET", url, follow_redirects=False
            ) as response:
                if response.is_redirect:
                    if redirects_remaining == 0:
                        raise ProviderError(
                            "unsafe_redirect", "Canvas asset redirect limit exceeded"
                        )
                    location = response.headers.get("location")
                    if not location:
                        raise ProviderError(
                            "request_failed", "Canvas asset redirect is invalid"
                        )
                    redirected_url = urljoin(str(response.url), location)
                    current_host = current_origin[1]
                    may_delegate_to_user_content = (
                        delegated_origin is None and current_origin == self._origin
                    )
                    may_delegate_to_inscloudgate = (
                        delegated_origin == current_origin
                        and current_host.endswith(_CANVAS_USER_CONTENT_SUFFIX)
                    )
                    may_delegate_within_inscloudgate = (
                        delegated_origin == current_origin
                        and current_host.endswith(_INSCLOUDGATE_SUFFIX)
                        and not inscloudgate_delegation_used
                    )
                    redirected_origin = self._validate_asset_url(
                        redirected_url,
                        allow_canvas_user_content=(
                            current_host.endswith(_CANVAS_USER_CONTENT_SUFFIX)
                            or may_delegate_to_user_content
                        ),
                        allow_inscloudgate=(
                            current_host.endswith(_INSCLOUDGATE_SUFFIX)
                            or may_delegate_to_inscloudgate
                        ),
                    )
                    allowed = current_origin
                    redirected_host = redirected_origin[1]
                    next_inscloudgate_delegation_used = (
                        inscloudgate_delegation_used
                    )
                    if (
                        may_delegate_to_user_content
                        and redirected_host.endswith(_CANVAS_USER_CONTENT_SUFFIX)
                    ) or (
                        may_delegate_to_inscloudgate
                        and redirected_host.endswith(_INSCLOUDGATE_SUFFIX)
                    ):
                        allowed = redirected_origin
                    elif (
                        may_delegate_within_inscloudgate
                        and redirected_origin != current_origin
                        and redirected_host.endswith(_INSCLOUDGATE_SUFFIX)
                    ):
                        allowed = redirected_origin
                        next_inscloudgate_delegation_used = True
                    with self._asset_stream(
                        redirected_url,
                        delegated_origin=allowed,
                        inscloudgate_delegation_used=(
                            next_inscloudgate_delegation_used
                        ),
                        redirects_remaining=redirects_remaining - 1,
                    ) as redirected:
                        yield redirected
                    return
                yield response
        except ProviderError:
            raise
        except httpx.HTTPError:
            raise ProviderError(
                "request_failed", "Canvas asset request failed"
            ) from None

    @contextmanager
    def open_binary(
        self,
        item: RemoteItem,
        *,
        chunk_size: int = 64 * 1024,
    ) -> Iterator[Iterator[bytes]]:
        self._validate_item(item, RemoteKind.FILE)
        if self._file_items.get(item.key) != item:
            if item.key not in self._file_items:
                raise ProviderError(
                    "forged_item", "Canvas item was not discovered by this provider"
                )
            raise ProviderError("stale_item", "Canvas file item is not current")
        metadata = self._file_metadata.get(item.key)
        if metadata is None or _file_signature(metadata) != item.signature:
            raise ProviderError("stale_item", "Canvas file metadata is unavailable")
        url = metadata.get("url")
        if not isinstance(url, str) or not url:
            raise ProviderError("schema_drift", "Canvas file has no download URL")
        with self._asset_stream(url) as response:
            try:
                response.raise_for_status()
            except httpx.HTTPError:
                raise ProviderError(
                    "request_failed", "Canvas file download failed"
                ) from None

            def chunks() -> Iterator[bytes]:
                received = 0
                for chunk in response.iter_bytes(chunk_size=chunk_size):
                    received += len(chunk)
                    yield chunk
                if item.size_bytes is not None and received != item.size_bytes:
                    raise ProviderError(
                        "size_mismatch", "Canvas file size did not match metadata"
                    )

            yield chunks()

    def _resolve_page_image(self, reference: str) -> HtmlAssetResolution:
        match = _FILE_REFERENCE.search(reference)
        if match is None:
            return HtmlAssetResolution(
                original_reference=reference,
                disposition=AssetDisposition.MISSING_IMAGE,
                issue_code="unsafe_image_url",
            )
        try:
            payload = self._api_json(
                f"{self.base_url}/files/{quote(match.group(1), safe='')}"
            )
            if not isinstance(payload, dict):
                raise ProviderError(
                    "schema_drift", "Canvas image metadata is invalid"
                )
            media_type = (
                str(payload.get("content-type") or "")
                .split(";", 1)[0]
                .strip()
                .casefold()
            )
            if not media_type.startswith("image/"):
                return HtmlAssetResolution(
                    original_reference=reference,
                    disposition=AssetDisposition.MISSING_IMAGE,
                    issue_code="not_an_image",
                )
            size = payload.get("size")
            if isinstance(size, bool) or (
                size is not None and not isinstance(size, int)
            ):
                raise ProviderError("schema_drift", "Canvas image size is invalid")
            if isinstance(size, int) and (
                size < 0 or size > self._max_inline_image_bytes
            ):
                return HtmlAssetResolution(
                    original_reference=reference,
                    disposition=AssetDisposition.MISSING_IMAGE,
                    issue_code="image_size_limit_exceeded",
                )
            url = payload.get("url")
            if not isinstance(url, str) or not url:
                raise ProviderError(
                    "schema_drift", "Canvas image has no download URL"
                )
            with self._asset_stream(url) as response:
                response.raise_for_status()
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
            if isinstance(size, int) and total != size:
                return HtmlAssetResolution(
                    original_reference=reference,
                    disposition=AssetDisposition.MISSING_IMAGE,
                    retry_required=True,
                    issue_code="image_size_mismatch",
                )
            return HtmlAssetResolution(
                original_reference=reference,
                disposition=AssetDisposition.INLINE_IMAGE,
                content=b"".join(chunks),
                media_type=media_type,
            )
        except (ProviderError, httpx.HTTPError):
            return HtmlAssetResolution(
                original_reference=reference,
                disposition=AssetDisposition.MISSING_IMAGE,
                retry_required=True,
                issue_code="image_request_failed",
            )

    def render_html(self, item: RemoteItem) -> RenderedHtml:
        self._validate_item(item, RemoteKind.PAGE)
        if self._page_items.get(item.key) != item:
            if item.key not in self._page_items:
                raise ProviderError(
                    "forged_item", "Canvas item was not discovered by this provider"
                )
            raise ProviderError("stale_item", "Canvas page item is not current")
        page = self._page_snapshots.get(item.key)
        if page is None or _page_signature(page) != item.signature:
            raise ProviderError("stale_item", "Canvas page snapshot is unavailable")
        title = str(page.get("title") or "Canvas Page")
        soup = BeautifulSoup(str(page.get("body") or ""), "html.parser")
        references: list[str] = []
        for image in soup.find_all("img"):
            reference = image.get("src")
            if (
                isinstance(reference, str)
                and reference
                and reference not in references
            ):
                references.append(reference)
        for link in soup.find_all("a"):
            reference = link.get("href")
            if not isinstance(reference, str):
                continue
            if _FILE_REFERENCE.search(reference) is not None:
                link["href"] = urljoin(
                    self._public_origin, urlparse(reference).path
                )
        resolutions = tuple(
            self._resolve_page_image(reference) for reference in references
        )
        source_url = page.get("html_url")
        if not isinstance(source_url, str) or not source_url:
            source_url = (
                f"{self._public_origin}/courses/"
                f"{quote(item.key.course_id, safe='')}/pages/"
                f"{quote(item.key.remote_id, safe='')}"
            )
        return render_page_html(
            title=title,
            public_source_url=source_url,
            body_html=str(soup),
            assets=resolutions,
        )
