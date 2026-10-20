# macOS Owner-Device Release Candidate Test

> Target: `canvas-ed-mcp 0.2.0rc12` on the repository owner's Mac.
> This checklist verifies native macOS behavior that GitHub-hosted runners cannot
> prove: Keychain prompts, the local MCP host, and the private legacy launcher.
> Record every unchecked item as `NOT_RUN`; do not infer a pass.

The preferred path for Sections 2–5 is now the one-command matrix in
`docs/MACOS_AUTOMATED_RELEASE_TEST.md`. Run it first; it covers the clean-clone,
Python 3.10/3.15 locked environments, full synthetic suite, build/distribution/
clean-source gates, isolated installed CLI/MCP smoke, and native Keychain canary.
Keep this document for the MCP host UI, optional real-account catalog, and private
legacy launcher checks that automation deliberately cannot authorize or observe.

## 1. Scope and safety boundary

Use a new clean clone of the private repository for Sections 2–6. Do not replace,
clean, reset, or reuse the existing daily-working checkout because it may contain
ignored credentials, state, course output, or uncommitted work.

- Private repository: `https://github.com/LHYubiquitous/canvas-ed-mcp.git`
- Branch: `hy/phase-3-4-portability`
- Verified baseline ancestor: `c305be28882dcedb3f93fd197a9218746c4d05d2`
- Expected package version: `0.2.0rc12`
- Result vocabulary: `PASS`, `FAIL`, `NOT_RUN`, or `DEGRADED`

Safety rules:

1. Never paste a Canvas/Ed token into Terminal arguments, repository files, MCP
   host JSON, screenshots, notes, or this checklist.
2. Do not copy `credentials_local.json`, `.course_update_state.json`, course
   materials, or the old personal launcher into the clean test clone.
3. Sections 2–5 perform no real provider request. Section 6 is optional read-only
   discovery. Section 7 covers the private legacy downloader separately.
4. Do not make this private repository or its Git history public. A public release
   must be initialized from the audited clean-source archive.

## 2. Create the clean Mac test checkout

Run in Terminal. Choose a new directory that does not already exist:

```zsh
mkdir -p ~/Developer
cd ~/Developer
git clone --branch hy/phase-3-4-portability --single-branch \
  https://github.com/LHYubiquitous/canvas-ed-mcp.git canvas-ed-mcp-rc-test
cd canvas-ed-mcp-rc-test
git status --short
git rev-parse HEAD
git merge-base --is-ancestor \
  c305be28882dcedb3f93fd197a9218746c4d05d2 HEAD
```

Expected results:

- `git status --short` prints nothing.
- `git rev-parse HEAD` is recorded below; it may be newer than the baseline.
- `git merge-base --is-ancestor ...` exits with status `0` and prints nothing.

- [ ] Clean clone: `PASS / FAIL`
- Tested commit: `________________________________________`

If the clone or ancestry check fails, stop. Do not test another fork, upstream, or
an old local copy and treat it as this release candidate.

After cloning, the automated path for Sections 3–5 is:

```zsh
uv run --no-project --python 3.15 \
  python -B ci/macos_release_matrix.py
```

Retain the commands below only as a transparent manual fallback. Do not record a
manual partial run as equivalent to a complete automated matrix.

## 3. Locked environment and offline-safe CLI smoke

Use the project-local environment. `uv` must already be available as a user-level
tool; do not perform a system-level installation solely for this test.

```zsh
uv sync --locked --all-extras --no-dev
uv run --locked --all-extras --no-dev canvas-ed-mcp --version
uv run --locked --all-extras --no-dev canvas-ed-mcp --help
uv run --locked --all-extras --no-dev canvas-ed-mcp doctor --show-paths
uv run --locked --all-extras --no-dev course-sync --help
uv pip check
```

Expected results:

- Version is exactly `canvas-ed-mcp 0.2.0rc12` or an equivalent display containing
  `0.2.0rc12`.
- `doctor --show-paths` reports macOS user locations and does not create course
  output or print any credential value.
- `course-sync --help` succeeds, but this only verifies the reserved compatibility
  entry point. Portable downloads use the reviewed `canvas-ed-mcp sync` command.
- `uv pip check` reports compatible packages.

- [ ] Locked install: `PASS / FAIL`
- [ ] CLI/version/path smoke: `PASS / FAIL`
- [ ] Dependency check: `PASS / FAIL`

## 4. Full synthetic suite and installed MCP protocol

The synthetic suite uses fixtures and temporary directories. It must not use real
provider credentials or write course output.

```zsh
uv run --locked --all-extras --no-dev \
  python -B -m unittest discover -s tests -p 'test_*.py'

rc_test_root="$(pwd -P)"
protocol_temp="$(mktemp -d)"
(cd "$protocol_temp" && \
  "$rc_test_root/.venv/bin/python" "$rc_test_root/ci/smoke_installed.py")
rmdir "$protocol_temp"
```

Expected results:

- The test command finishes with `OK`. Record the test and skipped counts exactly;
  do not convert a skip into a pass.
- The protocol command prints `installed wheel protocol smoke passed`.
- No output directory or provider credential is created by the protocol smoke.

- [ ] Full synthetic suite: `PASS / FAIL`
- Tests: `________`; skipped: `________`
- [ ] Installed MCP protocol smoke: `PASS / FAIL`

## 5. Native macOS Keychain round-trip

First inspect the backend without storing a real credential:

