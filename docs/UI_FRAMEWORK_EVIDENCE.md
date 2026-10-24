# Desktop UI Framework Evidence

## 1. Purpose and decision boundary

This document records the evidence used to design a desktop UI framework spike for
`canvas-ed-mcp`. It responds to the primary usability problem in the current first-run
experience: a long serial sequence of course prompts makes a large catalogue difficult
to scan, compare, and select.

The target product shape is a thin cross-platform Python desktop client over typed
application services. The existing CLI, sync, due-date, provider, configuration, secret,
and MCP semantics remain authoritative. A UI must not invoke CLI entry points, parse
console output, or duplicate domain rules.

PySide6 / Qt Widgets is **SELECTED FOR IMPLEMENTATION / RELEASE GATES OPEN** after the
owner-reviewed Windows usability work recorded below. wxPython and Tkinter/ttk are
frozen as historical spike evidence and receive no further feature work. This product
direction does not convert unrun accessibility, packaging, signing, notarization, or
macOS checks into passes, and it does not waive the recorded PySide6 responsiveness risk.

Evidence snapshot date: **2026-10-22 (Australia/Sydney)**.

### 1.1 Owner-reviewed Windows usability update — 2026-10-22

Tkinter/ttk is **rejected for this project** and receives no further implementation
work. On the owner-only Windows device, its tested UI remained visibly soft at the
active display scaling, and ordinary course selection followed native Treeview extended
selection semantics: clicking another row replaced the previous selection unless the
user held Ctrl. That interaction conflicts with the setup journey's central requirement
to select an entire term of Canvas and Ed courses without selection loss or modifier-key
knowledge.

This is a project-specific Windows usability result, not a claim about every Tkinter
application or platform. Existing Tkinter spike code and automated results remain as
an audit trail. Its dedicated widget suite is now skipped as `NOT_RUN`; at that point,
only PySide6 and wxPython continued to the comparison later resolved in section 1.2.

### 1.2 Owner framework decision — 2026-10-22

After iterative pointer-based review of the complete setup journey on the owner-only
Windows device, PySide6 / Qt Widgets is selected for continued implementation. The owner
accepted its course selection, search/filter behavior, mapping and reversal controls,
folder settings, and exact review flow as the clearest of the tested candidates.

wxPython is frozen as a rejected alternative with one known unresolved application-spike
defect: choosing `Keep as separate courses` removes the suggestion from the pending list
without exposing a visible reversal path. This violates the project's reversible-mapping
usability contract. The defect remains unfixed because wxPython was removed from active
implementation after PySide6 was selected. This is evidence about the repository's spike,
not a claim that wxPython cannot implement reversible decisions.

This selection is an implementation decision, not a release-readiness result. Physical
keyboard completion, Narrator/NVDA, VoiceOver, frozen artifacts, license-compliance
packets, Windows signing, macOS notarization, and packaged start-up/RSS remain `NOT_RUN`
or `DEGRADED` exactly as recorded below. The PySide6 source-widget initial-render maximum
of 735 ms also remains above the provisional 500 ms threshold and must be optimized or
explicitly re-baselined with equivalent packaged evidence before release.

Status vocabulary:

- `VERIFIED`: supported by the linked first-party documentation or official package
  metadata as of the snapshot date.
- `DEGRADED`: some relevant evidence exists, but an important platform or workflow
  property is incomplete or uncertain.
- `NOT_RUN`: repository-specific runtime, accessibility, packaging, or performance
  evidence has not yet been collected.

Official documentation describes toolkit capability, not the accessibility or release
quality of this application. Claims that depend on application composition remain
`NOT_RUN` until measured with the protocol below.

## 2. Repository constraints

The comparison is governed by the following existing constraints:

1. The core package declares Python 3.10 through 3.15 support and uses an MIT license.
2. Credentials are stored through the existing Python `keyring` boundary. A GUI toolkit
   supplies only the masked input control; it must not create a second credential store.
3. Discovery and sync are network- and file-I/O-heavy and must not block the GUI thread.
4. Course discovery may return hundreds of Canvas and Ed courses, including historical
   terms and similar names. The UI must preserve explicit user selection and must never
   infer Canvas/Ed mappings merely from names.
