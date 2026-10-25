# Release and Security Audit

## 0.2.0rc17 release gate

Status: **PASS — PRIVATE GITHUB PRERELEASE PUBLISHED**
Audit date: 2026-10-26

`0.2.0rc17` introduces the CourseHarbor App Shell, graphical Canvas/Ed credential
workflow, independent due-date views/exports, per-course sync reports and folders,
profile lifecycle, faster read-only material preview, and the first self-contained
Windows onedir candidate. The public `v0.2.0rc16` release remains immutable; this rc17
release is confined to the private `Canvas-Ed-CourseHarbor` repository.

| Gate | rc17 result |
|---|---|
| Full automated suite | `PASS`: 684 tests, 31 documented skips; Python 3.10 version contract passed |
| Credential boundary | `PASS`: typed application service, fake keyring/provider tests, masked transient input, provider-isolated read-only validation |
| Deterministic source/wheel/sdist | `PASS`: exact allowlists, metadata/SBOM, private material/path scan, two-build byte comparison |
| Windows portable structure | `PASS`: 878 files / 156,586,733 bytes; GUI subsystem 2, CLI subsystem 3, exactly one `qwindows.dll`, both executables carry icon resources |
| Windows dependency/keyring smoke | `PASS`: QtCore, HTTP/SSL, CLI help, and `Windows.WinVaultKeyring` readiness; no credential read/write |
| Windows relocation smoke | `PASS`: GUI and CLI exited 0 from a copied directory outside the repository |
| Windows visible frozen App Shell | `PASS`: owner confirmed all requested visible routes behaved normally on 2026-10-26 |
| Application icon assets | `PASS`: runtime PNG, seven-size Windows ICO, and ten-file macOS iconset validated |
| GitHub tag/prerelease | `PASS`: private `v0.2.0rc17` prerelease in `LHYubiquitous/Canvas-Ed-CourseHarbor` |
| macOS `.app`/DMG, signing and notarization | `NOT_RUN`: follows this Windows-first release |
| Windows/macOS signing, installer upgrade/uninstall | `NOT_RUN` |
| PyPI/package-index publication | `NOT_RUN / NOT AUTHORIZED` |

The Windows asset is an unsigned portable onedir archive, not an installer. The bundle
audit rejects embedded repository, user-directory, interpreter, or build paths; complete PEM private
keys, unsafe filenames, symlinks/junctions, unexpected PE subsystems, and missing or
duplicate Qt platform plugins. Build dependency discovery runs with an isolated PATH so
unrelated owner-device tools cannot supply same-named DLLs. No Token, private course
content, profile config, state, launcher, or user data is included in the assets.

## 0.2.0rc16 release gate

Status: **PASS — PUBLIC GITHUB PRERELEASE PUBLISHED**
Audit date: 2026-10-25

`0.2.0rc16` supersedes the failed, unpublished rc15 packet. It preserves configured
course folder/code/name/term metadata in `SyncSourceSummary`, presents human-readable
course and provider labels in the dashboard and apply confirmation, and rebuilds the
macOS runner without the rc15 quote corruption.

