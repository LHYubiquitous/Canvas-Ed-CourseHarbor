# Changelog

All notable changes to the portable package are documented here. The project follows
Semantic Versioning; release-candidate versions are not stable API commitments.

## [0.1.0rc1] - 2026-10-14

### Added

- Installable Python package and cross-platform console entry points.
- Versioned typed configuration, profile-isolated platform paths, keyring/environment
  credential references, redaction, and non-secret diagnostics.
- Canvas and Ed read-only providers with stable identities, explicit mapping, schema
  drift handling, same-origin/auth boundaries, and self-contained Ed Lesson rendering.
- Optional, disabled-by-default Gradescope read-only provider.
- Provider-aware read-only MCP server with conditional tools and lazy credential use.
- Portable sync domain, policy, state-v4, locking, no-clobber storage, renderer, and
  reconfiguration primitives.
- Windows/macOS/Linux CI for Python 3.10–3.15, wheel/sdist audits, installed-protocol
  smoke tests, dependency locks, CycloneDX SBOM, and deterministic clean-source gate.

### Security

- Credentials, state, real course data, signed URLs, personal paths, internal handoff
  files, and legacy/personal scripts are excluded from public distributions.
- Dependency vulnerability audit reported no known vulnerabilities in the locked
  release set on 2026-10-14.

### Known limitations

- Portable real sync orchestration and interactive Ed/both setup are not connected.
- Canvas OAuth is documented but not implemented.
- Native macOS/Linux keyring prompts and owner-device MCP-host launch remain `NOT_RUN`.
- No public repository, tag, release asset, or PyPI package has been created.
