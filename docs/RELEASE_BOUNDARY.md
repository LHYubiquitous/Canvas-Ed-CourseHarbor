# Release Boundary Classification

> Status: REVIEWED for Phase 0 on 2026-10-13. This is a source/data/legacy classification, not permission to publish. The build must use an allowlist; Git presence alone never makes a file distributable.

## Release allowlist

The eventual source release may contain only these reviewed classes:

1. Product package and entry points: `pyproject.toml`, `src/canvas_ed_mcp/**`.
2. Compatibility shims that contain no personal paths or credentials: reviewed root launchers only.
3. Synthetic tests and fixtures: `tests/**` after automated personal-data and secret scans.
4. Public documentation and governance: `README.md`, `README.zh-CN.md`, `LICENSE`, `docs/**`, `CONTRIBUTING.md`, `SECURITY.md`, `CHANGELOG.md`, `THIRD_PARTY_NOTICES.md`.
5. Packaging/CI metadata: `.gitignore`, `.github/workflows/**`, and explicitly reviewed build configuration.

Anything not matched by the final package manifest is denied by default.

## Current repository classification

| Class | Current paths | Release decision |
|---|---|---|
| Upstream product source | `canvas_ed_mcp.py` | Migrate into `src/`; include after provider/config guards and tests |
| Sync product candidate | `sync_courses.py` | Migrate by vertical slices; include after characterization/state-policy tests remain green |
| Product support candidates | `setup_tokens.py`, `verify_mcp.py`, `run_mcp.sh`, `run_course_sync.command`, `claude_desktop_config_example.json` | Review individually; console entry points supersede shell launchers; examples must contain placeholders only |
| Renderer candidates | `export_ed_lessons_html.py` | Extract reusable renderer; include only the portable module |
| Synthetic tests | `tests/test_sync_output.py` and future `tests/**` | Include when fixtures remain synthetic |
| Public design/operations docs | `AGENTS.md`, `HANDOFF.md`, `PROGRESS.md`, `TASKS.md`, `docs/*.md`, `README.md`, `README.zh-CN.md`, `LICENSE` | Repository/source archive only; wheel includes only user-facing docs required at runtime/license time |
| Snapshot metadata | `artifacts/PUBLICATION_AUDIT.md`, `artifacts/SNAPSHOT_FILELIST.txt`, `artifacts/SNAPSHOT_MANIFEST.md` | Private audit evidence; exclude from wheel/sdist and re-review before any source archive |
| Agent/session metadata | `.claude/**`, `.agents/**` | Deny |
| Credentials and state | `credentials_local.json`, `.course_update_state.json`, `.env*`, `*_local.json`, `claude_desktop_config.json` | Permanent deny |
| Course outputs/data | `downloads/**`, `exports/**`, `output/**`, course-note exports | Permanent deny; course access is not redistribution permission |
| Local archives/caches/environments | `artifacts/*.zip`, `artifacts/*.tar.gz`, `.venv/**`, `__pycache__/**`, `.DS_Store`, `.test-tmp/**`, build/dist metadata | Permanent deny |
| Personal absolute-path launcher | `Course Material Sync.command` | Deny; retain locally only until the relative/console launcher is verified on macOS |
| Course-specific export/cleanup/reorder tools | `clean_ed_markdown.py`, `export_canvas_<course>_pages.py`, `export_ed_lessons.py`, `export_ed_lessons_pdfs.py`, `organize_ed_lessons.py`, `reorder_<course>_lessons.py`, `reorder_<course>_pdfs.py` | Legacy/personal deny unless separately generalized and audited |
| Course-specific wordbook tools | `tools/build_26s2_wordbook.py`, `tools/make_bubeidanci_compatible.py`, `tools/mine_wordbook_candidates.py`, `tools/rank_expanded_wordbook.py` | Legacy/personal deny |

## Machine-enforced denylist requirements

The package/archive tests must fail if any manifest contains:

- credential/state names or token-shaped contents;
- course output roots or non-synthetic course material;
- `.claude`, `.agents`, virtual environments, caches, temporary files, or local archives;
- the personal `.command` launcher or any course-specific tool listed above;
- absolute home paths such as `/Users/<name>/`, `C:\\Users\\<name>\\`, or signed URL query values;
- files outside the explicit allowlist.

Removal from config or a future package never authorizes deletion of the user's local copies.
