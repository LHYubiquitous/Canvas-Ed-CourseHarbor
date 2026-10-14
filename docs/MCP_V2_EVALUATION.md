# MCP Python SDK v2 evaluation

> Evaluated 2026-10-14 against the official SDK repository, release notes, v2
> migration guide, versioning policy, and roadmap. **Migration decision: DEFER**.
> Keep `mcp>=1.29.0,<2.0.0` until the gates below are independently green.

## Current evidence

The current stable SDK line is v2; the latest reviewed release is **v2.2.0**. The
official repository says the v1.x branch remains on critical/security maintenance and
explicitly recommends a `<2` cap for applications that have not migrated. The current
package follows that recommendation rather than accepting an untested major upgrade.

This project depends on v1-specific APIs in production and tests:

- `FastMCP` is imported from `mcp.server.fastmcp`; v2 removes that path and renames the
  high-level server to `MCPServer` under `mcp.server.mcpserver`.
- Protocol tests use v1 `ClientSession`, connected-memory helpers, and the legacy
  initialize handshake. v2 also supports older protocol revisions, but its native
  2026-07-28 protocol uses discovery/request metadata instead of the old session and
  handshake model.
- v2 changes dependency floors and transport internals, including `mcp-types` and
  `httpx2`; this needs clean-install/license/behavior review, not only an import rename.

The current Windows v1.29 protocol suite and installed-wheel stdio smoke pass. A v2
migration has not been implemented, so no v2 compatibility result is claimed.

## Required migration branch and gates

Use a separate branch/worktree and follow the **official migration guide** end to end:

1. Replace server imports/types and lifecycle APIs with `MCPServer`; preserve lazy
   provider construction, error redaction, read-only tool registration, and cleanup.
2. Port protocol tests to the v2 client/test helpers while retaining a legacy-client
   compatibility case and adding a native 2026-07-28 discovery case.
3. Run Canvas-only, Ed-only, both, Gradescope-failure, disabled-provider, stdio,
   installed-wheel, cancellation, and lifespan cleanup tests on Python 3.10–3.15.
4. Run the official SDK migration/conformance tests relevant to stdio servers. Record
   the exact SDK/spec versions and expected failures; do not convert an upstream known
   failure into a local pass claim.
5. Build and audit wheel/sdist, re-check dependency licenses and the optional
   Gradescope environment, then obtain Windows/macOS/Linux CI results.

Only after these gates are `INTEGRATION_VERIFIED` may the `<2.0.0` upper bound be
removed. A successful import or one local stdio handshake is insufficient.

## Risk/benefit decision

v2 is the correct long-term target because it is the active stable line, supports the
new protocol while negotiating older revisions, and receives normal fixes/features.
Migrating inside the portability phase would mix a major server/client rewrite with
cross-platform and real-account verification. The safer release boundary is to retain
the working v1.29 protocol surface now and schedule a dedicated, fully tested migration.

Official sources:

- https://github.com/modelcontextprotocol/python-sdk
- https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.2.0
- https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/migration.md
- https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/whats-new.md
- https://github.com/modelcontextprotocol/python-sdk/blob/main/VERSIONING.md
- https://github.com/modelcontextprotocol/python-sdk/blob/main/ROADMAP.md
