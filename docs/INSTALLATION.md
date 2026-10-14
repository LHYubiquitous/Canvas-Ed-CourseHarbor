# Cross-platform installation and local operation

> Scope: the portable read-only MCP package and its configuration primitives. The
> portable `course-sync` command is still a safe reserved entry point; the legacy
> repository launcher remains the only production downloader. Never paste a token
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

## 5. Safe smoke checks

These local commands perform no provider request and do not need credentials:

```text
canvas-ed-mcp --version
canvas-ed-mcp --help
canvas-ed-mcp doctor --show-paths
course-sync --help
```

`course-sync --help` only verifies entry-point installation; it does not prove that
the portable downloader is connected. On 2026-10-14, a clean built-wheel install and
all four checks passed on Windows. Private GitHub Actions run `38422038193` passed on
Windows, macOS, and Linux across Python 3.10–3.15, including clean installation,
source tests, installed entry points, and an installed MCP protocol smoke. This does
not verify native keyring prompts or an owner-device MCP-host launch on
macOS/Linux; those device checks remain **NOT_RUN**.
Real provider access was separately verified on Windows for Canvas-only, Ed-only and
both read-only course catalogs without retaining payloads.

## 6. Updating, rollback, and uninstall

Before updating, keep the previous wheel and back up only the non-secret config/state
files shown by `doctor --show-paths`. Install a reviewed replacement with `python -m
pip install --upgrade PATH_TO_WHEEL`; re-run the safe smoke checks before any provider
access. Roll back with `python -m pip install --force-reinstall PREVIOUS_WHEEL`.

`python -m pip uninstall canvas-ed-mcp` removes the installed package, not course
output, platform config/state, or OS-keyring entries. Review those locations and
remote token revocation separately; never delete them as part of an automated
uninstall.
