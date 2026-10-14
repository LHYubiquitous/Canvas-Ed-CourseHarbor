# Release and Security Audit — 0.1.0rc1

Status: **LOCAL RELEASE_CANDIDATE — NOT PUBLISHED**
Audit date: 2026-10-14
Scope: the explicit public-source allowlist, wheel, sdist, dependency inventory,
and reachable Git history of the private development repository.

## 1. Executive conclusion

No unresolved Critical, High, or Medium security finding remains in the audited
public candidate. The current private repository and its reachable history are not
approved as a public repository: a public project must instead be initialized from
the deterministic, audited clean-source archive produced by `ci/release_gate.py`.

This audit does not authorize a remote change, tag, GitHub Release, or package-index
publication. Those actions remain behind P5-06 and require an exact destination,
visibility, tag, and publication decision.

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
- Public repository creation, public push, tag, GitHub Release, or PyPI publication:
  **NOT_RUN / NOT AUTHORIZED**.
