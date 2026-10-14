# Upstream Review — 2026-10-13

## Compared revisions

- Local baseline: `d410c07bb16655d14604b6f5bfa0e4fc4752b37a`
- Public upstream `main`: `e119ebe528a3c7529c4a209c1725a10ce51652de`
- Upstream-only commits:
  - `22e4387` — configurable `CANVAS_BASE_URL`
  - `e119ebe` — route assessment coverage outline fetch through the SSRF allowlist

The comparison changed `README.md`, `canvas_ed_mcp.py`, `claude_desktop_config_example.json`, and added `test_config.py` upstream.

## Disposition

No merge, checkout, or direct cherry-pick was performed. The working tree already contained user changes in `README.md` and `canvas_ed_mcp.py`; a direct cherry-pick would mix conflict resolution with those edits. Instead, the two behavioral changes were manually ported into the current file while retaining the existing local `enrollment_state[]` fix.

Equivalent unittest coverage was added under `tests/test_mcp_config.py`:

- custom/default Canvas instance and exact-subdomain allowlist behavior;
- malformed base URL fails at startup;
- `verify_assessment_coverage` rejects an untrusted external-tool URL before creating a general HTTP client.

This review does not authorize a push or publication. Future upstream reviews must compare from `e119ebe` (or the eventual integration commit) and repeat the dirty-tree check before applying changes.