5. Windows and macOS need independent build and assistive-technology verification.
   Cross-account, browser/mobile, Linux, and cloud behavior are outside this evidence
   snapshot unless a future release explicitly adds them.
6. A packaged desktop app should pin one tested Python runtime per release artifact.
   The core library can continue testing 3.10-3.15 independently; the GUI bundle does
   not need to depend on an arbitrary end-user Python installation.

## 3. Candidate evidence matrix

### 3.1 Platform, runtime, and interaction capability

| Criterion | PySide6 / Qt Widgets | wxPython | Tkinter / ttk |
|---|---|---|---|
| Python 3.10-3.15 | `VERIFIED`. PySide6 6.12 declares `>=3.10,<3.16`; its `abi3` wheels cover Windows x64/ARM64 and macOS universal2. | `VERIFIED`. wxPython 4.3.2 declares `>=3.10` and publishes CPython 3.10-3.15 wheels for Windows and macOS. | `VERIFIED` at the source/runtime level. Tkinter ships with official CPython and is available on Windows and macOS, but the bundled Tcl/Tk version must be recorded for every release runtime. |
| Dense course catalogue | `VERIFIED` toolkit capability. `QTableView`/`QTreeView`, item check-state roles, and `QSortFilterProxyModel` directly support table/tree presentation, sorting, and filtering without copying the source model. | `VERIFIED` toolkit capability. `DataViewCtrl` supports tabular/tree presentation, toggle renderers, and multiple selection. Filtering needs application-owned model logic. Some data-view behavior differs on macOS. | `DEGRADED`. `ttk.Treeview` supports hierarchy, columns, and multiple selection, but its documented API has no native per-row checkbox or filter model. A checkbox-like selector requires application-owned images, bindings, and assistive semantics. |
| Keyboard interaction | `VERIFIED` toolkit capability for focus navigation, labels, buddies, and shortcuts. Selection-screen programmatic focus smoke is `DEGRADED`; the physical keyboard workflow is `NOT_RUN`. | `VERIFIED` toolkit capability for native controls and accelerators. Selection-screen programmatic focus smoke is `DEGRADED`; the physical keyboard workflow is `NOT_RUN`, and focused-versus-selected behavior differs on macOS data views. | `VERIFIED` basic focus traversal through `takefocus`. Selection-screen programmatic focus smoke is `DEGRADED`; physical checkbox/tree keyboard interaction is `NOT_RUN` and custom-heavy. |
| Screen-reader foundation | `VERIFIED` toolkit foundation. Qt documents MSAA, macOS Accessibility, and AT-SPI backends and accessible implementations for built-in widgets. The application is `NOT_RUN`. | `DEGRADED`. Native controls may expose platform semantics, but the documented application extension API, `wx.Accessible`, is Microsoft Windows-only. Data-view toggle semantics on macOS are `NOT_RUN`. | `DEGRADED`, high risk. Tk's explicit Windows/X11/macOS screen-reader layer targets Tk 9.1. Python 3.15 official binaries bundle Tk 9.0, so the normal Tkinter distribution does not provide that 9.1 layer. |
| Masked secret entry | `VERIFIED`. `QLineEdit.Password` masks display; ordinary copy/cut behavior is restricted outside normal echo mode. | `VERIFIED`. Password-style text controls and `GetPasswordFromUser` are documented. | `VERIFIED`. `Entry(show=...)` and `askstring(show=...)` mask input; Tk documents that copying a masked selection copies the displayed mask, not the underlying value. |
| Background work | `VERIFIED` toolkit primitives. Qt supplies `QThread`, `QThreadPool`, queued signals, and higher-level concurrent APIs. Application cancellation semantics are `NOT_RUN`. | `VERIFIED` integration primitive. Python workers can marshal GUI updates through `wx.CallAfter`, which is documented for calls from non-GUI threads. Application cancellation is `NOT_RUN`. | `VERIFIED` as a possible architecture, with restrictions. Work can run in a Python worker, but Tk calls must remain coordinated with the Tcl interpreter and long event handlers block the event loop. Application behavior is `NOT_RUN`. |
| Framework maintenance signal | `VERIFIED`. PySide6 6.12 was released in October 2026 by the Qt Project. | `VERIFIED`. wxPython 4.3.2 was released in October 2026 with current CPython wheels. | `VERIFIED` as maintained through CPython and Tcl/Tk. Integration timing is a risk: Tk 9.1 accessibility follows the Tk 9.0 runtime bundled by Python 3.15. |

