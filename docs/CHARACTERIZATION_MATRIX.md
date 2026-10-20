# Sync Characterization Matrix

> Status: MOCK_VERIFIED on Windows, 2026-10-13. This matrix is the executable contract for REQ-001 and maps every rule in `CURRENT_BASELINE.md` to named tests. All fixtures and credentials are synthetic.

## Baseline contract

| # | Required invariant | Named characterization evidence |
|---:|---|---|
| 1 | First run is an explicit baseline and downloads nothing | `test_baseline_records_source_without_downloading` |
| 2 | A user-deleted tracked file is not backdated while the remote source is unchanged; a later real remote change is downloaded | `test_user_deleted_tracked_file_is_not_restored_when_remote_is_unchanged`; `test_user_deleted_tracked_file_is_restored_only_after_remote_changes` |
| 3 | Only new/changed sources are processed; signed URL rotation is not a content change | `test_user_deleted_tracked_file_is_not_restored_when_remote_is_unchanged`; `test_rotating_signed_download_url_does_not_change_source_signature`; `test_timed_out_canvas_image_is_retried_and_replaced_on_next_run` |
| 4 | Unedited managed files may be atomically replaced; user edits are preserved and the remote revision is written separately | `test_unedited_download_is_replaced_without_a_version_chain`; `test_unedited_html_is_replaced_in_place_and_edited_html_is_preserved` |
| 5 | A matching user-owned file may de-duplicate content but is never claimed as sync-managed | `test_matching_user_file_is_not_claimed_by_sync` |
| 6 | De-duplication is course-scoped and content-hash confirmed, including equal content under a different filename | `test_matching_user_file_is_not_claimed_by_sync`; all tests construct a separate `CourseFiles` index per course root |
| 7 | Existing course/module/category directories are reused before the explicit `_New Course Updates` fallback | `test_existing_course_directory_is_reused_for_new_material`; `test_unedited_download_is_replaced_without_a_version_chain` |
| 8 | Canvas Pages and Ed Lessons are self-contained HTML with image data URIs and no separate assets directory | `test_canvas_image_is_embedded_without_asset_file`; `test_ed_lesson_export_is_one_self_contained_html`; `test_legacy_organizer_embeds_assets_and_drops_raw_json_link` |
| 9 | PDF/video/audio links in HTML are not downloaded again; links are sanitized | `test_canvas_page_does_not_download_non_image_attachments`; `test_img_tag_pointing_to_video_is_not_downloaded_as_an_image`; `test_embedded_pdf_link_does_not_persist_signed_query` |
| 10 | Course output contains rendered/downloaded content, not raw API JSON sidecars | `test_canvas_sync_writes_content_without_raw_json_sidecars`; `test_legacy_organizer_embeds_assets_and_drops_raw_json_link` |
| 11 | Downloads are bounded and complete; final publication uses same-directory staging plus atomic replace; a failed replace preserves the old destination | `test_file_download_stops_after_total_transfer_deadline`; `test_file_download_rejects_truncated_response`; `test_atomic_install_uses_destination_directory_and_leaves_no_staging_file`; `test_atomic_install_failure_preserves_existing_destination` |
| 12 | A single Ed Resource failure is isolated and later resources continue | `test_ed_resource_timeout_does_not_skip_later_resources` |
| 13 | State is isolated by absolute output root; v2 paths are not silently claimed as managed | `test_state_is_namespaced_by_output_root_and_v2_paths_stay_unmanaged`; `test_managed_path_outside_course_root_is_rejected` |
| 14 | Signed queries/fragments are redacted from links and errors, and Bearer credentials are not sent to non-Canvas media hosts | `test_safe_error_strips_signed_query_and_fragment`; `test_embedded_pdf_link_does_not_persist_signed_query`; `test_canvas_page_does_not_download_non_image_attachments`; `test_canvas_image_is_embedded_without_asset_file`; `test_canvas_metadata_failure_still_sanitizes_attachment_link` |
| 15 | Portable Canvas discovery preserves module-linked Pages/Files plus remaining course Files; first module wins grouping and unlinked Pages are not enumerated | `test_content_universe_first_group_and_self_contained_fetches`; `test_page_only_never_lists_all_files_or_unlinked_pages`; `test_optional_group_adds_one_sanitized_portable_component` |
| 16 | Canvas API Bearer remains on the configured origin; asset fetch is credential-free, exact-host allowlisted, rejects arbitrary DNS targets before request, and cannot change origin through redirects | `test_cross_origin_pagination_is_rejected_before_token_can_leak`; `test_asset_client_rejects_credentials_and_truncated_file`; `test_unallowlisted_dns_asset_is_rejected_before_request`; `test_asset_redirect_cannot_change_delegated_origin` |

