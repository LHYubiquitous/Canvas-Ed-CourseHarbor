"""Pure self-contained HTML rendering from provider-resolved assets."""

from __future__ import annotations

import base64
import html
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlsplit, urlunsplit

from bs4 import BeautifulSoup, Comment


_ACTIVE_CONTENT_TAGS = frozenset(
    {"base", "embed", "iframe", "link", "meta", "object", "script", "style"}
)
_URL_ATTRIBUTES = frozenset(
    {
        "action",
        "background",
        "cite",
        "data",
        "formaction",
        "href",
        "longdesc",
        "manifest",
        "ping",
        "poster",
        "profile",
        "src",
        "srcdoc",
        "srcset",
        "usemap",
        "xlink:href",
    }
)
_DOCUMENT_STYLE = """
html {
  background: #f3f4f6;
  color: #1f2937;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  line-height: 1.65;
}
body { margin: 0; }
main {
  box-sizing: border-box;
  max-width: 960px;
  margin: 2rem auto;
  padding: 2rem 2.5rem;
  background: #ffffff;
  border-radius: 0.5rem;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.12);
}
h1, h2, h3, h4, h5, h6 { line-height: 1.3; }
p, li { overflow-wrap: anywhere; }
td, th {
  overflow-wrap: normal;
  word-break: normal;
}
section.slide { margin-block: 2rem; }
img {
  display: block;
  max-width: 100%;
  height: auto;
  margin: 1rem auto;
}
table {
  display: block;
  max-width: 100%;
  overflow-x: auto;
  border-collapse: collapse;
}
pre {
  max-width: 100%;
  overflow-x: auto;
  padding: 1rem;
  background: #f3f4f6;
}
code { font-family: ui-monospace, "Cascadia Code", monospace; }
@media (max-width: 720px) {
  main {
    margin: 0;
    padding: 1rem;
    border-radius: 0;
  }
}
""".strip()


class AssetDisposition(str, Enum):
    INLINE_IMAGE = "inline_image"
    SAFE_LINK = "safe_link"
    MISSING_IMAGE = "missing_image"


def _validate_text(
    value: str,
    field: str,
    *,
    allow_empty: bool = False,
    allow_formatting_whitespace: bool = False,
) -> None:
    if not isinstance(value, str) or (not allow_empty and not value):
        raise ValueError(f"{field} must be a string")
    if any(
        (
            ord(character) < 32
            and not (allow_formatting_whitespace and character in "\t\n\r")
        )
        or ord(character) == 127
        for character in value
    ):
        raise ValueError(f"{field} cannot contain control characters")


def sanitize_public_url(url: str) -> str:
    """Remove credentials, query and fragment from an HTTP(S) or relative URL."""
    _validate_text(url, "url")
    parsed = urlsplit(html.unescape(url))
    scheme = parsed.scheme.casefold()
    if scheme:
        if scheme not in {"http", "https"} or parsed.hostname is None:
            raise ValueError("public URL must use HTTP or HTTPS")
        host = parsed.hostname
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("public URL has an invalid port") from exc
        netloc = host if port is None else f"{host}:{port}"
        return urlunsplit((scheme, netloc, parsed.path or "/", "", ""))
    if parsed.netloc or url.startswith("//"):
        raise ValueError("scheme-relative URLs are not allowed")
    return urlunsplit(("", "", parsed.path, "", ""))


def image_data_uri(content: bytes, media_type: str) -> str:
    """Encode verified image bytes as a self-contained data URI."""
    if not isinstance(content, bytes):
        raise ValueError("content must be bytes")
    _validate_text(media_type, "media_type")
    normalized = media_type.split(";", 1)[0].strip().casefold()
    if not normalized.startswith("image/"):
        raise ValueError("only image media types can be embedded")
    encoded = base64.b64encode(content).decode("ascii")
    return f"data:{normalized};base64,{encoded}"


def missing_image_data_uri(label: str) -> str:
    """Return an inline SVG placeholder without external references."""
    _validate_text(label, "label", allow_empty=True)
    message = html.escape(label or "Image unavailable")
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="90">'
        '<rect width="100%" height="100%" fill="#f3f3f3"/>'
        f'<text x="20" y="52" font-family="sans-serif" font-size="18" fill="#666">{message}</text>'
        "</svg>"
    )
    encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


@dataclass(frozen=True, slots=True)
class HtmlAssetResolution:
    original_reference: str
    disposition: AssetDisposition
    content: bytes | None = None
    media_type: str | None = None
    public_url: str | None = None
    retry_required: bool = False
    issue_code: str | None = None

    def __post_init__(self) -> None:
        _validate_text(self.original_reference, "original_reference")
        if not isinstance(self.disposition, AssetDisposition):
            raise ValueError("disposition must be an AssetDisposition")
        if not isinstance(self.retry_required, bool):
            raise ValueError("retry_required must be a boolean")
        if self.issue_code is not None:
            _validate_text(self.issue_code, "issue_code")

        if self.disposition is AssetDisposition.INLINE_IMAGE:
            if self.content is None or self.media_type is None:
                raise ValueError("inline images require content and media_type")
            image_data_uri(self.content, self.media_type)
            if self.public_url is not None:
                raise ValueError("inline images cannot carry a public URL")
        elif self.disposition is AssetDisposition.SAFE_LINK:
            if self.public_url is None:
                raise ValueError("safe links require a public URL")
            sanitize_public_url(self.public_url)
            if self.content is not None or self.media_type is not None:
                raise ValueError("safe links cannot carry embedded content")
        elif any(
            value is not None for value in (self.content, self.media_type, self.public_url)
        ):
            raise ValueError("missing images cannot carry content or a public URL")


