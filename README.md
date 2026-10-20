# Canvas Ed MCP

[![Python 3.10–3.15](https://img.shields.io/badge/Python-3.10%E2%80%933.15-3776AB)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Canvas Ed MCP is a local desktop tool for students who use Canvas, Ed, or both. It
downloads course material into folders you choose, creates weekly and semester due
date reports, and provides read-only MCP access to configured courses.

`0.2.0rc11` is the current GitHub prerelease. Download the reviewed wheel from the
[Releases page](https://github.com/LHYubiquitous/canvas-ed-mcp-public/releases).
The package is not published to PyPI.

## What it does

The installed package provides:

- an English numbered terminal app and native Windows, macOS, and Linux launchers;
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

Download `canvas_ed_mcp-0.2.0rc11-py3-none-any.whl` from the release and place it in
a new folder. Python 3.10–3.15 is supported. Keep this folder: generated launchers
point to the virtual environment inside it.

### Windows PowerShell

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install .\canvas_ed_mcp-0.2.0rc11-py3-none-any.whl
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
./.venv/bin/python -m pip install ./canvas_ed_mcp-0.2.0rc11-py3-none-any.whl
source ./.venv/bin/activate
canvas-ed-mcp --version
```

For installation from a reviewed source checkout, replace the wheel path with `.`.
More update, rollback, and Linux keyring details are in
[Cross-platform installation](docs/INSTALLATION.md).
The remaining examples assume the virtual environment is active. You can instead use
the full `.venv` executable path shown above.

## 2. Store credentials

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

See [Authentication](docs/AUTHENTICATION.md) for Canvas personal access tokens, Ed
session tokens, revocation, and headless environment references.

## 3. Create your profile

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

To add, remove, or change courses later, preview and then commit the configuration:

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

| Launcher | Result |
|---|---|
| `canvas_ed_app` | Opens the numbered menu |
| `course_material_sync` | Updates every configured course |
| `this_week_due` | Creates `Due Dates/this-week-due.txt` |
| `this_semester_due` | Creates a teaching-week Markdown checklist |
| `configure_courses` | Reviews course and policy changes |
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
canvas-ed-mcp configure --profile default --dry-run
canvas-ed-mcp launchers install --profile default
canvas-ed-mcp open-course-folder --profile default
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

Report software vulnerabilities through [SECURITY.md](SECURITY.md).

## Verification and release materials

The project runs synthetic tests across Windows, macOS, and Linux for Python
3.10–3.15. The release candidate also includes:

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

## 中文速览

安装 wheel 后，先把 Canvas/Ed token 写入系统钥匙串，再运行
`canvas-ed-mcp setup --profile default`。配置成功后，直接双击输出目录中的
`canvas_ed_app`，即可使用课程同步、本周截止日期和全学期清单。同步只写入你选择的
本地目录；MCP 服务不包含远端写操作。

## Attribution and license

This fork is based on [r1ckyIn/canvas-ed-mcp](https://github.com/r1ckyIn/canvas-ed-mcp),
originally created by Ricky. The project remains licensed under the MIT License; see
[LICENSE](LICENSE). That license applies to the software, not to course content or
third-party platform data.
