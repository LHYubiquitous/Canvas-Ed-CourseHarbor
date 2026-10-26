# Changelog

All notable changes to the portable package are documented here. The project follows
Semantic Versioning; release-candidate versions are not stable API commitments.

## [0.2.0rc19] - 2026-10-26

### Courses history responsiveness

- Refreshes the Courses tree's accessibility cell cache after collapsing a branch,
  preventing stale sibling indexes during Windows UI Automation navigation.
- Keeps baseline-only sync runs expandable and shows their result summary and local
  destination even when no files were added or updated.

## [0.2.0rc18] - 2026-10-26

### macOS application and DMG candidate

- Adds a native PyInstaller `CourseHarbor.app` with GUI and console recovery
  companions, stable bundle identity, versioned `Info.plist`, and the reviewed ICNS
  application icon.
- Adds a fail-closed macOS bundle audit for metadata, architectures, executable layout,
  Qt Cocoa platform plugin, safe in-bundle symlinks, private material, and embedded
  workspace/temp paths or external native dependencies.
- Adds an arm64 `macos-15` CI job that creates a DMG containing an ad-hoc-signed app,
  verifies it, mounts it read-only, reruns GUI/CLI smoke tests from the mounted volume,
  and uploads the DMG plus its audit manifest as a 14-day workflow artifact.
- Makes the setup journey provider-aware: Canvas-only and Ed-only profiles skip the
  cross-provider Map step in both directions, while dual-provider profiles retain it.
- Lets Connections form fields use the available width consistently across native Qt
  styles, including macOS.
- Final private CI run `40293746038` passed the complete matrix and produced the
  published audited arm64 release candidate from commit `bdcb4f7`.
- Owner-device validation passed Finder launch, drag-install, App Shell navigation,
  profile/setup flows, Connections layout, and native Keychain save/test/replace.
- Developer ID signing, Apple notarization, Gatekeeper acceptance, and
  Intel/universal builds remain `NOT_RUN`; the published DMG is arm64, ad-hoc signed,
  and not notarized.

## [0.2.0rc17] - 2026-10-26

### Graphical credential onboarding

- Adds an application-layer typed credential controller for deterministic Canvas/Ed
  keyring targets, native-store readiness, provider-isolated read-only validation,
  explicit replacement, cancellation boundaries, and sanitized results.
- Changes first-run onboarding from terminal command copying to masked **Save and
  test** and **Test existing** actions. Tokens are cleared from the widget before
  background work and never enter config, status text, clipboard, argv, or Qt models.
- Uses validate-before-persist so invalid credentials, provider/network failures, and
  cancellation before keyring write leave an existing credential unchanged.
- Separates the Canvas/Ed provider cards from the tab bar and renders enabled providers
  with an explicit checked indicator instead of a color-only solid square.
- Generalizes Canvas documentation around each institution's host and labels non-AU
  Ed regional token pages as `NOT_RUN` instead of inferring support.

### Desktop workflow integration

- Adopts **CourseHarbor** as the display brand and names the repository
  `Canvas-Ed-CourseHarbor`, while retaining package, CLI, keyring, profile, state, and
  launcher identifiers for upgrade compatibility.
- Replaces the Dashboard's fixed dark sidebar with the compact Setup visual language;
  warning results are aggregated into reusable, collapsed notices that can be expanded
  or dismissed, leaving the course table as the primary workspace.
- Adds the unified CourseHarbor App Shell routes for Library, Due dates, Courses,
  Connections, Health & About, and Open local library.
- Adds a local-only startup planner and Profile chooser: zero profiles enters First run,
  one healthy profile opens directly, and multiple or damaged profiles remain visible
  without provider traffic or credential resolution. The App Shell can return through
  **Switch profile** after active work reaches a safe cancellation boundary.
- Keeps `First run → course selection → Save configuration → Open daily dashboard` in
  the same CourseHarbor process. Closing course selection also closes its hidden
  First-run host, while standalone setup retains the detached-launch recovery path.
- Returns an abandoned First Run wizard or embedded course-selection flow to the
  refreshed Profile chooser instead of terminating the whole app. Adds reviewed
  **Remove profile…**: the selected
  config is revision-checked and moved to a recoverable local archive, while course
  files, credentials, sync state, logs/cache, and remote tokens remain untouched.
- Adds the installed `courseharbor` GUI entry point while retaining `canvas-ed-app` and
  explicit `--profile` compatibility. A frozen executable/installer remains `NOT_RUN`.
- Splits due-date work into independent This week and Semester load/export workflows,
  each with its own destination and explicit publish action.
- Adds a profile-local, non-secret sync-report history. Courses expands each configured
  course into timestamped apply runs and changed-file destinations while keeping the
  complete course folder visible.
- Reduces Canvas preview request amplification by requesting inline Module items and
  reusing the Course Files index, while fetching only module-linked Page bodies with a
  bounded pool. Independent course sources discover concurrently in configuration order;
  apply remains serial. On the Windows owner profile used for this candidate, the same
  ten-source read-only preview improved from about 54.5 seconds to 17.2 seconds.
- Records the existing-profile Windows App Shell, independent due workflows, course
  report expansion, and navigation as owner-device `PASS` on 2026-10-25. Frozen-package
  startup, macOS rc17 behavior, signing, upgrade, and uninstall remain `NOT_RUN`.
- Adds the first PyInstaller 6.22.3 onedir release candidate with separate windowed
  `CourseHarbor.exe` and console `courseharbor-cli.exe` companions. The build isolates
  dependency discovery from host tool paths, audits PE subsystems, Qt plugins, private
  material, and embedded machine paths, and preserves source/frozen launcher behavior.