### 3.2 Packaging, footprint, updates, and licensing

| Criterion | PySide6 / Qt Widgets | wxPython | Tkinter / ttk |
|---|---|---|---|
| Packaging path | `VERIFIED`. `pyside6-deploy` wraps Nuitka and emits Windows executables and macOS application bundles. PyInstaller and cx_Freeze remain alternatives. | `VERIFIED` through a third-party packager. PyInstaller explicitly supports wxPython and Windows/macOS; each target OS must build its own artifact. | `VERIFIED` through a third-party packager. PyInstaller can freeze the application, but inclusion of the selected Tcl/Tk runtime and resources must be verified in each artifact. |
| Dependency pressure before freezing | PySide6 Essentials 6.12 wheels are approximately 82.2 MB for Windows x64 and 116.0 MB for macOS universal2. These are wheel sizes, not final app sizes. | wxPython 4.3.2 wheels are approximately 17-20 MB per supported Windows/macOS architecture. These are wheel sizes, not final app sizes. | Tk is included in official Python distributions, so incremental toolkit pressure is lower. Final artifact size remains `NOT_RUN`. |
| Frozen artifact size, start time, and RSS | `NOT_RUN` | `NOT_RUN` | `NOT_RUN` |
| Application updates | `DEGRADED` for all candidates. The toolkit does not establish a release-update policy. Qt's deployment guidance explicitly notes that common freeze tools do not provide an update mechanism. Begin with replacement of the complete signed package; evaluate an updater separately only when requirements exist. | Same | Same |
| Windows signing | `NOT_RUN` for all candidates. Signing reduces publisher ambiguity, but Microsoft states that even a valid new certificate may still receive SmartScreen warnings until reputation develops. The Microsoft Store has separate reputation behavior. | Same | Same |
| macOS signing/notarization | `NOT_RUN` for all candidates. Direct distribution requires Developer ID signing, hardened runtime, a secure timestamp, notarization, and validation of nested executable code. | Same | Same |
| Toolkit license | LGPLv3/GPLv3 or commercial. An MIT application needs a documented LGPL compliance plan when using community binaries, including required notices and user replacement/relinking rights; selected modules and third-party components require review. | `LGPL-2.0-or-later WITH WxWindows-exception-3.1`. The exception permits binary object-code distribution under the application's own terms. | CPython uses the PSF License v2; applicable Tcl/Tk and incorporated-software notices must be retained in a binary distribution. |

Wheel size is a dependency signal only. It must not be presented as an installed-size or
update-size result. Those values remain `NOT_RUN` until equivalent applications are
frozen with the same application resources and runtime policy.

## 4. First-party evidence links

### 4.1 PySide6 and Qt

