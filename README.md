# Canvas Ed MCP

[English](README.md) | [简体中文](README.zh-CN.md)

[![Python 3.10–3.14](https://img.shields.io/badge/Python-3.10%E2%80%933.14-3776AB)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Canvas Ed MCP is a local desktop tool for students who use Canvas, Ed, or both. It
downloads course material into folders you choose, creates weekly and semester due
date reports, and provides read-only MCP access to configured courses.

`0.2.0rc16` is the current source candidate. The latest published GitHub prerelease
remains `0.2.0rc12` until the rc16 release gate and publication are complete. Reviewed
artifacts are available from the
[Releases page](https://github.com/LHYubiquitous/canvas-ed-mcp-public/releases).
The package is not published to PyPI.

## What it does

The installed package provides:

- a PySide6 first-run and graphical course-reconfiguration UI, plus native Windows,
  macOS, and Linux launchers;
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

## 1. Install

Download `canvas_ed_mcp-0.2.0rc16-py3-none-any.whl` from the rc16 release when it is
published, and place it in
a new folder. Python 3.10–3.14 is supported. Keep this folder: generated launchers
point to the virtual environment inside it.

### Windows PowerShell

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install .\canvas_ed_mcp-0.2.0rc16-py3-none-any.whl
.\.venv\Scripts\canvas-ed-mcp.exe --version
```

If `py` is unavailable, replace it with the full path to an installed Python.
Activate the environment with:

```powershell
.\.venv\Scripts\Activate.ps1
```

### macOS or Linux

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install ./canvas_ed_mcp-0.2.0rc16-py3-none-any.whl
source ./.venv/bin/activate
canvas-ed-mcp --version
```

For installation from a reviewed source checkout, replace the wheel path with `.`.
More update, rollback, and Linux keyring details are in
[Cross-platform installation](docs/INSTALLATION.md).
The remaining examples assume the virtual environment is active. You can instead use
the full `.venv` executable path shown above.

## 2. Store credentials

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
2. Open the Australian-region [Ed API Tokens](https://edstem.org/au/settings/api-tokens)
   page.
3. Create a dedicated token for `canvas-ed-mcp` and copy it once.

The Ed integration uses an unofficial, schema-sensitive API. Do not substitute a
browser cookie, SSO password, Canvas token, or copied browser request.

### Save tokens in the operating-system keyring

Check the operating-system keyring before setup:

```text
canvas-ed-mcp doctor --show-paths
```

Store only the providers you use. Each command prompts for the value without placing
it in command history or the configuration file:

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

## 3. Create your profile

Install the optional PySide6 first-run UI from a source checkout with
`python -m pip install ".[gui]"`, or install a wheel with its `gui` extra. Then start
the real first-run host with:

```text
canvas-ed-setup
```

From a source checkout it can also be run as
`python -m canvas_ed_mcp.ui.first_run_window`.

It uses the actual profile path and read-only Canvas/Ed course discovery, then hands the
real catalogues to the reviewed Select → Map → Folders → Review flow. Credential fields
accept references such as `keyring:canvas-ed-mcp/default/canvas`; they never accept or
display the token itself. The final confirmation creates only the new configuration—no
course content, output folders, state, or launchers. After that save succeeds, the
completion screen can explicitly install the daily-use shortcuts and open either the
numbered daily menu or their folder. The first real sync remains a separate confirmed
action. This installable entry point is not yet a signed or frozen desktop release.

Preview a new Canvas, Ed, or combined profile:

```text
canvas-ed-mcp setup --profile default --dry-run
```

The wizard shows every discovered course and asks which ones to include. Choose
`baseline_from_now` to record the current state and download only later changes, or
`import_all` to download the selected existing material on the first real sync.
When the preview is correct, create the profile:

```text
canvas-ed-mcp setup --profile default
```

The non-dry-run command asks for a final confirmation before it writes the profile.
It also installs the daily-use launchers. Validate the result with:

```text
canvas-ed-mcp config validate PATH_PRINTED_BY_SETUP
canvas-ed-mcp doctor --profile default --show-paths
```

To add, remove, remap, or change course settings later, open the graphical editor:

```text
canvas-ed-configure --profile default
```

The same editor opens from `configure_courses` and menu option 4. It starts with the
saved courses selected and shows the exact config diff before saving. Existing folder
names are read-only in this release. A mapping can be changed only when that source
has no sync history and its current course folder is absent or empty; otherwise the
UI explains why it is locked. Saving never moves or deletes files and never starts a
sync.

The terminal workflow remains available as a fallback:

```text
canvas-ed-mcp configure --profile default --dry-run
canvas-ed-mcp configure --profile default
```

Removing a source from configuration keeps its downloaded files and historical state.

## 4. Daily use

After setup or a configuration change, native launchers are installed in
`<output root>/Canvas Ed MCP Launchers`. Double-click `canvas_ed_app` for the numbered
menu, or use `course_material_sync`, `this_week_due`, and `this_semester_due` directly.
Existing edited launchers are never overwritten; a changed managed replacement is
written with a `.new` suffix for review.

When the optional `gui` extra is installed, the first daily desktop dashboard can be
opened with:

```text
canvas-ed-app --profile default
```

It shows the active profile, connected sources, local destination, and an asynchronous
material-sync preview. Applying always requires confirmation and uses the exact reviewed
plan; partial-provider warnings remain visible. Closing during work requests cooperative
cancellation before the provider session is released. This is an additive preview: the
generated `canvas_ed_app` launcher continues to open the tested terminal menu until the
desktop packaging and owner-device release gates are complete.

| Launcher | Result |
|---|---|
| `canvas_ed_app` | Opens the numbered menu |
| `course_material_sync` | Updates every configured course |
| `this_week_due` | Creates `Due Dates/this-week-due.txt` |
| `this_semester_due` | Creates a teaching-week Markdown checklist |
| `configure_courses` | Opens graphical course reconfiguration |
| `health_check` | Shows package, profile, path, and keyring status |
| `open_course_folder` | Opens the configured output folder |

Due reports are create-only: changed content receives a numbered revision instead of
overwriting the previous report. Semester reports preserve checked boxes and notes
attached to stable item markers.

## 5. Command-line and MCP use

The same functions are available as short CLI commands:

```text
canvas-ed-mcp sync --profile default --dry-run
canvas-ed-mcp sync --profile default
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
