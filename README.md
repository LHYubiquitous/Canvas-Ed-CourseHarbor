# CourseHarbor

[English](README.md) | [简体中文](README.zh-CN.md)

[![Python 3.10–3.14](https://img.shields.io/badge/Python-3.10%E2%80%933.14-3776AB)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

CourseHarbor is a local desktop tool for students who use Canvas, Ed, or both. It
downloads course material into folders you choose, creates weekly and semester due
date reports, and provides read-only MCP access to configured courses.

`0.2.0rc19` is the current GitHub prerelease. Download the reviewed artifacts from the
[v0.2.0rc19 release](https://github.com/LHYubiquitous/Canvas-Ed-CourseHarbor/releases/tag/v0.2.0rc19).
The package is not published to PyPI. This release includes graphical token management,
the CourseHarbor App Shell, due-date views, sync-run reports, an audited Windows
portable bundle, and an audited arm64 macOS DMG. It also fixes a Windows UI Automation
cache defect that could freeze Courses when an older sync run was expanded. The DMG is
ad-hoc signed and not Apple-notarized.

## What it does

The installed package provides:

- a PySide6 first-run flow with masked Canvas/Ed token save-and-test, plus graphical
  course reconfiguration and native Windows, macOS, and Linux launchers;
- incremental Canvas and Ed material sync with dry-run preview and no-clobber storage;
- weekly TXT and semester Markdown due reports grouped in Sydney time;
- separate profiles for different semesters, accounts, and output folders;
- OS Keychain/Credential Manager references instead of tokens in configuration;
- explicit Canvas↔Ed course mapping—matching names never select a course for you;
- a read-only MCP server whose tools follow the providers enabled in the profile;
- optional Gradescope due-date support, disabled until explicitly configured.

The portable sync orchestrator supports explicitly configured Canvas and Ed sources.
Interactive setup supports Canvas-only, Ed-only, and explicit Canvas+Ed mappings;
interactive reconfiguration retains existing sources by default and requires explicit
confirmation before detaching or adding a source. `course-sync` is a safe compatibility
command and does not download anything.
The historical write-capable server and personal scripts are not included in the
public source candidate, wheel, or sdist.

## 1. Download and open the desktop app

Most users should download a desktop package from the
[v0.2.0rc19 release](https://github.com/LHYubiquitous/Canvas-Ed-CourseHarbor/releases/tag/v0.2.0rc19).
No separate Python installation is required for these packages.

### Windows 10/11 x86_64

1. Download `CourseHarbor-v0.2.0rc19-windows-x86_64-portable.zip`.
2. Extract the complete ZIP to a folder you intend to keep. Do not run the executable
   from inside the ZIP or move only one file out of the extracted folder.
3. Open `CourseHarbor.exe`.

The Windows package is an unsigned portable app, not an installer. Windows may show an
unrecognized-app warning. Compare the download with `SHA256SUMS` before running it.

### macOS on Apple silicon

1. Download `CourseHarbor-v0.2.0rc19-macos-arm64.dmg`.
2. Open the DMG and drag **CourseHarbor** into **Applications**.
3. Launch CourseHarbor from Applications.

This DMG supports Apple silicon (`arm64`) only. It is ad-hoc signed and not
Apple-notarized, so Gatekeeper acceptance is `DEGRADED`; verify `SHA256SUMS` before
opening it. Intel and universal builds remain `NOT_RUN`.

Python package installation, CLI/MCP use, and source builds are intentionally kept in
the separate [Python package and source-build section](#6-python-package-and-source-builds).

## 2. Configure Canvas and Ed API access

### Get a Canvas API token

1. Sign in to your institution's Canvas website.
2. Open **Account → Settings**. The direct path is normally
   `https://<your-canvas-host>/profile/settings`.
3. Scroll to **Approved Integrations** and select **New Access Token**.
4. Enter a purpose such as `canvas-ed-mcp`, choose the shortest practical expiry,
   and generate the token.
5. Copy the value when Canvas displays it. Canvas does not show that value again.

For the University of Sydney, the API base URL requested by setup is
`https://canvas.sydney.edu.au/api/v1`. If **New Access Token** is unavailable, the
institution has disabled self-service tokens; contact its Canvas support team rather
than trying to bypass that policy. See Instructure's official
[Canvas OAuth2: Manual Token Generation](https://developerdocs.instructure.com/services/canvas/oauth2/file.oauth#manual-token-generation)
guide.

### Get an Ed API token

1. Sign in to the same Ed account that can open the courses you want to sync.
2. Open **Settings → API Tokens** in the region where that account is signed in.
3. Create a dedicated token for `canvas-ed-mcp` and copy it once.

The Ed integration uses an unofficial, schema-sensitive API. Do not substitute a
browser cookie, SSO password, Canvas token, or copied browser request.
The Australian token page `https://edstem.org/au/settings/api-tokens` is the only
region-specific page verified for this project. Other Ed regions are `NOT_RUN`; use
the settings page reached from the signed-in account rather than guessing a hostname.

### Save and verify tokens in the desktop setup

Open CourseHarbor. On first run, choose the providers you use, enter the Canvas API base
URL when Canvas is enabled, and paste each token into its masked **API token** field.
Choose **Save and test**. The app first
checks that a secure native credential store is available, then performs one
provider-isolated, read-only course-list request. Only a successful token is saved to
the deterministic credential for that profile. The field is cleared when work starts;
the token is never copied to the clipboard, config, state, command arguments, or UI
status text.

Use **Test existing** after a restart or when diagnosing access. A blank replacement
field keeps the existing credential. Replacing a value requires a confirmation naming
the profile, provider, and local keyring target. A failed validation or cancellation
before persistence leaves the old credential unchanged. If a later configuration save
fails, the already verified keyring credential remains so setup can be retried.

`env:` credential references remain an advanced, externally managed and read-only
option for CLI/headless configuration. The application layer can test an existing
reference but cannot overwrite it; this first-run keyring UI does not create `env:`
references.

### Terminal fallback for advanced users

Check the operating-system keyring before setup:

```text
canvas-ed-mcp doctor --show-paths
```

The following fallback remains available for headless or recovery workflows. Store
only the providers you use; each command prompts for the value without placing it in
command history or the configuration file:

```text
python -m keyring set canvas-ed-mcp/default canvas
python -m keyring set canvas-ed-mcp/default ed
```

Use these references when the setup wizard asks for credentials:

```text
keyring:canvas-ed-mcp/default/canvas
keyring:canvas-ed-mcp/default/ed
```

Use the same profile name in all three places. For example, profile `semester-2`
uses service `canvas-ed-mcp/semester-2` and references
`keyring:canvas-ed-mcp/semester-2/canvas` and
`keyring:canvas-ed-mcp/semester-2/ed`. Never paste a token into TOML, a launcher, an
MCP host configuration, a screenshot, or a Git repository.

See [Authentication](docs/AUTHENTICATION.md) for Canvas and Ed personal API tokens,
revocation, OAuth boundaries, and headless environment references.

## 3. Finish first-run setup

After each enabled connection passes its read-only test, CourseHarbor discovers the
course catalogues and opens the reviewed setup flow. Canvas-only and Ed-only profiles
use **Select → Folders → Review**; **Map** appears only when both providers are enabled
and an explicit Canvas↔Ed relationship may be needed. Similar names never cause an
automatic pairing. Token fields are
masked and transient; configuration receives only deterministic references such as
`keyring:canvas-ed-mcp/default/canvas`. The final confirmation creates the new
configuration and any missing empty course folders—no course content, sync state,
credentials, or launchers. After that save
succeeds, the completion screen can explicitly install the daily-use shortcuts and
open either the daily dashboard or their folder. The first real sync remains a
separate confirmed action.

Library root selection is intentionally deferred to the Folders step. The root contains
course folders, `Canvas Ed MCP Launchers`, and due reports, so it is not only a launcher
directory. It must already exist before Review; setup does not silently create it. Each
new logical course can then use **Browse…** to select an existing direct
child. For example, choose Desktop as the Library root and select each existing Desktop
course folder independently. A selected folder is reused when it already exists; a
missing course folder is created when the reviewed configuration is saved. If a folder is outside the current root, change the root on
the same page and retry; no restart or repeated provider discovery is required. Existing
configured roots and course folders remain read-only because configuration never moves
files.

The setup table shows every discovered course and asks which ones to include. Choose
`baseline_from_now` to record the current state and download only later changes, or
`import_all` to download the selected existing material on the first real sync.
Review shows the exact local layout before **Save configuration** writes anything.

To add, remove, remap, or change course settings later, open **Courses** and choose the
configuration action. The editor starts with the
saved courses selected and shows the exact config diff before saving. Existing folder
names are read-only in this release. A mapping can be changed only when that source
has no sync history and its current course folder is absent or empty; otherwise the
UI explains why it is locked. Saving never moves or deletes files and never starts a
sync. Removing a source from configuration keeps its downloaded files and historical
state.

## 4. Use the CourseHarbor App Shell

CourseHarbor opens First run when no profile exists, opens the only healthy profile
directly, or shows **Profile chooser** when multiple profiles exist or one needs
attention. Profile inspection is local-only and does not contact Canvas or Ed.

### Library

Choose **Preview course material** to perform read-only discovery. Review the exact
per-course and per-provider actions, expand any warning summary, then choose **Apply
reviewed changes**. Preview never writes course content; Apply writes only the actions
shown in that reviewed plan. The operation stays off the Qt event loop and exposes
progress and cancellation.

### Due dates

**This week** and **Semester** are independent views. Each has its own **Load** action,
export folder, and explicit **Export** action, so loading a report does not write it and
the two report types may use different destinations. Published reports are create-only:
changed content receives a numbered revision rather than overwriting the previous file.

### Courses

Expand a course to see retained sync runs, then expand a run to see every file added or
updated and its exact local destination. This local report history contains no tokens or
raw provider exception text. Course configuration is also opened from this area.

### Connections

Use **Test existing** to diagnose the stored Canvas or Ed credential with a read-only,
provider-isolated request. To rotate a token, paste it into the masked **Replacement
token** field and choose **Save and test**. Leave the field blank to retain the current
credential. `env:` references are externally managed and cannot be replaced in the UI.

### Health & About and local files

**Health & About** shows non-secret package, profile, path, and keyring readiness. **Open
local library** opens the configured library root; each course folder already exists
after setup. Closing the app during background work requests cooperative cancellation
before provider sessions are released.

Use **Switch profile** to return to Profile chooser. **Remove profile…** archives only
the selected profile config after targeted confirmation; it does not delete downloaded
course files, native credentials, sync state, logs/cache, or revoke a remote token.

### Optional daily-use shortcuts

Setup can create native shortcuts under `<library root>/Canvas Ed MCP Launchers`.
Existing user-edited shortcuts are never overwritten; a changed managed candidate is
written with a `.new` suffix for review.

| Launcher | Result |
|---|---|
| `canvas_ed_app` | Opens the CourseHarbor App Shell |
| `course_material_sync` | Updates every configured course |
| `this_week_due` | Creates `Due Dates/this-week-due.txt` |
| `this_semester_due` | Creates a teaching-week Markdown checklist |
| `configure_courses` | Opens graphical course reconfiguration |
| `health_check` | Shows package, profile, path, and keyring status |
| `open_course_folder` | Opens the configured output folder |

Due reports are create-only: changed content receives a numbered revision instead of
overwriting the previous report. Semester reports preserve checked boxes and notes
attached to stable item markers.

## 5. Advanced CLI and MCP

The same functions are available as short CLI commands:

```text
canvas-ed-mcp sync --profile default --dry-run
canvas-ed-mcp sync --profile default
canvas-ed-mcp setup --profile default --dry-run
canvas-ed-mcp due week --profile default
canvas-ed-mcp due semester --profile default
canvas-ed-mcp configure-gui --profile default
canvas-ed-mcp configure --profile default --dry-run
canvas-ed-mcp launchers install --profile default
canvas-ed-mcp open-course-folder --profile default
canvas-ed-app --profile default
```

Canvas and Ed due dates work from the confirmed course mappings. Optional Gradescope
and local Unit Outline inputs require exact mappings in schema-v2 configuration; the
tool never guesses that similarly named courses or assessments are identical.

Launch the MCP server with:

```text
canvas-ed-mcp serve --profile default
```

Use the absolute installed executable in the MCP host with arguments
`serve --profile default`. Its tools are limited to the configured read-only surface:

- `server_status`
- `canvas_list_configured_courses` when Canvas is enabled
- `ed_list_configured_courses` and `ed_list_course_content` when Ed is enabled
- `gradescope_list_courses` and `gradescope_list_assignments` only after explicit
  Gradescope opt-in and optional dependency installation

Disabled providers resolve no credential and make no network request.

To preview one course instead of the whole profile:

```text
canvas-ed-mcp sync --profile default --course LOGICAL_COURSE_KEY --dry-run
```

Removing `--dry-run` writes local state and course output. It never writes to Canvas
or Ed. `course-sync` is a compatibility placeholder; use `canvas-ed-mcp sync`.

## 6. Python package and source builds

This section is for developers, CLI/MCP users, and platforms without a frozen desktop
package. It is not required for the Windows portable app or macOS DMG.

### Install the reviewed wheel

Python 3.10–3.14 is supported. Download
`canvas_ed_mcp-0.2.0rc19-py3-none-any.whl` from the release, then create an isolated
environment and install the GUI extra:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install .\canvas_ed_mcp-0.2.0rc19-py3-none-any.whl
.\.venv\Scripts\python.exe -m pip install "PySide6>=6.8,<7"
.\.venv\Scripts\courseharbor.exe
```

On macOS or Linux, use `python3 -m venv .venv`, then the equivalent
wheel and `PySide6>=6.8,<7` install commands through `./.venv/bin/python`.
The package is not published to PyPI.

### Build and run from source

```text
git clone https://github.com/LHYubiquitous/Canvas-Ed-CourseHarbor.git
cd Canvas-Ed-CourseHarbor
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[gui]"
.\.venv\Scripts\courseharbor.exe
```

On macOS or Linux, replace `py` with `python3`, then use `./.venv/bin/python` and
`./.venv/bin/courseharbor`. For the locked contributor environment, install `uv
0.11.28` and run:

```text
uv sync --locked --all-extras --group dev
uv run --locked python -B ci/run_release_tests.py --root .
```

The public release source is the audited clean-source archive, not the private
development history. Packaging details and exact verification boundaries are in
[Testing and operations](docs/TESTING_AND_OPERATIONS.md) and
[Release/security audit](docs/RELEASE_AUDIT.md).

## Known limitations and privacy

- Canvas course-wide Files access depends on instance permissions. Module-linked
  pages and files can still work when the broader Files endpoint returns `403`.
- Ed integration uses an unofficial, schema-sensitive API and reports partial items
  when the response shape changes.
- Gradescope due dates require the optional `gradescope` extra and explicit mapping.
- Native owner-device Linux keyring testing is still `NOT_RUN`.
- Tokens belong in the OS keyring, not source control or MCP host configuration.
- Course access does not grant redistribution rights for downloaded material.

## Troubleshooting and updates

- `authentication_failed` or HTTP `401`: replace the expired/revoked token in the
  keyring, then retry a dry run. Do not create a plaintext fallback.
- HTTP `403`: the signed-in account or institution has denied that endpoint. A new
  token usually does not change the account's permissions.
- `missing_keyring` or `unsafe_backend`: run `canvas-ed-mcp doctor --show-paths` and
  fix the native keyring before making provider requests.
- A due report is not published when a required Canvas/Ed source has no complete
  snapshot. Restore provider access and run it again.
- After upgrading the wheel, run `canvas-ed-mcp launchers install --profile default`
  so generated launchers point to the reviewed environment.

Keep the previous wheel for rollback. Full install, update, rollback, headless Linux,
and MCP host instructions are in [Cross-platform installation](docs/INSTALLATION.md).

Report software vulnerabilities through [SECURITY.md](SECURITY.md).

## Verification and release materials

The project runs synthetic tests across Windows, macOS, and Linux for Python
3.10–3.14. The release candidate also includes:

- `uv.lock` and the standard `pylock.toml` dependency lock;
- `sbom.cdx.json`, a CycloneDX 1.5 software bill of materials;
- [third-party notices](THIRD_PARTY_NOTICES.md);
- a fail-closed distribution and clean-source archive audit;
- a local clean-install and installed MCP protocol smoke test.

Exact verification boundaries and remaining `NOT_RUN` device checks are documented in
[Testing and operations](docs/TESTING_AND_OPERATIONS.md) and
[Release/security audit](docs/RELEASE_AUDIT.md).

## Development

Read [CONTRIBUTING.md](CONTRIBUTING.md) before proposing changes. Tests use synthetic
fixtures only:

```text
python -B -m unittest discover -s tests -v
```

Do not add live API calls to the default test suite.

## Chinese documentation

完整中文安装、API token 获取、首次配置、日常使用、MCP 与故障排查说明见
[简体中文 README](README.zh-CN.md)。

## Attribution and license

This fork is based on [r1ckyIn/canvas-ed-mcp](https://github.com/r1ckyIn/canvas-ed-mcp),
originally created by Ricky. The project remains licensed under the MIT License; see
[LICENSE](LICENSE). That license applies to the software, not to course content or
third-party platform data.