## Decision table locked by tests

| Remote state | Existing state | Local state | Required result |
|---|---|---|---|
| baseline | absent | any | record signature only; no download |
| unchanged | known | present or missing | no download; preserve ownership evidence |
| changed | known managed hash matches | present | atomically replace in place |
| changed | known managed hash differs | present | preserve user file; save a separate updated copy |
| changed | known managed path missing | missing | download the new revision as a managed file |
| new | absent | identical user file exists | skip duplicate; do not claim the user file |

The remote-change-after-deletion behavior intentionally preserves the legacy default. A future permanent tombstone policy remains EXT-004 and must be opt-in.

## P2-01 domain evidence

`tests/test_sync_domain.py` mirrors this table without performing I/O. It additionally proves that source identity includes provider instance/course/kind/item IDs, previous ownership is bound to the same source, retry trigger and post-install retry outcome remain distinct, duplicate content is never claimed, and corrupt or non-portable paths fail closed.

## P2-02 storage and renderer evidence

`tests/test_sync_storage.py` composes the decisions with course-local observation and same-directory atomic no-clobber publication. It proves content-hash-confirmed de-duplication, output/course containment, missing/modified/unmodified/unsafe observations, Windows reparse rejection, pre-commit cleanup, concurrent target preservation, unsupported-filesystem fail-closed behavior and post-commit cleanup reporting. Portable storage intentionally does not expose in-place replacement; when that capability is unavailable, `decide_install` returns `INSTALL_SEPARATELY / SAFE_REPLACE_UNAVAILABLE`, so execution and future state can record the real versioned-sibling outcome.

`tests/test_html_renderer.py` proves image data-URI embedding, missing-image placeholders/retry evidence, `srcset` removal, URL credential/query/fragment redaction and conservative removal of active content, event handlers, CSS URL surfaces and unapproved URL-bearing attributes. The renderer is pure: it performs no network or filesystem I/O. The 28 legacy tests remain the behavioral oracle for the currently active downloader.

## P2-03 policy evidence

`tests/test_sync_policy.py` proves provider-neutral category selection and file/resource extension, MIME and size filtering. Explicit exclusions precede includes; MIME matching accepts exact types and `type/*` rules; known oversize items are rejected before a request, while unknown-size streams use a pre-write cumulative chunk guard. All skips expose stable reason codes. Pages and lessons deliberately bypass binary filters. The policy is not yet connected to the real downloader, so REQ-008/009 remain mock-verified at the pure-policy boundary rather than integration-verified.

## P2-04 state evidence

`tests/test_sync_state.py` proves deterministic strict state v4 serialization, profile/output-root namespace binding, explicit backup recovery and atomic preservation under primary/backup failures. Synthetic v3 fixtures prove that only the selected output scope migrates, structured provider identity replaces delimiter-based keys, unmanaged legacy paths remain unclaimed, unsafe ownership fails before any v4 write, and a concurrent destination is preserved. The migration leaves the legacy file byte-for-byte unchanged. State primitives are not yet wired into the portable engine.

## P2-05 configure evidence

`tests/test_configure_flow.py` proves deterministic course add/remove/change previews, explicit initial behavior for every newly added source and no operational diff from confirmation timestamp churn. The interactive `configure` CLI is retain-by-default and provider-side scoped: unselected providers perform no secret lookup or discovery, mixed-course edits preserve the other provider, blank edits preserve exact policy bytes and filters, and no-op/dry-run/final cancel are zero-write. Confirmed removal updates only config while preserving course files and historical state bytes; a config changed after preview is rejected. Windows owner-device configure is `PASS` for real Ed-side, Canvas-side and mixed-provider discovery. The isolated Ed copy additionally passed config-only commit and byte-exact rollback; Canvas-only and mixed-provider were no-op dry-runs with no content/state writes.

## P2-06 lock evidence

`tests/test_sync_lock.py` exercises real Windows OS-held locks: same physical course output contention across profiles, independence for different course folders, profile/output state scopes, bounded timeout, exception-safe release and reacquisition after a subprocess exits without cleanup. Retained metadata files are diagnostic only and never treated as ownership. Storage and state tests additionally prove that publication, v4 save and v3 migration reject missing or mismatched locks. The POSIX `flock` branch is implemented but remains `NOT_RUN` until macOS/Linux CI.