```zsh
uv run --locked --all-extras --no-dev canvas-ed-mcp doctor
uv run --locked --all-extras --no-dev \
  python -m keyring set canvas-ed-mcp/mac-smoke synthetic-check
```

At the password prompt, enter the non-secret canary `synthetic-smoke-only`. Approve
the macOS Keychain prompt only if it names the expected project Python executable.
Verify equality without printing the stored value, then delete the canary:

```zsh
uv run --locked --all-extras --no-dev python -c \
  'import keyring; print("keyring round-trip:", keyring.get_password("canvas-ed-mcp/mac-smoke", "synthetic-check") == "synthetic-smoke-only")'
uv run --locked --all-extras --no-dev \
  python -m keyring del canvas-ed-mcp/mac-smoke synthetic-check
```

Expected results:

- `doctor` reports `keyring.available` as true and does not report a plaintext or
  null backend.
- The equality check prints `keyring round-trip: True`.
- The synthetic entry is deleted after the test.

- [ ] Safe Keychain backend: `PASS / FAIL`
- [ ] Keychain prompt and round-trip: `PASS / FAIL`
- [ ] Synthetic entry deleted: `PASS / FAIL`

If the backend is unavailable or unsafe, stop. Do not enable a plaintext backend and
do not move a real token into a repository file as a workaround.

## 6. Owner-device MCP host and optional real-account read

### 6.1 MCP host startup — required when a configured profile exists

Resolve the executable:

```zsh
pwd -P
command -v uv
./.venv/bin/canvas-ed-mcp --version
```

In the Mac MCP host, configure:

- command: the absolute path ending in
  `canvas-ed-mcp-rc-test/.venv/bin/canvas-ed-mcp`;
- arguments: `serve`, `--profile`, `default`;
- environment: no token values and no `PYTHONPATH` override.

Restart the host, list the available tools, and call only `server_status`. Do not call
a provider tool yet. Expected: the server starts, the tool catalog matches the
configured providers, `server_status` succeeds, and no Keychain value is displayed.

- [ ] Native MCP-host startup: `PASS / FAIL / NOT_RUN`
- [ ] `server_status` only, no provider request: `PASS / FAIL / NOT_RUN`

If no portable `default` profile exists on the Mac, record `NOT_RUN`; do not copy a
credential-bearing config from another machine merely to satisfy this checkbox. The
protocol smoke in Section 4 remains valid but does not replace this device check.

### 6.2 Real-account catalog — optional, read-only

Run only if the intended Canvas instance and/or Ed account is already configured and
you choose to authorize a real read-only discovery request. Call only the applicable
course-catalog tool; do not download content and do not call any write-capable legacy
MCP tool. Record only `OK`, provider name, and an item count outside the repository.
Do not record course names, course IDs, raw payloads, URLs, or screenshots.

- [ ] Canvas catalog: `PASS / FAIL / NOT_RUN`
- [ ] Ed catalog: `PASS / FAIL / NOT_RUN`
- [ ] No raw provider payload retained: `PASS / FAIL / NOT_RUN`

## 7. Private legacy `.command` launcher regression

This section uses the existing personal Mac checkout—not the clean RC clone—because
the ignored legacy credential and state files must never be copied. It is a personal
workflow regression, not part of the public source candidate.

The relative launcher `run_course_sync.command` is preferred. The old
`Course Material Sync.command` contains a personal absolute project path and should
only be retained for compatibility.

Safe launcher checks that perform no provider request:

```zsh
cd /path/to/existing/private/canvas_ed_mcp
git status --short
zsh -n ./run_course_sync.command
./run_course_sync.command --help
```

Expected: syntax validation succeeds and help is displayed. Do not clean/reset the
working tree, and do not stage ignored personal files.

- [ ] Relative launcher syntax: `PASS / FAIL`
- [ ] Relative launcher help: `PASS / FAIL`
- Existing dirty working tree preserved: `YES / NO`

An actual launcher run is optional and **not dry-run**. It performs real Canvas/Ed
requests, can download files, and writes `.course_update_state.json`. Run it only
against the already intended personal output and course selection after reviewing
that exact target. Never use the clean RC clone as a substitute by copying secrets.

- [ ] Actual private sync: `PASS / FAIL / NOT_RUN`
- Exact target reviewed before execution: `YES / NO / NOT_RUN`
- User edits and existing output preserved: `YES / NO / NOT_RUN`

## 8. Completion record

Required release-device checks are Sections 2–5. Section 6.1 is required to change
the native owner-device MCP-host result from `NOT_RUN`; Section 6.2 remains an
explicitly scoped real-account choice. Section 7 is recommended because the private
legacy launcher is the current production downloader, but it is not evidence for the
portable/public package.

| Check | Result | Notes (no private data) |
|---|---|---|
| Clean private-branch clone | `________` | `________` |
| Locked install and CLI | `________` | `________` |
| Synthetic suite | `________` | `________` |
| MCP protocol smoke | `________` | `________` |
| Native Keychain | `________` | `________` |
| Native MCP host | `________` | `________` |
| Optional real catalogs | `________` | `________` |
| Private launcher | `________` | `________` |

Overall macOS result: `PASS / FAIL / DEGRADED`

Tester date/time and macOS version: `________________________________________`

On failure, retain the checkout and report only the failing command, exit status,
macOS/Python version, and sanitized error category. Do not attach tokens, Keychain
screenshots, course identifiers, course material, raw responses, or signed URLs.
