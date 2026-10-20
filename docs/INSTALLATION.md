# Cross-platform installation and local operation

> Scope: the portable read-only MCP package and its configuration primitives. The
> portable `canvas-ed-mcp sync` command supports explicitly configured **Canvas and
> Ed** sources; `course-sync` remains a safe reserved entry point. The legacy
> repository launcher remains the only owner-device downloader with real-content
> evidence. Never paste a token
> into a repository file, shell history, MCP JSON, screenshot, or support log.

## 1. Supported runtime and installation

Python 3.10–3.15 is the declared range. Use an isolated environment or `pipx`; do
not install as an administrator/root user. From a reviewed local checkout:

```text
python -m venv .venv
```

Activate it with `.venv\Scripts\Activate.ps1` on Windows PowerShell, or
`source .venv/bin/activate` on macOS/Linux, then install the package:

```text
python -m pip install --upgrade pip
python -m pip install .
```

For a built artifact, replace `.` with the exact reviewed `.whl` path. Optional
Gradescope support is installed separately with `python -m pip install
".[gradescope]"`; it remains disabled unless explicitly configured.

Platform notes:

- **Windows:** use a normal per-user PowerShell/Terminal. The secure backend should
  normally report `keyring.backends.Windows.WinVaultKeyring`.
- **macOS:** use the system Keychain backend. The first access can trigger a Keychain
  permission prompt tied to the selected Python executable.
- **Linux desktop:** install/configure a Secret Service or KWallet backend before
  storing credentials. A headless machine often has no unlocked secure keyring;
  use a process-scoped environment reference instead of enabling a plaintext backend.

## 2. Paths and output ownership

Run the following command to print the paths resolved by `platformdirs` for the
current OS and profile; it does not create them or reveal a secret:

```text
canvas-ed-mcp doctor --show-paths
```

Configuration, state, logs, and cache use separate per-user platform locations.
Typical roots are `%LOCALAPPDATA%\canvas-ed-mcp` on Windows,
`~/Library/Application Support/canvas-ed-mcp` plus the matching macOS state/log/cache
locations, and the XDG config/state/cache locations on Linux. Treat the command
output—not these examples—as authoritative.

Course output is different: it belongs under the output root the user explicitly
chooses during setup. Do not point it at this source checkout, a config/state folder,
or a shared directory without reviewing ownership and backup behavior.

## 3. Credentials

First check only backend readiness:

```text
canvas-ed-mcp doctor
```

When `keyring.available` is `true`, store a Canvas token interactively. The command
prompts without requiring the token in argv:

```text
python -m keyring set canvas-ed-mcp/default canvas
```

Reference that entry in config as
`keyring:canvas-ed-mcp/default/canvas`. Use separate accounts such as `ed`,
`gradescope-email`, and `gradescope-password` rather than reusing one entry.
Delete a retired entry with `python -m keyring del SERVICE ACCOUNT` after its remote
token/password has been revoked.

For CI or a genuinely headless session, config may contain an environment reference
such as `env:CANVAS_API_TOKEN`. Inject the value only into the MCP/sync process and
clear it when the session ends. The variable name is configuration; the value must
never be committed or copied into host configuration examples.

If `doctor` reports `unsafe_backend` or `missing_dependency`, stop before any network
operation. Do not enable plaintext or Null keyring backends as a workaround.

## 4. Launching and MCP host configuration

The platform-neutral server command is:

```text
canvas-ed-mcp serve --profile default
```

For an MCP desktop host, resolve the executable with `Get-Command canvas-ed-mcp` on
Windows or `command -v canvas-ed-mcp` on macOS/Linux. Configure that absolute
executable with arguments `serve --profile default`. Keep credentials in the OS
keyring; do not put token values in the host JSON.

For scheduled execution, use the same console entry point and the intended user
account: Task Scheduler on Windows, `launchd` on macOS, or a user-level `systemd`
timer on Linux. No system-wide install is required. The repository's personal
absolute-path launchers are not part of the portable interface.

## 5. Safe smoke checks and Canvas/Ed sync preview

These local commands perform no provider request and do not need credentials:

```text
canvas-ed-mcp --version
canvas-ed-mcp --help
canvas-ed-mcp doctor --show-paths
course-sync --help
```