@dataclass(frozen=True, slots=True)
class RenderedHtml:
    document: str
    retry_required: bool
    issue_codes: tuple[str, ...]


def render_page_html(
    *,
    title: str,
    public_source_url: str,
    body_html: str,
    assets: tuple[HtmlAssetResolution, ...] = (),
) -> RenderedHtml:
    """Render deterministic page HTML without network or filesystem access."""
    _validate_text(title, "title")
    _validate_text(
        body_html,
        "body_html",
        allow_empty=True,
        allow_formatting_whitespace=True,
    )
    source_url = sanitize_public_url(public_source_url)
    resolutions: dict[str, HtmlAssetResolution] = {}
    for resolution in assets:
        if not isinstance(resolution, HtmlAssetResolution):
            raise ValueError("assets must contain HtmlAssetResolution values")
        if resolution.original_reference in resolutions:
            raise ValueError("asset references must be unique")
        resolutions[resolution.original_reference] = resolution

    soup = BeautifulSoup(body_html, "html.parser")
    issues: list[str] = []
    retry_required = False

    def add_issue(code: str | None) -> None:
        if code and code not in issues:
            issues.append(code)

    for active in soup.find_all(tuple(sorted(_ACTIVE_CONTENT_TAGS))):
        active.decompose()

    for comment in soup.find_all(string=lambda value: isinstance(value, Comment)):
        comment.extract()

    for form in soup.find_all("form"):
        form.unwrap()

    for picture in soup.find_all("picture"):
        for source in picture.find_all("source"):
            source.decompose()

    for image in soup.find_all("img"):
        image.attrs.pop("srcset", None)
        image.attrs.pop("sizes", None)
        reference = str(image.get("src") or "")
        if reference.casefold().startswith("data:image/"):
            continue
        resolution = resolutions.get(reference)
        if resolution is not None and resolution.disposition is AssetDisposition.INLINE_IMAGE:
            image["src"] = image_data_uri(
                resolution.content or b"", resolution.media_type or ""
            )
            add_issue(resolution.issue_code)
            retry_required = retry_required or resolution.retry_required
            continue

        label = str(image.get("alt") or "Image unavailable")
        image["src"] = missing_image_data_uri(label)
        if resolution is None:
            add_issue("unresolved_image")
        else:
            add_issue(resolution.issue_code or "image_not_embedded")
            retry_required = retry_required or resolution.retry_required

    for source in soup.find_all("source"):
        source.attrs.pop("srcset", None)

    for tag_name, attribute in (
        ("a", "href"),
        ("audio", "src"),
        ("video", "src"),
        ("source", "src"),
    ):
        for tag in soup.find_all(tag_name):
            reference = tag.get(attribute)
            if not isinstance(reference, str) or not reference:
                continue
            resolution = resolutions.get(reference)
            candidate = (
                resolution.public_url
                if resolution is not None
                and resolution.disposition is AssetDisposition.SAFE_LINK
                else reference
            )
            try:
                tag[attribute] = sanitize_public_url(str(candidate))
            except ValueError:
                del tag[attribute]
                add_issue("unsafe_external_url")
            if resolution is not None:
                add_issue(resolution.issue_code)
                retry_required = retry_required or resolution.retry_required

    allowed_urls = {
        ("a", "href"),
        ("audio", "src"),
        ("video", "src"),
        ("source", "src"),
        ("img", "src"),
    }
    for tag in soup.find_all(True):
        for attribute in tuple(tag.attrs):
            attribute_name = attribute.casefold()
            if (
                attribute_name == "style"
                or attribute_name.startswith("on")
                or attribute_name.startswith("data-")
            ):
                del tag.attrs[attribute]
                continue
            is_lazy_url = "src" in attribute_name or "href" in attribute_name
            if (
                attribute_name in _URL_ATTRIBUTES or is_lazy_url
            ) and (tag.name.casefold(), attribute_name) not in allowed_urls:
                del tag.attrs[attribute]

    escaped_title = html.escape(title)
    escaped_source = html.escape(source_url, quote=True)
    document = (
        '<!doctype html><html><head><meta charset="utf-8">'
        f"<style>{_DOCUMENT_STYLE}</style>"
        f"<title>{escaped_title}</title></head><body>"
        f"<main><h1>{escaped_title}</h1>"
        f'<p class="source"><a href="{escaped_source}">Open source</a></p>'
        f"{soup}</main></body></html>"
    )
    return RenderedHtml(document, retry_required, tuple(issues))