- Records automated Windows owner-device bundle and relocation smoke as `PASS` on
  2026-10-26, including QtCore import, Windows Credential Manager backend readiness,
  CLI help, and no-console GUI startup. Full visible frozen-app use, installer, signing,
  upgrade/uninstall, and macOS packaging remain `NOT_RUN`.
- Records the owner-confirmed visible frozen Windows App Shell journey as `PASS` on
  2026-10-26. This version is published as matching private/public GitHub prereleases;
  PyPI, signing, installer, auto-update, and macOS DMG publication remain `NOT_RUN`.
- Adds an original CourseHarbor application icon across source UI, Windows executable
  resources, and a reviewed multi-resolution macOS iconset. The mark combines an open
  book, protective harbor, and water without using Canvas, Ed, or university branding.

- Changes the managed `canvas_ed_app` launcher from the legacy numbered terminal menu
  to the PySide6 daily dashboard. An exact generated predecessor is upgraded atomically;
  edited or unknown launchers remain preserved for review, and the GUI never opens the
  preserved canonical file as though it were the newly generated dashboard.
- Adds a per-course folder picker to the setup Folders step. Each new course can select
  an existing direct child of the reviewed local course library, including an existing
  Desktop course folder when Desktop is chosen as the library root.
- Moves Library root selection from the early Profile page into the Folders step, so a
  direct-child validation error can be recovered in place without restarting discovery.
  Review is blocked until the selected Library root is an existing directory.
- Compacts the shared setup header plus the Map and Folders layouts for scaled Windows
  desktops, keeping primary course-pair actions and at least two course-folder rows in
  the first viewport at the standard 1040×710 logical test size.
- Simplifies Review to one **Save configuration** action, moves close confirmation to
  the title-bar X, and exposes **Open daily dashboard** immediately after save without
  requiring shortcut installation.
- Provisions missing empty course folders as part of the reviewed save, reuses existing
  safe folders, fails closed on collisions, and rolls back only folders created by a
  failed save attempt.
- Keeps a provider-isolated discovery failure visible as a partial ten-source dashboard
  review instead of collapsing the entire preview into a generic failure.
- Keeps existing configured course folders read-only and does not move, delete, create,
  or infer course directories during configuration review.

This source candidate is unreleased. Public `v0.2.0rc16` and its artifacts remain
immutable; rc17 requires full automated gates plus Windows/macOS owner-device review.

## [0.2.0rc16] - 2026-10-23

### Human-readable reviewed sync

- Carries configured course folder, code, name, and term metadata through the
  reviewed-sync summary without changing the stable internal source identity.
- Shows human-readable course metadata and provider names in the dashboard table
  and apply confirmation instead of internal course keys, hostnames, and numeric IDs.
- Replaces the UI regression expectations that encoded the internal-identity display
  and adds application coverage for preview/apply metadata and safe folder fallback.
- Rebuilds the macOS owner-device runner after the rc15 packet duplicated shell quotes
  and failed before starting the GUI.
- Cancels pending setup-window focus delivery before Qt destroys its child widgets,
  preventing a Windows Python 3.14 shutdown access violation in isolated GUI tests.

This candidate supersedes the failed, unpublished rc15 packet. Publication remains
blocked until the exact rc16 wheel passes a fresh macOS owner-device gate and private
three-system CI.

## [0.2.0rc15] - 2026-10-23

### macOS button contrast

- Gives ordinary dashboard and confirmation-dialog buttons an explicit dark
  foreground whenever the application supplies their light surface.
- Prevents a macOS dark system palette from producing white button labels on the
  application's white button background.
- Adds macOS palette regression coverage while preserving the reviewed Windows
  disabled-button and primary-action styling.

This candidate supersedes the unpublished rc14 wheel and requires a fresh automated
release gate plus macOS owner-device confirmation before publication.

## [0.2.0rc14] - 2026-10-22

### Non-destructive course reconfiguration

- Allows existing courses and individual Canvas/Ed sources to be deactivated; their
  downloaded files stay in place and later sync runs omit the inactive sources.
- Allows retired sources to be reactivated, existing Canvas and Ed courses to be
  relinked, and a newly adopted provider to join an initialized logical course.
- Makes `Start from now` and `Import all history` selectable again for existing
  courses. Each explicit reset is consumed once by the next successful sync, after
  which normal incremental synchronization resumes.
- Adds versioned reconciliation checkpoints and archived ownership evidence. Old
  files are neither deleted nor moved when a source is reset or rebound.
- Keeps folder migration out of scope: existing folder names remain read-only.

This candidate requires the complete local gate plus Windows and macOS owner review
before publication.

## [0.2.0rc13] - 2026-10-22

### Graphical course reconfiguration

- Adds a PySide6 editor for an existing profile. Current courses, Canvas/Ed mappings,
  folder names, content selections, file-size policy, and initial-download choices are
  loaded from the saved configuration instead of starting from an empty setup.
- Allows adding and removing courses and safely remapping sources that have no sync
  history and whose existing course folder is absent or empty. Initialized or
  non-empty mappings remain locked with a user-facing reason.
- Saves only the reviewed configuration diff. Every save rechecks configuration,
  state, and folder evidence; it never moves, deletes, or downloads course files.
- Opens graphical reconfiguration from the daily menu and new profile launchers,
  while retaining the terminal `configure` command as a fallback.

### Compatibility and CI

- Declares Python 3.10–3.14 support while upstream MCP remains incompatible with
  Python 3.15, and fixes platform-neutral output-root fixtures in the CI suite.

This candidate still requires the release gate, remote CI, and Windows/macOS owner
review before publication.

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