- [PySide6 package metadata and release files](https://pypi.org/project/PySide6/)
- [PySide6 Essentials package sizes](https://pypi.org/project/PySide6-Essentials/)
- [Qt for Python requirements](https://doc.qt.io/qtforpython-6/gettingstarted.html)
- [Qt-supported desktop platforms](https://doc.qt.io/qtforpython-6/overviews/qtdoc-supported-platforms.html)
- [Qt model/view programming](https://doc.qt.io/qtforpython-6/overviews/qtwidgets-model-view-programming.html)
- [`QSortFilterProxyModel`](https://doc.qt.io/qtforpython-6/PySide6/QtCore/QSortFilterProxyModel.html)
- [Qt accessibility guidance](https://doc.qt.io/qtforpython-6/overviews/qtdoc-accessible.html)
- [`QAccessible` platform backends](https://doc.qt.io/qt-6/qaccessible.html)
- [`QLineEdit` password echo modes](https://doc.qt.io/qtforpython-6/PySide6/QtWidgets/QLineEdit.html)
- [Qt multithreading technologies](https://doc.qt.io/qtforpython-6/overviews/qtdoc-threads-technologies.html)
- [Qt for Python deployment](https://doc.qt.io/qtforpython-6/deployment/index.html)
- [`pyside6-deploy`](https://doc.qt.io/qtforpython-6/deployment/deployment-pyside6-deploy.html)
- [Qt licensing](https://doc.qt.io/qt-6/licensing.html)

### 4.2 wxPython

- [wxPython package metadata and release files](https://pypi.org/project/wxPython/)
- [`DataViewCtrl`](https://docs.wxpython.org/wx.dataview.DataViewCtrl.html)
- [`DataViewVirtualListModel`](https://docs.wxpython.org/wx.dataview.DataViewVirtualListModel.html)
- [`wx.Accessible`](https://docs.wxpython.org/wx.Accessible.html)
- [`wx.CallAfter`](https://docs.wxpython.org/wx.functions.html#wx.CallAfter)
- [wxPython license](https://www.wxpython.org/pages/license/)

### 4.3 Tkinter, packaging, and platform release controls

- [Python Tkinter documentation](https://docs.python.org/3/library/tkinter.html)
- [Python ttk documentation](https://docs.python.org/3/library/tkinter.ttk.html)
- [Python masked simple dialogs](https://docs.python.org/3/library/dialog.html)
- [Tk Entry `-show` behavior](https://www.tcl-lang.com/man/tcl9.0/TkCmd/entry.html)
- [Tk TIP 733: accessibility/screen-reader support in Tk 9.1](https://core.tcl-lang.org/tips/doc/trunk/tip/733.md)
- [Python license and incorporated-software notices](https://docs.python.org/3/license.html)
- [PyInstaller supported platforms and Python versions](https://pyinstaller.org/en/latest/)
- [PyInstaller one-folder and one-file behavior](https://pyinstaller.org/en/stable/operating-mode.html)
- [Python `keyring` API](https://keyring.readthedocs.io/en/stable/)
- [Microsoft SmartScreen reputation guidance](https://learn.microsoft.com/windows/apps/package-and-deploy/smartscreen-reputation)
- [Apple notarization requirements](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution)

These URLs are live references. Before a framework decision or release, record the exact
package versions, wheel filenames, hashes, build host, runtime version, and documentation
version used by the spike; do not silently substitute whatever is latest at that time.

## 5. Common 300-course evidence spike

### 5.1 Spike boundary

Build one disposable prototype per candidate over the same framework-neutral fake
presenter. A spike must not call real Canvas, Ed, Gradescope, or MCP endpoints; read or
write real profiles; request a real token; or alter production CLI/domain behavior.

The presenter owns deterministic data and state transitions. Framework implementations
own presentation, focus, accessibility metadata, and event-loop integration only. This
separation prevents a candidate from appearing simpler because it embeds domain logic in
the view.

### 5.2 Deterministic fixture

Generate exactly 300 synthetic courses with:

- Canvas and Ed providers, including provider-only and intentionally similar entries;
- 12 terms, including current, future, and historical terms;
- long names, Unicode names, duplicate display names, missing optional codes, and stable
  immutable IDs;
- at least 20 plausible-but-unconfirmed Canvas/Ed pairs;
- one complete provider result, one partial result, and one provider-failure state.

The fixture contains no production identifiers, credentials, URLs, course content, or
Vault-derived information.

### 5.3 Required prototype journey

Each prototype implements the same seven screens/states:

1. **Provider connection:** provider cards, a labelled masked token field, keyring status,
   and a read-only discovery explanation.
2. **Discovery:** progress, provider-specific success/partial/failure states, retry, and
   cancellation at a defined safe boundary.
3. **Course selection:** search; provider and term filters; grouped rows; per-course
   checkbox; `Select visible`; `Clear visible`; and a persistent `Selected N courses`
   summary. Filtering must not clear hidden selections.
4. **Explicit mapping:** selected Canvas and Ed courses may be paired or left independent.
   Similar names may be shown as suggestions but must never auto-create mappings.
5. **Defaults and overrides:** shared content coverage and size limit, per-course initial
   download, and folder naming, with folder overrides progressively disclosed. A linked
   Canvas/Ed row applies its initial-download choice to both sources.
6. **Review:** providers, mappings, modes, and exact destination-path previews. Credential
   output is limited to a redacted `stored in system keyring` state.
7. **Fake sync:** 150 progress/result events, warnings, a partial course, cancellation,
   and a stale-review/fingerprint rejection state.

### 5.4 Repeated task script

Run the following task without framework-specific shortcuts:

1. Search for a course fragment shared by at least 20 rows.
2. Select five visible rows, change the term filter, and select three more.
3. Clear two visible selections and return to the unfiltered catalogue.
4. Verify that the total and every retained checkbox are correct.
5. Map two explicit pairs, reject one suggestion, and leave one pair independent.
6. Apply shared defaults, override two courses, and review destination paths.
7. Start fake sync, inspect a partial warning, cancel at a safe boundary, restart, and
   observe stale-plan rejection.

Repeat once with pointer input, once keyboard-only, and once with the target screen
reader active. Record failures rather than repairing the test script differently for a
candidate.

## 6. Hard gates

A candidate that fails any hard gate is not eligible for selection regardless of its
weighted score.

### 6.1 Workflow correctness

- All 300 courses remain discoverable through search/filter/group navigation.
- Hidden selections survive filtering and group collapse; selection totals remain exact.
- `Select visible` and `Clear visible` affect only the reviewed visible set.
- No mapping is created without an explicit user action.
- Review shows the exact resulting paths and effective per-course settings before commit.

### 6.2 Accessibility and keyboard operation

- The complete task script is operable without a pointer.
- Focus order is logical and visible; focus is never trapped in the course table or
  details panel.
- Search, filter, provider/term group, row checkbox, selected count, mapping action,
  warning, progress, and cancellation controls expose correct accessible names, roles,
  states, and changes.
- Windows passes with Narrator and NVDA; macOS passes with VoiceOver.
- A painted image that resembles a checkbox but does not expose a checked state fails.
- Information conveyed by color or visual placement also has a textual/semantic channel.

### 6.3 Responsiveness and background safety

Provisional local-device thresholds are:

- 300-row initial presentation under 500 ms;
- search/filter response under 100 ms at p95;
- no GUI-thread stall over 100 ms while discovery or sync work is active;
- visible cancellation acknowledgement under 250 ms; and
- no unsafe worker termination, partial config commit, or false-success state.

If the measurement harness cannot reliably observe these bounds, report `DEGRADED`; do
not convert absence of measurement into a pass.

### 6.4 Credential boundary

- The fake token is masked and is absent from logs, exception text, screenshots, review
  models, configuration serialization, command-line arguments, and exported artifacts.
- The value is handed directly to the existing keyring-facing application service.
- No local HTTP server, WebView bridge, or additional IPC hop is introduced for secrets.
- Keyring unavailability is explicit and fail-closed; the UI does not fall back to a
  plaintext file.

Masked widgets reduce display and clipboard exposure; they do not prove memory erasure.
The spike must not claim secure zeroization of immutable Python strings.

### 6.5 Architecture and regression boundary

- The UI consumes typed use cases/presenters rather than CLI entry points or stdout.
- It does not implement provider requests, sync policy, naming, locking, due-date logic,
  config migration, or MCP behavior.
- The existing CLI and MCP test suites remain unchanged and passing.
- Closing or cancelling the GUI cannot corrupt existing profiles or sync state.

### 6.6 Packaging and legal readiness

- Native Windows and macOS artifacts build reproducibly on their respective host OSes,
  launch offline, show no unintended console, and install/uninstall cleanly.
- The artifact contains only the required runtime/plugins and retains existing user-data
  locations across application updates.
- Nested executables and libraries that require signing are enumerated.
- Required license texts, notices, and relinking/replacement obligations are documented
  and accepted before adding the framework to production dependencies.

Unsigned local spike artifacts may be used for measurement. This does not satisfy the
release signing/notarization gate.

## 7. Measurement rubric

### 7.1 Required evidence record

Record the following in a single machine-readable result plus a human summary for every
framework/OS pair:

| Area | Required measurements |
|---|---|
| Reproducibility | OS/version, architecture, hardware summary, framework/wheel hashes, Python runtime, packager version, clean build command, and build duration |
| Interaction | Task completion, incorrect/lost selections, pointer clicks, keystrokes, focus-order defects, and user-visible error states |
| Accessibility | Screen reader/version, accessible names/roles/states, state-change announcements, keyboard traps, reading order, and any custom semantic adapters |
| Performance | Initial render p50/p95, filter p50/p95, longest GUI-thread stall, cancel acknowledgement, cold/warm start, peak RSS, and idle RSS |
| Packaging | Compressed artifact size, installed size, file count, runtime/plugin inventory, install/uninstall result, first-launch warnings, and signing/notarization observations |
| Maintainability | Prototype source lines, custom widgets/delegates, platform branches, framework adapters, test lines, warnings, and unresolved defects |
| Security | Secret canary scan of logs/config/artifacts, clipboard result, keyring failure behavior, crash-report result, and redaction result |

Unavailable evidence must be marked `NOT_RUN`; partial or platform-specific evidence must
be marked `DEGRADED`. Do not infer macOS behavior from Windows, VoiceOver behavior from
Narrator/NVDA, or packaged behavior from running source in a virtual environment.

### 7.2 Weighted comparison after hard gates

Only candidates that pass every hard gate receive a weighted score:

| Category | Weight | Scoring focus |
|---|---:|---|
| Accessibility | 30% | Correct semantics and full keyboard/screen-reader completion on both OSes |
| Workflow clarity | 25% | Low-error catalogue selection, mappings, defaults, review, progress, and recovery |
| Integration and maintenance | 20% | Thin presenter boundary, little custom control code, low platform divergence, testability |
| Packaging and release | 15% | Reproducible artifacts, signing/notarization path, install/uninstall reliability |
| Footprint and startup | 10% | Measured artifact/update size, cold/warm start, and memory use |

Use a 0-5 score per category with written evidence. A difference smaller than 0.25 in the
weighted total is not decision-grade; resolve it with targeted follow-up measurements.
Framework familiarity or visual polish alone is not a scoring category.

## 8. Decision procedure and current status

1. Freeze the presenter contract and synthetic fixture.
2. Preserve the completed three-framework spikes as historical comparison evidence;
   continue implementation only in the selected PySide6 candidate.
3. Preserve the owner-reviewed Windows framework decision without treating it as
   cross-platform, accessibility, packaging, or release approval.
4. Run the same protocol on a real macOS x64/ARM64 target as applicable; absence of that
   host is `NOT_RUN`, not a Windows-derived pass.
5. Apply hard gates before weighted scoring.
6. Review license and release obligations for the exact selected modules and packager.
7. Record the decision and rejected alternatives in the project decision log before any
   production UI rewrite begins.

Current repository-specific evidence:

- deterministic 300-course fixture and machine-readable evidence schema:
  `VERIFIED` by repository tests;
- framework-neutral seven-state presenter, 150-event fake sync, and the repeated task
  contract: `VERIFIED` in a headless reference bridge;
- headless timing samples: `DEGRADED`; they test measurement plumbing only and are not
  widget, GUI-thread, start-up, memory, or packaging measurements;
- PySide6 6.12.0, wxPython 4.3.2, and Tkinter/Tk 8.6 source widgets on the
  owner-only Windows host: `VERIFIED` for the programmatic common contract only;
- provider filtering plus `Select visible` / `Clear visible` on the 300-course
  catalogue: `VERIFIED` through each toolkit's real widget events, including hidden
  selection retention;
- seven workflow states expose the same bounded visible/enabled semantic controls:
  `VERIFIED` from real stacked pages or state-specific containers; wrong-stage widget
  activation fails closed in every adapter;
- stale review recovery: `VERIFIED` through a state-specific `Rebuild review` control;
  stale screens hide `Start sync`, rebuild from the current selection, then restore the
  fresh review action;
- common selection-screen focus order: `DEGRADED` at 1/7 keyboard-journey steps;
  each real widget accepted programmatic focus in order, but Tab/Shift+Tab, keyboard
  activation, shortcuts, focus visibility, and assistive technology are `NOT_RUN`;
- real 300-course source-widget timings: `VERIFIED` for the three bounded measurements
  in the table below; GUI-thread stall, process start-up, RSS, and packaged timings are
  `NOT_RUN`;
- Narrator/NVDA task completion: `NOT_RUN`
- VoiceOver task completion: `NOT_RUN`
- frozen Windows artifacts: `NOT_RUN`
- frozen macOS artifacts: `NOT_RUN`
- signing/notarization dry run: `NOT_RUN`
- framework decision: **PySide6 SELECTED FOR IMPLEMENTATION / RELEASE GATES OPEN**

### 8.1 Windows source-widget snapshot

The 2026-10-21 run used Python 3.10.19 on Windows build 10.0.26200, AMD64, the fixed
fixture hash `fc3badd2cf1f3dbc1dfbda37ad160d3905e6a141c3d76de43e0f313c957a4942`,
and a working tree based on Git revision `b348e52`. Each sample ran in a fresh child
process, so Qt and wx global application objects were not reused and Tk did not perform
an extra availability-window probe inside the timer. Toolkit import and interpreter
start-up occur before the measured boundary, so these remain widget-construction and
event-loop samples, not cold-start or packaged-application measurements.

| Candidate | Initial render p50 / p95 / max | Filter p95 | Cancel max | Bounded result |
|---|---:|---:|---:|---|
| PySide6 6.12.0 / Qt 6.12.0 | 656 / 729 / 735 ms | 15 ms | 16 ms | `FAIL`: initial maximum exceeds 500 ms |
| wxPython 4.3.2 / wxWidgets 3.3.4 | 234 / 235 / 235 ms | 31 ms | 32 ms | `PASS` for these three measurements only |
| Tkinter / Tk 8.6 | 390 / 403 / 406 ms | 16 ms | 16 ms | `PASS` for these three measurements only |

The host clock samples are visibly quantized at roughly 15-16 ms; a recorded `0 ms`
means below that observed resolution, not instantaneous completion.

The Windows source environment occupied approximately 681 MB for the installed full
PySide6 plus Shiboken package directories and 105 MB for wxPython plus its installed
NumPy dependency directories. These are local environment footprints, not production
dependency requirements, frozen artifact sizes, installer sizes, or update sizes. Tk
is bundled with this Python runtime. None of these figures alone justified the framework
decision; the owner selected PySide6 from the tested workflow as a whole. They remain
insufficient to claim release readiness.

### 8.2 Formal preview follow-up

The selected implementation's zero-write `SetupWindow` was remeasured on the same owner
Windows device on 2026-10-22 with the deterministic 300-course fixture. One source-run
sample recorded 1.19 s to construct the complete Select window, 12.8 ms to apply a Term
filter, 13.6 ms to restore all 300 rows, and 8.8 ms to apply the sample Search filter.
The deliberately worst-case 300-selected-course Folders window constructed in 1.54 s.
The framework-neutral 300-row folder-presentation calculation, previously observed at
about 1.81 s before optimization, measured 6.7 ms after recomputation was limited to
rows with real folder overrides.

These single source-run samples are useful regression evidence, not p50/p95 data and not
packaged cold-start evidence. Search/Term interaction is comfortably below the 100 ms
provisional response threshold in this sample, but the sub-500 ms initial-presentation
gate still fails and remains open. Automated tests now verify label buddies, accessible
names for the principal collections, explicit page-local Tab order, and preservation of
the table's focused/current row when a course is checked. Physical Tab/Shift+Tab task
completion, visible-focus inspection, Narrator/NVDA, and keyboard activation remain
`NOT_RUN` and require owner review.

The shared spike foundation is internal-only and intentionally excluded from the public
source export. Owner-reviewed Windows layout and workflow evidence was sufficient to
select PySide6 for implementation, but automated source evidence has otherwise reached
its honest boundary. The next release-gate work is physical Tab/Shift+Tab and keyboard
activation, visible focus, Narrator/NVDA, macOS/VoiceOver, equivalent packaging
measurements, and the PySide6 license-compliance plan. Framework selection is not
permission to rewrite existing CLI, sync, due, provider, configuration, or MCP logic;
the GUI remains an adapter over their typed application boundaries.
