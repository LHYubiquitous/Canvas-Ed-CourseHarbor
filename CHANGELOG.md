# Changelog

All notable changes to the portable package are documented here. The project follows
Semantic Versioning; release-candidate versions are not stable API commitments.

## [0.2.0rc12] - 2026-10-20

### User documentation

- Adds a complete Simplified Chinese README and links it from the English README.
- Documents the exact Canvas settings flow for manual token generation and the
  Australian Ed API-token page.
- Expands first-run setup, native launchers, CLI/MCP use, upgrades, rollback, and
  troubleshooting into step-by-step operational guidance.
- Includes the Chinese README in the reviewed public-source and source-distribution
  allowlists without changing runtime behavior.

The published `0.2.0rc11` release and its tag remain immutable; rc12 is a new source
candidate until its release gate and publication complete.

## [0.2.0rc11] - 2026-10-20

### Release documentation

- Supersedes the unpublished immutable local `0.2.0rc10` packet without changing
  synchronization, due-report, launcher, or MCP behavior.
- Replaces historical release-status text with the current verification record and
  keeps unsupported device/account evidence explicitly scoped.
- Reorganizes the README around installation, credential storage, first setup, daily
  launchers, CLI use, MCP setup, and known limitations.
- Points installed package metadata to the public project and issue tracker.

Publication status and exact gate evidence are recorded in `docs/RELEASE_AUDIT.md`.

## [0.2.0rc10] - 2026-10-20

### Weekly due report count

- Supersedes the unpublished immutable local `0.2.0rc9` packet after owner-device
  testing showed that the weekly report contained the correct three items but the
  terminal displayed the unfiltered 23-item semester collection count.
- Weekly rendering and its terminal summary now share the same Sydney-week
  selection, and the summary explicitly reports `Due items in report`.

This candidate is not published and still requires owner-device review.

## [0.2.0rc9] - 2026-10-19

### macOS virtual-environment launchers

- Supersedes the unpublished immutable local `0.2.0rc8` packet after owner-device
  testing showed that resolving the `.venv/bin/python` symlink bound generated
  launchers to the underlying `uv` interpreter outside the project environment.
- Generated launchers now preserve the virtual-environment Python entry path, so
  isolated mode still imports the installed `canvas_ed_mcp` package.

This candidate is not published and still requires owner-device review.

## [0.2.0rc8] - 2026-10-18

### Daily menu warning review

- Supersedes the unpublished immutable local `0.2.0rc7` packet after owner-device
  testing showed that an actionable Canvas preview was blocked by the known
  course-wide Files-list authorization warning.
- The interactive menu now reviews structured sync results: a warning preview may be
  explicitly confirmed only when every selected source produced an actionable plan
  and no run-level failure occurred. A source with no actions remains fail-closed.
- A fully committed apply with provider warnings is reported as completed with
  warnings in the interactive menu, while the direct automation command retains its
  strict nonzero warning exit code.

This candidate is not published and still requires owner-device review.

## [0.2.0rc7] - 2026-10-18

### Windows timezone data

- Supersedes the unpublished immutable local `0.2.0rc6` packet after owner-device
  testing found that a base Windows installation could not load
  `ZoneInfo("Australia/Sydney")`.
- Declares `tzdata` as a Windows runtime dependency so weekly and semester due
  reports work in clean wheel environments without an OS IANA timezone database.

All daily-use behavior listed for `0.2.0rc5` and launcher isolation from `0.2.0rc6`
are retained. This candidate is not published and still requires owner-device review.

## [0.2.0rc6] - 2026-10-18

### Launcher isolation

- Supersedes the unpublished immutable local `0.2.0rc5` packet after installed-wheel
  testing found that a repository-root legacy module could shadow the installed
  package when a generated launcher used `python -m canvas_ed_mcp`.
- Runs every generated Windows, macOS, and Linux launcher with Python isolated mode,
  preserving the intended English terminal menu regardless of the working directory.

All daily-use behavior listed for `0.2.0rc5` below is retained. This candidate is not
published and must not be published; Windows owner testing found the missing timezone
dependency corrected by `0.2.0rc7`.

## [0.2.0rc5] - 2026-10-18