`course-sync --help` only verifies entry-point installation; it does not prove that
the portable downloader is connected. On 2026-10-20, private CI for rc10 commit
`f178c27` passed on Windows, macOS, and Linux across Python 3.10–3.15. It covered
clean wheel and source installation, synthetic tests, installed entry points,
deterministic distributions, lock/SBOM freshness, dependency checks, and installed
MCP protocol smoke. Windows owner testing also covered Canvas/Ed setup,
configuration, material sync, due reports, and the double-click menu. macOS owner
testing covered the native keyring, generated `.command` launchers, MCP host, real
Canvas catalog, material sync, and due reports. Linux owner-device keyring and
desktop-launcher checks remain **NOT_RUN**.

The portable sync path supports explicitly configured **Canvas and Ed** sources.
For an existing profile, preview interactive changes before committing them:

```text
canvas-ed-mcp configure --profile PROFILE --dry-run
canvas-ed-mcp configure --profile PROFILE
```

Existing mappings are retained unless removal is explicitly confirmed. Provider
discovery is opt-in; an unselected or undiscovered provider resolves no credential and
makes no request. Detaching a source changes config ownership only and retains local
output and historical state.

Start with one explicitly selected course and `--dry-run`; it reads the configured
content catalog but does not write
state, cache, or course output:

```text
canvas-ed-mcp sync --profile PROFILE --course LOGICAL_COURSE_KEY --dry-run
```

It prints a JSON summary with actions and stable issue codes, never a token or raw
provider exception. Remove `--dry-run` only after reviewing that summary and backing
up the selected output folder. Real Ed content has the scoped result
`HUMAN_VERIFIED_WITH_WARNINGS`; real Canvas content is
`HUMAN_VERIFIED_WITH_WARNINGS` after owner review of images and desktop/narrow HTML
layout; the confirmed course-wide Files 403 remains partial coverage. `course-sync`
still performs no sync action, and the private legacy launcher remains a separate
compatibility path.

## 6. Daily-use terminal app and launchers

A committed `setup` or `configure` installs seven native, profile-bound launchers in
`<output root>/Canvas Ed MCP Launchers` by default. Windows receives `.cmd`, macOS
receives executable `.command`, and Linux receives executable `.sh` files:

```text
canvas_ed_app
course_material_sync
this_week_due
this_semester_due
configure_courses
health_check
open_course_folder
```

Double-click `canvas_ed_app` to use the English numbered menu. The material-sync menu
action always runs a dry-run first and applies only after a separate `y` confirmation.
The direct `course_material_sync` launcher is intentionally the one-click apply path.
Due launchers create reports under `<output root>/Due Dates`; they reuse identical
bytes and otherwise create `--r002`, `--r003`, and later siblings. They never replace
an earlier report.

If a launcher was edited, installation preserves it and writes the reviewed candidate
with a `.new` suffix. Repair or install launchers again with:

```text
canvas-ed-mcp launchers install --profile PROFILE
```

Schema-v1 profiles load with safe schema-v2 daily-use defaults without rewriting the
configuration file. Optional `daily_use.due` settings can provide an exact semester
label, explicit non-overlapping teaching periods, reviewed local Unit Outline Markdown,
Gradescope course mappings, and confirmed Outline-title overrides. These mappings are
exact: similarly named courses or assessments are never merged automatically.

Due collection treats configured Canvas/Ed sources as required. A partial/failed
refresh uses that source's last complete snapshot; without one, no new report is
published. Gradescope mappings and local Outline files are optional by default, so
their failure is reported without erasing Canvas/Ed history. Set `required = true` on
a `gradescope_courses` entry, or list an Outline path in both `unit_outline_files` and
`required_outline_files`, when that optional source must block publication unless a
last complete snapshot exists. Semester regeneration preserves marker-backed `[x]`
checkboxes and indented manual notes in the new versioned sibling.

## 7. Updating, rollback, and uninstall

Before updating, keep the previous wheel and back up only the non-secret config/state
files shown by `doctor --show-paths`. Install a reviewed replacement with `python -m
pip install --upgrade PATH_TO_WHEEL`; re-run the safe smoke checks before any provider
access. Roll back with `python -m pip install --force-reinstall PREVIOUS_WHEEL`.

`python -m pip uninstall canvas-ed-mcp` removes the installed package, not course
output, platform config/state, or OS-keyring entries. Review those locations and
remote token revocation separately; never delete them as part of an automated
uninstall.
