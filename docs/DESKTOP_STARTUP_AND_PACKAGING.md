# CourseHarbor desktop startup and packaging decision

Status: rc18 Windows onedir and macOS arm64 `.app`/DMG candidates `PASS`; both visible
owner journeys `PASS`. Developer ID signing, notarization, Intel/universal packaging,
and upgrade/uninstall remain `NOT_RUN`; Gatekeeper remains `DEGRADED`.

## 1. Startup lifecycle

The supported desktop entry point is `canvas_ed_mcp.ui.app_shell:main`. Startup first
builds a typed, local-only `StartupPlan`:

1. no configured profile → First run for `default`;
2. exactly one readable profile → CourseHarbor App Shell;
3. multiple profiles, or any local profile needing attention → Profile chooser;
4. explicit `--profile`/`--config` → preserve the requested profile behavior.

Profile discovery reads only local config/state metadata. It does not resolve a token,
open the native keyring, construct a provider, or make a network request. The chooser
contains only non-secret `ProfileOverview` values. A damaged profile stays visible but
cannot be opened or overwritten. `Switch profile` first asks active pages to reach their
existing cooperative-cancellation boundary.

Closing embedded course selection returns to a refreshed chooser. `Remove profile…`
uses a revision-bound application review and targeted confirmation, then moves only the
profile config into the private `.removed` archive. It intentionally retains course
files, native credentials, sync state, logs/cache, and remote tokens; removal is not
remote revocation or complete data erasure.

`courseharbor` is also declared under `[project.gui-scripts]`. On an ordinary Windows
Python installation this provides a GUI launcher without a console window, but it is not
a self-contained distribution and does not replace the packaging work below.

## 2. Packaging comparison

| Criterion | PyInstaller | Briefcase | Nuitka |
|---|---|---|---|
| Windows GUI | `--windowed`/`--noconsole`; onedir or onefile | Native Windows application packaging and MSI-oriented workflow | Standalone/app/onefile plus `--windows-console-mode=disable`; installer support |
| macOS | `--windowed` creates `.app`; onedir is preferred over onefile for signing/notarization | `.app`, DMG/PKG/ZIP and integrated signing/notarization workflow | App bundle and macOS installer modes |
| PySide6 | Mature hook/spec-file model; Qt contents remain explicitly auditable | Application template plus packaged Python dependencies; requires a larger project-layout adoption | Dedicated `pyside6` plugin and Qt-plugin controls |
| Build prerequisites | Smallest change to the current Python project; builds are host-platform specific | More native packaging/template prerequisites, including platform toolchains | Compiler/toolchain requirement and longer compilation path |
| Auditability | Explicit spec + onedir tree fits the existing fail-closed manifest review | Strong native installer structure, but more generated template content to audit | Standalone tree is inspectable; compiled/onefile internals add review complexity |
| Current evidence | Windows owner-device onedir spike `PASS` | `NOT_RUN` | `NOT_RUN` |

Primary references:

- [PyInstaller usage and windowed app bundles](https://pyinstaller.org/en/stable/usage.html)
- [Briefcase stable documentation](https://briefcase.readthedocs.io/en/stable/)
- [Nuitka user manual](https://nuitka.net/user-documentation/user-manual.html)

## 3. Staged decision

Use **PyInstaller onedir/windowed as the first packaging spike**. It is the narrowest
addition to the current PySide6 project, keeps collected Qt/keyring files visible for
the existing allowlist audit, and avoids onefile extraction behavior while startup,
native keyring, and upgrade retention are still being characterized.

The spike is not approval to publish. It must produce separate Windows and macOS builds
on their native runners and verify:

- no console window during normal Windows launch;
- PySide6 platform plugins and TLS/network dependencies load from the bundle;
- Windows Credential Manager and macOS Keychain keep the existing deterministic service
  names and survive an application upgrade;
- config, state, logs, and course libraries remain outside the application bundle;
- moving an unpacked build does not embed the build machine's interpreter path;
- exact artifact inventory, size, cold start, signing/notarization state, uninstall, and
  upgrade retention are recorded without credentials or private course data.

PyInstaller 6.22.3 is an exact development dependency for the private rc17 Windows
prerelease; it is not a runtime dependency of source installs. Briefcase is the next
candidate if a native MSI/DMG lifecycle becomes more important than minimal integration.
Nuitka remains a measured alternative if startup or bundle characteristics justify the
compiler/toolchain cost.

## 4. Evidence state

- Source/install GUI entry point: automated tests `PASS`.
- Local startup planning and Profile chooser: automated tests `PASS`.
- Existing-profile Windows App Shell workflow: owner-device `PASS` on 2026-10-25.
- Frozen Windows onedir executables: automated owner-device `PASS` on 2026-10-26.
  The AMD64 Windows 11 candidate used Python 3.12.10, PySide6 6.12.0, and PyInstaller
  6.22.3. It contains 878 files / 156,586,733 bytes, one `qwindows.dll`, a GUI-subsystem
  `CourseHarbor.exe`, and a console-subsystem `courseharbor-cli.exe`. The audited hashes
  are recorded in the adjacent generated manifest rather than copied into source docs.
- Frozen dependency/keyring smoke: `PASS`. QtCore and its dependent DLLs loaded from the
  bundle; the console smoke reported `keyring.backends.Windows.WinVaultKeyring` as
  `ready` without reading or writing a credential. The final icon-aware GUI smoke exited
  0 in 1.019 s and CLI smoke exited 0 in 3.778 s on the build location.
- Relocation smoke: `PASS`. A copied final icon-aware bundle outside the repository
  exited 0 for GUI, then the temporary copy was removed. The
  candidate audit rejected embedded build/home paths, complete PEM private keys, unsafe
  filenames, symlinks, wrong PE subsystems, and missing/duplicate Qt platform plugins.
- Frozen Windows full visible App Shell workflow: owner-device `PASS` on 2026-10-26.
  A frozen credential replacement/round-trip was not repeated; backend readiness passed
  and the existing source-app credential workflow retains its earlier owner evidence.
- Application icon: the original CourseHarbor mark is embedded in the Windows PE
  resource, applied to source/frozen Qt applications, and retained as an exact macOS
  iconset for native `iconutil` conversion during DMG construction.
- Windows installer, signing, SmartScreen, upgrade retention, and uninstall: `NOT_RUN`.
- Frozen macOS `.app`/DMG automated pipeline: `PASS` on private CI run `40293746038`
  at release-prep commit `bdcb4f7`. The source app and read-only mounted DMG both passed GUI/CLI
  smoke, arm64/native-link audit, confined-symlink audit, and whole-bundle ad-hoc
  signature verification. The published DMG is 119,297,536 bytes with SHA-256
  `e5145075802c01159250cd5524db94fb20e0e19b121f0dd3602f66d2b8473893`.
- macOS owner-device visible Finder launch, drag-install, and real Keychain
  save/test/replace: `PASS` on 2026-10-26. App Shell navigation, profile/setup flows,
  provider-aware Map skipping, and the Connections layout also passed visible review.
- macOS Developer ID signing and notarization: `NOT_RUN`; Gatekeeper: `DEGRADED`.