| Gate | rc16 result |
|---|---|
| Root-cause application regression tests | `PASS`: replacement-window teardown 30/30, complete setup UI module 10/10, then the full release suite |
| Complete public release suite | `PASS`: 474 non-GUI tests plus 37 process-isolated GUI tests; 2 documented platform skips in the Windows run |
| Exact wheel build and distribution audit | `PASS`: private CI deterministic build/audit/install job |
| macOS exact-wheel automated and human label gate | `PASS`: macOS 26.5.2 arm64, Python 3.14.8, exact wheel SHA-256 `3dc625712651d37661a8e3d19ea9debd3d4212ba47dc9359c290ea1d36e35dec` |
| macOS real local Apply | `NOT_RUN`: not required for this presentation-only repair |
| Remote Windows/macOS/Linux CI | `PASS`: private release-gate [run 40104529592](https://github.com/LHYubiquitous/Canvas-Ed-CourseHarbor/actions/runs/40104529592), private final HEAD [run 40104851327](https://github.com/LHYubiquitous/Canvas-Ed-CourseHarbor/actions/runs/40104851327), and public release-source [run 40106666647](https://github.com/LHYubiquitous/canvas-ed-mcp-public/actions/runs/40106666647) |
| GitHub tag/prerelease | `PASS`: [v0.2.0rc16](https://github.com/LHYubiquitous/canvas-ed-mcp-public/releases/tag/v0.2.0rc16), tag target `620b6776fe06d92b3d188f70937afa28c5a03237` |
| PyPI/package-index publication | `NOT_RUN / NOT AUTHORIZED` |

An earlier documentation-only head exposed a nondeterministic Windows/Python 3.14
test-fixture teardown failure after all setup-window assertions had passed. The test
replaced one Qt window without first destroying its wrapper; delayed collection could
then terminate the process with `0xC0000005`. Commit `4346a65` makes that ownership
transition deterministic. The runtime source, package metadata, dependency locks, and
SBOM are unchanged from the owner-tested runtime inputs; only release documentation and
the test module changed.

The final private matrix passed on Windows and macOS at Python 3.10/3.14 and on Linux at
every declared Python version from 3.10 through 3.14. Both Windows jobs ran the real
PySide6 test modules in isolated processes, including the setup-window focus-delivery
and replacement-window teardown regressions. The distribution job
then passed deterministic packet construction, public manifest and metadata audits,
exact-wheel installation, clean-source installation, console-entry-point checks,
and MCP protocol smoke tests. The owner-device macOS gate then passed packet integrity,
exact-wheel install, dependency, contrast, read-only preview, human-readable Course and
Source labels, confirmation labels/default `No`, and normal dashboard exit. The owner
selected `No`; no local Apply or provider write occurred. An initial attempt failed
closed on an expired local Canvas credential; after the owner replaced it directly in
macOS Keychain, the exact unchanged candidate passed the complete gate.

## 0.2.0rc15 release gate

Status: **FAIL — NOT PUBLISHED**
Audit date: 2026-10-23

`0.2.0rc15` supersedes the unpublished rc14 wheel after macOS owner testing found
that ordinary and confirmation-dialog button labels could inherit a white system
foreground while the application supplied a white button surface. The candidate
adds one explicit dark foreground rule and macOS palette regression tests; sync,
due, MCP, provider, state, and storage behavior are unchanged.

| Gate | rc15 result |
|---|---|
| Returned macOS evidence packet integrity and secret scan | `PASS` locally |
| Root-cause regression test | `PASS`: reproduced before fix; fixed contract passes |
| Targeted Windows application/UI tests | `PASS`: 17 passed, 3 documented skips |
| Complete local synthetic suite | `PASS`: 583 tests, 13 documented skips |
| Locked pylock/SBOM audit | `PASS` with reviewed `uv 0.11.28` |
| Deterministic wheel, sdist, clean-source rehearsal | `PASS`: 64 / 142 / 170 entries |
| Windows Python 3.10 exact-wheel install | `PASS`: GUI extra, dependency check, version, entry points, and UI imports |
| macOS exact-wheel automated checks | `PASS` only after manually correcting the delivered runner |
| Human Course/Source readability | `FAIL`: internal key, hostname, and numeric ID were shown |
| Delivered runner executed unchanged | `FAIL`: duplicated single quotes caused exit 1 before GUI startup |
| macOS real local Apply | `NOT_RUN`: not required for the rc15 style-only change; rc14 apply evidence remains historical |
| Remote Windows/macOS/Linux CI | `NOT_RUN` |
| GitHub tag/prerelease | `NOT_RUN / BLOCKED` |

The final rc15 packet remained blocked even though the wheel's automated product
checks passed after a temporary runner repair. `SyncSourceSummary` discarded existing
course code/name/term metadata, so the dashboard and confirmation path used internal
identity values. The delivered runner also duplicated single quotes in its `awk` and
Python command arguments and exited before GUI startup. Existing evidence is retained
as a failed gate; no real local Apply, public tag, or public release was performed.

## 0.2.0rc14 release gate

Status: **IN PROGRESS — NOT PUBLISHED**
Audit date: 2026-10-22

`0.2.0rc14` adds non-destructive graphical course reconfiguration. Existing sources
may be deactivated, reactivated, relinked, or assigned a one-time next-sync mode.
Downloaded files are preserved in place; superseded state becomes diagnostic archive
evidence, and folder migration remains intentionally unsupported.

| Gate | rc14 result |
|---|---|
| Targeted config/state/engine/controller/UI tests | `PASS` locally |
| Complete local synthetic suite | `NOT_RUN` |
| Locked pylock/SBOM and deterministic artifacts | `NOT_RUN` |
| Windows graphical owner review | `NOT_RUN` |
| macOS graphical owner review | `NOT_RUN` |
| Remote Windows/macOS/Linux CI | `NOT_RUN` |
| GitHub tag/prerelease | `NOT_RUN / NOT AUTHORIZED` |

## 0.2.0rc13 release gate

Status: **IN PROGRESS — NOT PUBLISHED**
Audit date: 2026-10-22

`0.2.0rc13` adds config-only graphical reconfiguration for existing profiles and
temporarily caps declared Python support at 3.14 because the pinned MCP runtime does
not yet import correctly on Python 3.15. The editor never migrates course folders:
initialized or non-empty mappings are locked, and eligible remapping is rechecked at
save time.

| Gate | rc13 result |
|---|---|
| Local synthetic suite | `PASS`: 530 tests, 10 documented skips |
| Locked pylock/SBOM audit | `PASS` with reviewed `uv 0.11.28` |
| Deterministic wheel, sdist, clean-source packet | `PASS`: 58 / 130 / 158 entries |
| Windows Python 3.10 exact-wheel install | `PASS`: GUI extra, version, configure entry point, and reconfiguration imports |
| Windows graphical reconfiguration owner review | `NOT_RUN` |
| macOS graphical reconfiguration owner review | `NOT_RUN` |
| Remote Windows/macOS/Linux CI | `NOT_RUN` |
| GitHub tag/prerelease | `NOT_RUN` |
| PyPI/package-index publication | `NOT_RUN / NOT AUTHORIZED` |

## 0.2.0rc12 release gate

Status: **LOCAL AUTOMATED RELEASE GATE PASSED**
Audit date: 2026-10-20

`0.2.0rc12` is a documentation-only successor to the published, immutable
`0.2.0rc11` release. It adds a complete Simplified Chinese README, documents the
Canvas manual-token settings path and Australian Ed API-token page, and expands
installation, daily-use, MCP, update, rollback, and troubleshooting guidance. It
does not change synchronization, due-report, launcher, or MCP runtime behavior.

The public-source allowlist contains 127 files. Fresh local verification completed
332 synthetic tests with one expected Windows symlink-privilege skip, regenerated and
verified the locked pylock/SBOM, and produced deterministic 42-entry wheel, 99-entry
sdist, and 127-file clean-source artifacts. Existing rc9–rc11 owner-device and
remote-CI evidence remains historical supporting evidence, not a claim that rc12 has
already been rerun on those devices.

| Gate | rc12 result |
|---|---|
| Local synthetic suite | `PASS`: 332 tests, 1 expected Windows symlink skip |
| Locked pylock/SBOM audit | `PASS` with reviewed `uv 0.11.28` |
| Deterministic wheel, sdist, clean-source packet | `PASS`: 42 / 99 / 127 entries |
| Windows/macOS real behavior | Historical rc9–rc11 evidence only; rc12 rerun `NOT_RUN` |
| Remote Windows/macOS/Linux CI | `PASS` at commit `7340b14`; [workflow run 39411378014](https://github.com/LHYubiquitous/Canvas-Ed-CourseHarbor/actions/runs/39411378014) |
| GitHub tag/prerelease | `NOT_RUN` |
| PyPI/package-index publication | `NOT_RUN / NOT AUTHORIZED` |

## 0.2.0rc11 release gate

Status: **AUTOMATED RELEASE GATE PASSED**
Audit date: 2026-10-20

This section is the immutable build-time evidence snapshot. The public
[GitHub Release](https://github.com/LHYubiquitous/canvas-ed-mcp-public/releases/tag/v0.2.0rc11)
is the authoritative publication record; PyPI publication is outside this release.

`0.2.0rc11` is a documentation and package-metadata successor to the owner-tested
`0.2.0rc10` candidate. It does not change synchronization, due-report, launcher, or
MCP behavior. It updates the public README, current verification record, candidate
version, and public project links.

The candidate is built from the exact 127-file `release-manifest.txt` allowlist. The
private development history, credentials, state, course content, outputs, personal
launchers, and legacy root tools are outside this boundary.

### Current evidence

| Gate | rc11 result |
|---|---|
| Windows owner-device Canvas/Ed material sync and rendering | `HUMAN_VERIFIED_WITH_WARNINGS` |
| Windows daily terminal app and due reports | `PASS` on the rc7–rc10 behavior line |
| macOS automated owner-device matrix | `PASS` for rc9; rc11 owner rerun `NOT_RUN` |
| macOS launchers, MCP host, real Canvas catalog, material sync and due reports | `PASS` on rc9/rc10 |
| rc10 private CI: Windows/macOS/Linux, Python 3.10–3.15 | `PASS` at commit `f178c27` |
| rc11 local synthetic suite | `PASS`: 331 tests, 1 expected Windows symlink skip |
| rc11 lock/SBOM and deterministic packet audit | `PASS` with reviewed `uv 0.11.28` |
| rc11 Windows Python 3.14 wheel and clean-source installs | `PASS`: 42 packages, CLI/MCP smoke passed |
| rc11 private remote CI | `PASS`: Windows/macOS/Linux, Python 3.10–3.15, distribution build and audit |
| Public repository, tag and GitHub prerelease | Authoritative status is recorded by the linked GitHub Release |
| PyPI/package-index publication | `NOT_RUN / NOT AUTHORIZED` |

The known Canvas course-wide Files `403` remains partial coverage; module-linked
content was reviewed successfully. Ed remains an unofficial schema-sensitive
integration, and its accepted rendering warnings remain visible in the result.
Native owner-device Linux keyring and desktop-launcher checks remain `NOT_RUN`.

The sections below retain the historical `v0.1.0rc1` audit for provenance. They are
not evidence for rc11.

## Historical v0.1.0rc1 audit

### Release and Security Audit — 0.1.0rc1

Status: **PUBLIC RELEASE CANDIDATE PUBLISHED — GITHUB ONLY**
Audit date: 2026-10-14
Scope: the explicit public-source allowlist, wheel, sdist, dependency inventory,
and reachable Git history of the private development repository.

## 1. Executive conclusion

No unresolved Critical, High, or Medium security finding remains in the audited
public candidate. The current private repository and its reachable history are not
approved as a public repository: a public project must instead be initialized from
the deterministic, audited clean-source archive produced by `ci/release_gate.py`.

After this audit, the user explicitly authorized the public destination and GitHub
release scope. The 103-file candidate was published as clean history at
`LHYubiquitous/canvas-ed-mcp-public`, tagged `v0.1.0rc1`, and released as a GitHub
prerelease. PyPI/package-index publication remains unauthorized and `NOT_RUN`.

## 2. Findings and disposition

### Critical

None found.

### High

- **SEC-001 — Private development history is not a public-release surface
  (resolved by design).** The repository intentionally retains ignored credentials,
  state, course output, owner launchers, and tracked legacy compatibility code. The
  public candidate is therefore selected by a fail-closed allowlist rather than by
  copying or publishing the working tree. `select_public_files()` rejects missing
  required files and symlinks; `audit_public_files()` rejects private keys, literal
  secrets, absolute home paths, and volatile signed URLs. A future public repository
  must start from this clean archive, not from the private repository history.

### Medium

- **SEC-002 — Mutable CI action tags could change build behavior (resolved).** All
  `actions/checkout` and `actions/setup-python` uses are pinned to reviewed full commit
  SHAs, while comments retain the human-readable major version.
- **SEC-003 — Dependency and license provenance was incomplete (resolved).** Exact
  dependency resolution is recorded in `uv.lock` and standard `pylock.toml`; the
  CycloneDX 1.5 inventory is `sbom.cdx.json`; license families and conditional
  packages are recorded in `THIRD_PARTY_NOTICES.md`. A locked environment audit found
  no known vulnerability and no installed package with unknown license metadata.

### Low / accepted limitations

- Ed uses an unofficial, schema-sensitive integration and Gradescope uses an
  optional unofficial scraper. Both are documented; Gradescope is disabled by
  default, and provider failures remain isolated.
- Canvas OAuth is not implemented. Personal access tokens are explicitly documented
  as a local-user path rather than a multi-user deployment mechanism.
- The portable `course-sync` entry point remains reserved and does not claim to
  replace the legacy downloader.

## 3. Verification evidence

| Gate | Result |
|---|---|
| Synthetic/unit suite | 226 tests passed; 1 Windows symlink-privilege test skipped |
| macOS owner-device matrix | 59/59 stages passed on arm64 with Python 3.10.19 and 3.15.0b3 at commit `c63e0a4`; native Keychain cleanup and MCP-host `server_status` passed |
| Public-source policy tests | exact manifest, scanner bypass regressions, path/version safety, deterministic archive and metadata consistency passed |
| Dependency lock | `uv lock --check` passed |
| Vulnerability audit | 56 applicable distributions audited; 0 known vulnerabilities |
| License audit | 57 installed distributions; 0 unknown license entries |
| SBOM | CycloneDX 1.5; 68 dependency components |
| Reachable-history private key/secret review | 7 pattern hits, all synthetic test canaries |
| Reachable-history absolute home paths | 0 hits |
| Reachable-history signed URLs | 36 hits, all reserved-domain synthetic fixtures/tests |
| Historically tracked denied credential/state/launcher paths | 0 paths |
| Package manifest | wheel and sdist passed explicit fail-closed audits |
| Clean install | wheel, sdist, and clean-source archive verification recorded in the external release manifest |

The dependency audit count is platform-marker dependent: seven lock/SBOM entries are
not installed on Windows/Python 3.13. Their licenses were cross-checked against their
official package metadata and included in `THIRD_PARTY_NOTICES.md`.

## 4. Release boundary

The clean-source candidate includes only the 103 paths in the reviewed
`release-manifest.txt`; any new eligible file under `ci/`, `docs/`, `src/`, or `tests/`
fails the gate until explicitly reviewed and added. It excludes the
legacy root server/downloader, personal launchers, credentials, local state, course
content and output, caches, build products, and two legacy-root tests whose subjects
are intentionally absent from the public source.

The archive uses sorted paths, normalized ownership/mode/mtime, a fixed source epoch,
and a gzip header without a local filename. The adjacent generated manifest and
SHA-256 file are the authoritative artifact records; the digest is deliberately not
embedded in this included document because that would make the archive self-referential.

## 5. Evidence not claimed

- Native owner-device Linux keyring prompts and launcher/MCP-host startup:
  **NOT_RUN**.
- The macOS Python 3.10/3.15 owner-device matrix, synthetic native Keychain
  round-trip/cleanup, Codex MCP-host startup, and host-side no-provider
  `server_status` check: **PASS**. The test made no real provider request and did not
  create course output.
- Real-account calls on macOS/Linux: **NOT_RUN**.
- Ed authenticated UI/schema confirmation beyond the authorized Windows read-only
  catalog smoke: **DEGRADED**.
- Public clean-history repository, public push, tag, and GitHub prerelease: **PASS**.
  Public `main` and tag CI both passed, and the uploaded archive digest matched the
  audited local SHA-256.
- PyPI/package-index publication: **NOT_RUN / NOT AUTHORIZED**.
