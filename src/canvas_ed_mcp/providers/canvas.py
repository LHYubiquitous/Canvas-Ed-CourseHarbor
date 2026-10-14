"""Canvas course catalog adapter with opaque, same-instance pagination."""

from __future__ import annotations

from urllib.parse import urlparse

import httpx

from .base import CourseCandidate, ProviderError


def _origin(url: str | httpx.URL) -> tuple[str, str, int | None]:
    parsed = urlparse(str(url))
    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme.casefold() == "https" else 80
    return parsed.scheme.casefold(), (parsed.hostname or "").casefold(), port


class CanvasProvider:
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
