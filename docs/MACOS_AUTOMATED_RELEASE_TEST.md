# macOS Automated Release Test Matrix

> Target: the repository owner's Mac and private branch
> `hy/phase-3-4-portability`. This matrix performs no real Canvas/Ed request,
> course download, Git mutation, or publication.

## 1. Use a clean private clone

Do not run the release matrix in the daily checkout that contains credentials,
state, course output, or personal launchers. Create a new clone if one is not
already available:

```zsh
mkdir -p ~/Developer
cd ~/Developer
git clone --branch hy/phase-3-4-portability --single-branch \
  https://github.com/LHYubiquitous/canvas-ed-mcp.git canvas-ed-mcp-rc-test
cd canvas-ed-mcp-rc-test
```

The script fails closed if the checkout is dirty or contains known private files.
It never runs `git clean`, `git reset`, `git stash`, or another command that could
change existing work.

## 2. Run the complete automated matrix

`uv 0.9.30` must already be installed as a user-level tool; the runner rejects a
different release so that lock/export semantics remain identical to the reviewed CI.
From the clean clone, run one command:

```zsh
uv run --no-project --python 3.15 \
  python -B ci/macos_release_matrix.py
```

The default matrix may take a while. It creates isolated Python 3.10 and 3.15
environments in a temporary directory and runs:

1. clean-clone, private-file, Git ancestry, lockfile and version preflight;
2. locked install, CLI/doctor, dependency validation, and the full synthetic suite
   on Python 3.10 and 3.15;
3. wheel/sdist build, distribution audit, release-metadata audit, and two identical
   clean-source builds;
4. clean installation of the exact wheel and audited source on both Python bounds,
   plus exact sdist installation on Python 3.15;
5. installed CLI and MCP stdio protocol smoke outside the checkout;
6. a native macOS Keychain round-trip using a random synthetic value, with cleanup
   attempted in `finally` and verified before the stage passes.

The Keychain stage may display a native permission prompt. Approve it only when it
names the expected test Python process from this run. The synthetic value is never
placed in argv, output, or the report.

On completion, the terminal prints `PASS`, `FAIL`, or `DEGRADED` and the absolute
path to a status-only report under:

```text
artifacts/macos-owner-device/<UTC timestamp>/macos-release-report.md
```

The report contains stage names, exit codes, durations, commit, OS, and architecture.
It intentionally contains no command output, credential, course identity, provider
payload, user-home path, or signed URL. `artifacts/` is ignored by Git.

## 3. Useful modes

Preview the matrix on any operating system without changing anything:

```zsh
uv run --no-project --python 3.15 \
  python -B ci/macos_release_matrix.py --plan
```

Use only local `uv` caches:

```zsh
uv run --no-project --offline --python 3.15 \
  python -B ci/macos_release_matrix.py --offline
```

Skip Keychain only when the prompt cannot be handled. This intentionally produces
`DEGRADED`, not `PASS`:

```zsh
uv run --no-project --python 3.15 \
  python -B ci/macos_release_matrix.py --skip-keychain
```

Override the Python matrix by repeating `--python`. For example, a diagnostic
single-version run is:

```zsh
uv run --no-project --python 3.15 \
  python -B ci/macos_release_matrix.py --python 3.15
```

## 4. Manual boundary status

The matrix does not automate these actions. The table records the separately
confirmed owner-device result where one now exists:

| Check | Current result | Evidence or reason |
|---|---:|---|
| Native MCP host restart and `server_status` | `PASS` | Owner confirmed the no-provider profile in Codex Desktop on 2026-10-14 |
| Canvas/Ed real-account catalogs | `NOT_RUN` | Requires separate explicit authorization and configured credentials |
| Private legacy actual sync | `NOT_RUN` | Performs network requests, downloads, and state writes |
| Public remote/tag/Release/PyPI | `NOT_RUN` | P5-06 publication authorization is separate |

## Recorded owner-device run

On 2026-10-14, commit `c63e0a4` passed all 59 automated stages on an
arm64 Mac with Python 3.10.19 and 3.15.0b3. The native Keychain round-trip and
cleanup both passed. A separate no-credential Codex MCP-host check also passed:
only `server_status` was exposed, it reported read-only mode with zero providers
and zero courses, and no output directory was created. Real-account catalogs,
private legacy sync, and publication remain `NOT_RUN`.

Use `docs/MACOS_MANUAL_RELEASE_TEST.md` after the automated matrix for the applicable
owner-device checks. Never treat an installed MCP protocol smoke as proof that a GUI
host was configured, and never treat synthetic tests as a real-account verification.

## 5. Failure handling

The first failed required stage stops dependent work and still writes the status-only
report. Keep the clone for diagnosis and share only the failed stage name, exit code,
macOS/Python version, and sanitized error category. Do not attach tokens, Keychain
screenshots, course identifiers, course material, raw responses, or signed URLs.