### Daily-use application

- Adds an English-first numbered terminal menu for material sync, weekly and semester
  due reports, configuration, diagnostics, course-folder opening, and launcher repair.
- Installs native create-only convenience launchers after a committed setup/configure;
  edited launchers are retained and reviewed replacements use a `.new` suffix.
- Adds provider-neutral due collection for Canvas, Ed, optional explicitly mapped
  Gradescope courses, and explicitly configured local Unit Outline Markdown.
- Preserves the last complete snapshot when a source is partial or unavailable, and
  publishes reports create-only with revision suffixes instead of overwriting files.
- Preserves completed checkboxes and marker-attached manual notes in regenerated
  semester Markdown, with optional teaching-week grouping in Australia/Sydney time.
- Loads schema-v1 profiles into schema v2 in memory with safe daily-use defaults;
  existing configuration files are not rewritten merely by loading them.

This candidate is not published and must not be published. Its installed-wheel smoke
found the launcher shadowing issue corrected by `0.2.0rc6`.

## [0.2.0rc4] - 2026-10-18

### Release metadata

- Supersedes the unpublished, immutable local `0.2.0rc3` candidate after its macOS
  matrix correctly detected SBOM output generated by an older Windows `uv 0.9.30`.
- Regenerates lock exports and CycloneDX metadata with the reviewed `uv 0.11.28`
  tool used by both the owner Mac and CI, and adds a regression assertion for the
  recorded SBOM generator version.

Phase-6 product behavior is unchanged. This candidate remains unpublished until the
owner-device macOS matrix passes.

## [0.2.0rc3] - 2026-10-18

### Release tooling

- Supersedes the unpublished, immutable local `0.2.0rc2` candidate.
- Aligns the owner-device macOS matrix and every CI job on reviewed `uv 0.11.28`,
  matching the repository owner's Mac and preventing a valid Mac from being rejected
  solely because an older tool pin remained in the runner.

Phase-6 product behavior is unchanged. This candidate remains unpublished until the
owner-device macOS matrix passes and release authorization is confirmed.

## [0.2.0rc2] - 2026-10-18

### Security and release tooling

- Supersedes the unpublished local `0.2.0rc1` candidate after independent review.
- Rejects Windows backslash traversal, control characters, links, devices, and
  non-file/non-directory members before manually extracting a clean-source archive.
- Requires fresh locked pylock/SBOM metadata before building a candidate and runs the
  full deterministic candidate builder in CI from the locked development environment.

All Phase-6 functionality and verification boundaries listed for `0.2.0rc1` below
remain unchanged. Neither local candidate is published.

## [0.2.0rc1] - 2026-10-18

### Added

- Provider-neutral portable synchronization for explicitly configured Canvas and Ed
  sources, including mixed-provider transactions, dry-run, per-source initial mode,
  policy enforcement, state-v5 checkpoints, and no-clobber publication.
- Canvas module-linked Page/File discovery, safe self-contained Page rendering, and a
  credential-free, ordered asset-delegation boundary for reviewed Canvas image hosts.
- Ed Lesson/Resource synchronization with safe self-contained rendering, exact asset
  host trust, responsive reading layout, and surfaced content-quality warnings.
- Interactive Canvas-only, Ed-only, and explicit Canvas+Ed setup plus retain-by-default
  reconfiguration with opt-in discovery and config-only preview/commit behavior.
- Immutable local release-candidate packet tooling with deterministic wheel, sdist,
  clean-source archive, manifests, checksums, and fail-closed distribution audits.

### Verification boundaries

- Windows real Ed and Canvas content are `HUMAN_VERIFIED_WITH_WARNINGS`: Ed retained
  four accepted non-visible placeholders; Canvas course-wide Files enumeration may
  remain partial after an account-scoped 403.
- Windows owner-device Ed-only, Canvas-only, and mixed-provider configure discovery
  passed. The `0.2.0rc1` macOS owner-device matrix and remote CI remain pending until
  the immutable candidate is reviewed and transferred through an authorized path.
- `course-sync` remains a non-downloading compatibility command. Public release,
  GitHub tag/Release replacement, and PyPI publication are not part of this build.

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
