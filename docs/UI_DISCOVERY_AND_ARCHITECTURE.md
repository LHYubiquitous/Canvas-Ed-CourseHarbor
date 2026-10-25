# UI Usability Discovery and Architecture

## 1. Status and scope

This document records the UI discovery baseline for the phase after `0.2.0rc12`.
It addresses the first-run usability problem reported by the owner: presenting a long
serial list of courses creates selection fatigue and makes the catalogue difficult to
understand.

The approved direction for the first implementation batch is a framework-neutral
application layer and setup-session model. After owner-reviewed Windows usability
testing on 2026-10-22, PySide6 / Qt Widgets is **selected for implementation**, while
its release gates remain open. The existing CLI, sync, due-report, provider,
configuration, secret, launcher, and MCP behavior remains authoritative. The wxPython
and Tkinter spikes are frozen as historical comparison evidence and receive no further
feature work.

## 2. Current journeys and primary friction

### 2.1 First run today

The terminal setup flow asks for provider connection details, discovers courses, and
then repeats confirmation, folder, content, size-limit, mapping, and initial-sync prompts
one course at a time. A dry run repeats much of the same interaction.

This preserves explicit consent but has poor information scent for a large catalogue:

- historical and current courses occupy the same serial prompt stream;
- the user cannot search, filter, compare, or keep a visible selection summary;
- shared defaults are requested repeatedly instead of being applied once with exceptions;
- Canvas/Ed mapping decisions are interleaved with selection details;
- destination paths and effective settings are difficult to review as one coherent plan;
- cancellation is safe, but progress and retained state are not presented as a workflow.

### 2.2 Daily use today

The numbered terminal menu exposes sync, due reports, configuration, folder opening,
launcher repair, and diagnostics. It is a useful fallback, but it cannot present profile
health, provider freshness, per-course warnings, and preview/apply state together.

### 2.3 Design response

Explicit confirmation remains mandatory, but it moves from hundreds of isolated prompts
to a reviewable sequence:

`Connect -> Discover -> Select -> Map -> Defaults -> Review -> Commit`

The course catalogue is a persistent table/tree state with search, provider and term
filters, grouped rows, per-row selection, `Select visible`, `Clear visible`, and an exact
`Selected N courses` summary. Filtering never clears hidden selections. Shared defaults
are applied once; per-course overrides are progressively disclosed.

## 3. Canvas content semantics the UI must explain

The current Canvas adapter does not interpret the labels `Pages` and `Files` as a full
recursive crawl of arbitrary course HTML.

- **Pages** means HTML pages linked from Canvas Modules. The adapter does not enumerate
  every item in the standalone Canvas Pages area.
- **Files** includes files linked from Modules and, when permitted, files returned by the
  course-wide Canvas Files API. If that API returns `403`, module-linked files can still
  be retained and the result is explicitly partial.
- Images referenced by a synchronized page may be inlined subject to the existing size
  and safety policy. Ordinary links inside arbitrary HTML are not recursively downloaded
  as additional standalone files.
- Module-linked content keeps module grouping locally. The current destination shape is
  `<course>/Pages/<Module>/...` and `<course>/Files/<Module>/...`; course-wide files use
  `<course>/Files/Course Files/...`. If the same remote item appears in multiple modules,
  the first discovered module owns its grouped destination.

The selection UI should therefore use explanatory labels such as `Module pages` and
`Module + course files`, with a coverage note, rather than implying a website mirror.

## 4. Product-form comparison

| Form | Strengths | Risks and release cost | Fit |
|---|---|---|---|
| Thin cross-platform Python desktop client | Direct access to the existing Python services and keyring boundary; native window, keyboard, file-dialog, and progress patterns; no local web server or browser-secret bridge | Toolkit dependency, per-OS packaging, accessibility verification, signing, notarization, and update footprint must be measured | **Recommended form for evidence spikes**, subject to the hard gates in `UI_FRAMEWORK_EVIDENCE.md` |
| Packaged local web UI | Flexible layout and strong table/filter ecosystem; familiar web testing techniques | Requires a localhost or embedded-WebView boundary, extra IPC/authentication and lifecycle design, larger attack surface for credentials, browser accessibility variance, and another packaging/runtime layer | Keep as a fallback only if native prototypes fail a hard usability gate |
| Enhanced terminal/TUI | Small distribution delta, SSH-friendly, and close to the current CLI | Dense course selection and mapping remain visually constrained; screen-reader and terminal behavior vary; file dialogs, secret prompts, and discoverability remain weak for the reported problem | Preserve the CLI/menu as recovery and automation surfaces; do not make TUI the primary usability answer |

The recommendation first selected a product shape for prototyping. After owner-reviewed
Windows testing on 2026-10-22, PySide6 / Qt Widgets was selected as the implementation
framework. Its information hierarchy, course-selection workflow, reversible mapping
decisions, folder settings, and review flow were the best fit after iterative manual
testing. Tkinter was removed from consideration because display scaling remained unclear
and native modifier-key multiple selection conflicted with the semester-wide selection
journey. wxPython was not selected after the evidence spike retained an unresolved
mapping-recovery defect: `Keep as separate courses` removed a suggestion without a
visible way to reverse that decision. These are project-specific prototype findings,
not general claims that Tkinter or wxPython cannot implement the required behavior.

Selecting PySide6 authorizes continued implementation; it does not assert release
readiness. Accessibility, responsiveness, packaging, license-compliance, signing, and
macOS evidence retain their recorded `DEGRADED` or `NOT_RUN` status until independently
verified.

## 5. Proposed screen and state model

### 5.1 First-run setup

1. **Profile:** profile name, resolved config location, output root, and an explicit
   create-new versus existing-profile status.
2. **Connect:** provider cards, endpoint/region, credential storage status, and masked
   secret entry through the existing secret boundary.
3. **Discover:** provider-specific progress and independent success, partial, failure,
   retry, and safe-boundary cancellation states.
4. **Select:** searchable and filterable catalogue with stable identities, bulk actions
   scoped to visible rows, and a persistent selection count.
5. **Map:** Canvas and Ed sources remain independent by default; linking is explicit and
   reversible. Similar names may be suggestions only.
6. **Defaults:** shared content and file-size settings, per-course initial download, and
   collision-safe folder defaults followed by optional folder exceptions.
7. **Review:** exact providers, source mappings, effective settings, initial modes, and
   destination paths. Credentials are represented only by storage status.
8. **Commit:** create-only configuration commit bound to the reviewed plan and target
   revision. A stale review returns to Review rather than applying.

### 5.2 Daily dashboard and sync slice

1. Show the active profile, output root, configured providers, and last-known freshness.
2. Build a material-sync preview through a typed service, preserving partial coverage and
   actionable warnings.
3. Require explicit review before Apply; bind Apply to the exact preview fingerprint and
   state/config revisions.
4. Stream structured progress off the UI thread and cancel only at declared safe points.
5. Show per-course results and warnings, then offer `Open output folder` without treating
   the GUI as required for future CLI or MCP operation.

Due reports, configuration changes, launcher repair, and diagnostics follow the same
reviewed-operation pattern after the first sync slice is stable.

### 5.3 Setup usability contract for the framework evidence spikes

The three source-widget spikes share one task model. Toolkit conventions may change
spacing and native rendering, but not the order, vocabulary, or state rules below.

1. **Choose courses** selects the complete Canvas and Ed scope for this setup run.
   Search, Provider, and Term only change which catalogue rows are visible; they never
   discard hidden selections. One bulk toggle selects every visible row when any are
   missing and clears every visible row when all are selected. The same action strip
   shows the global `Selected N courses` count and the primary mapping action. The
   mapping action is disabled until at least one course is selected. A single selected
   course can continue without manufacturing a Canvas/Ed pairing decision.
2. **Resolve matches** shows only suggestions whose Canvas and Ed sources are both
   selected. `Treat as the same course` and `Keep as separate courses` sit beside the
   current suggestion and are visually distinct from navigation. Each suggestion gets a
   safe, unique course key by default; the same labelled field remains editable in every
   candidate UI before linking. Both `Treat as the same course` and
   `Keep as separate courses` decisions remain visible and reversible through nearby
   Undo actions; undoing either restores the pair to pending suggestions. Continue is
   disabled while a selected automatic or manual suggestion is pending, so silence is
   never interpreted as consent to link or separate it.
3. **Folder settings** applies safe shared content and file-size defaults, while initial
   download is chosen explicitly per logical course. A linked Canvas/Ed course applies
   that row's choice to both sources. Folder names remain optional per-course exceptions.
   Canvas labels explicitly distinguish HTML
   module pages from module-linked plus course-wide files. The automatically generated
   folder is shown beside the override field and repeated as its placeholder; an empty
   field keeps that value. Folder edits have no per-row Apply/Clear controls: `Build exact
   preview` validates and applies the complete table atomically, or keeps every entered
   value in place when any row is invalid.
4. **Review and confirm** is the first place an exact executable plan is built. It shows
   every logical course, source provider, effective folder, and effective settings before
   the fake or production operation can start.

Selection bulk actions, match decisions, Undo, Back, and Continue are separate control
groups because they operate on different scopes. The primary Continue action stays at
the lower-right edge of each step; Back stays lower-left. A state-dependent action is
disabled before it can produce a domain exception, and its empty state explains what the
user must select next.

## 6. Thin application-service boundary

Presentation adapters must consume typed requests, results, issues, progress events,
cancellation tokens, and review proofs. They must not call CLI handlers, parse stdout,
or implement provider/domain rules.

| Use case | Read/plan boundary | Commit or effect boundary |
|---|---|---|
| Profile status | resolve and validate profile/config without secret resolution | none |
| Course discovery | lazily resolve only requested providers; return isolated provider results | none; remote operations remain read-only |
| Setup/configure | build an immutable plan with exact paths and stable issues | create/replace only after review proof and revision checks |
| Material sync | preview typed per-course actions, coverage, warnings, and state revision | apply exact preview under existing locks and no-clobber policy |
| Due reports | collect typed items, source freshness, and warnings | publish through the existing report service after explicit target review |
| Folder opening | validate a known configured path | delegate to a small platform adapter |
| Launcher repair | preview files and interpreter targets | write only the reviewed launcher plan |

The first batch implements shared operation contracts and fingerprints, lazy catalogue
discovery, reviewed setup commit, and an in-memory setup session. The selected-framework
work now also includes the first daily-sync service slice and a typed output-folder
effect boundary. Due reports and launcher repair remain subsequent application-service
slices; their existing logic is not being rewritten.

The second batch adds an internal framework-neutral evidence harness under
`spikes/ui_framework/`: a deterministic 300-course fixture, the seven-state presenter,
a 150-event fake sync, a common repeated-task runner, and a machine-readable result
schema. A headless reference bridge verifies the presenter/runner contract but does not
count as real GUI, accessibility, performance, or packaging evidence. The harness and
its tests remain deliberately excluded from the public source export. At that evidence
stage, both the production dependency and framework decision were still open; PySide6
has since been selected for implementation, while packaging and release gates remain
open.

The first selected-implementation batch adds a framework-neutral
`SetupController` and a capability-injected PySide6 `SetupWindow`. The formal window follows
`Select → Map → Folders → Review`, keeps both link and separate decisions
reversible, exposes selected courses that lack an automatic suggestion for manual
Canvas/Ed pairing, allows a mistaken manual pair to be removed and rebuilt, blocks
progress while a suggestion is still pending, confirms before discarding a visible
draft, supports a one-course setup, and derives content defaults, per-course
initial-download modes, file-size policy, folder defaults, and overrides from controller
state. Labels have
keyboard buddies, primary collections have accessible names, checking a course keeps
the table focus/current row, and tab order follows each page's task sequence. An explicit
manual pair takes priority over any overlapping automatic suggestion; removing it makes
the still-applicable automatic suggestion visible again.
The setup host injects a reviewed local-save capability. The Review page seals the exact
already-rendered `SetupPlan`, confirms the profile, configuration path, output root, and
course/source counts, then creates the missing TOML configuration and any missing empty
course folders on a worker thread. It never creates the output root, resolves or writes
secrets, downloads content, runs initial sync, repairs launchers, or invokes due/MCP behavior. The
save fails closed if the displayed review is stale, replaced, already saved, concurrently
saving, or if the target configuration now exists; an existing file is never
overwritten. Existing safe course directories are reused; file/link collisions fail closed.
If config commit fails, only empty directories created by that attempt are rolled back.

Capability absence remains meaningful: the synthetic 300-course launcher under
`spikes/` injects no saver, keeps the visible `Preview only` state, and cannot write a
configuration.

The production source tree now also contains a real `FirstRunWindow` host. It validates
`Profile → Connect` locally, rejects an occupied configuration target before secret or
network access, and runs the existing read-only `discover_course_catalogs()` application
service on a worker thread. Canvas and Ed results stay independent and visible. A partial
result cannot continue silently: the user must retry, change settings, or explicitly
continue with available providers only; that choice disables failed providers in the
eventual configuration. Immutable draft generations prevent a late worker result from
replacing newer connection settings. A successful handoff constructs the existing
`SetupController` from real `CourseCandidate` values and injects the config-only saver at
the canonical profile path.

This first connection slice accepts only pre-provisioned `env:` and `keyring:` credential
references. It deliberately has no raw-token field and performs no keyring write, because
credential persistence and create-only config commit do not yet share an atomic rollback
boundary. The installable entry point is `canvas-ed-setup` when the optional `gui`
extra is installed; a source checkout may also use
`python -m canvas_ed_mcp.ui.first_run_window`. Dependency metadata and source/wheel
entry-point packaging are now declared, while frozen packaging, installer integration,
signing, and launcher installation remain separate release gates.

On the owner Windows device, the 2026-10-22 synthetic measurements for this formal
preview were: 12.8 ms to apply a Term filter, 13.6 ms to restore all 300 rows, 8.8 ms to
apply the sample Search filter, 6.7 ms to calculate 300 folder presentations, 1.19 s to
construct the complete 300-row Select window, and 1.54 s to construct the deliberately
worst-case 300-row Folders window. These are source-run measurements, not packaged-app
evidence. The sub-500 ms provisional cold-render gate therefore remains open.

The first daily dashboard slice is available through the additive `canvas-ed-app`
entry point when the optional `gui` extra is installed. Its visual direction is a
course-library workspace rather than a generic settings form: a persistent navy
library spine summarizes the active profile, connected providers, course/source counts,
and local destination; the main paper-toned workspace presents one review table and one
clear preview/apply sequence. Amber is reserved for partial coverage or attention states.

The dashboard consumes `ProfileOverview`, `DailySyncController`,
`ReviewedSyncOperation`, and `OutputFolderService` rather than CLI output. Preview and
apply run in Qt's worker pool; only structured progress crosses back to the UI thread.
Apply receives the exact in-memory `SyncReview` object produced by Preview. Closing an
active window first requests cooperative cancellation and waits for the next safe
boundary, while closing an idle reviewed window discards its provider lease. Opening the
course folder revalidates the current profile and exact output root at click time. The
existing `canvas-ed-mcp app` terminal menu and generated launchers remain unchanged in
this slice, so the desktop client is additive and does not remove the recovery path.

## 7. Architecture rules and acceptance gates

- The UI is an additive adapter. CLI, launchers, and MCP remain independently usable.
- Provider clients are lazy and isolated; a disabled provider does not resolve a secret.
- Exception text, tokens, signed URLs, raw payloads, and real course data do not enter UI
  models, logs, fixtures, screenshots, or release artifacts.
- Selection and mapping use immutable provider/instance/course identities, never display
  name equality.
- Review and apply are separate. A config, state, target, platform, or plan change makes
  the review stale and fails closed.
- Network and filesystem work stays off the UI thread. Cancellation is cooperative and
  only acknowledged at safe boundaries.
- Partial provider coverage remains visible and cannot be converted into generic success.
- Public source additions remain fail-closed through `release-manifest.txt` and the
  distribution audits.

## 8. Deferred decisions

- packager and exact PySide6 module/deployment inventory;
- English-only versus bilingual resource bundle for the first GUI release;
- installer format, signing, notarization, and update channel;
- whether setup/configure and daily sync ship together or as separate preview releases;
- icon source and final screen-reader acceptance evidence;
- when the generated `canvas_ed_app` launcher should switch from the terminal recovery
  menu to the selected desktop entry point.

These decisions require the bounded prototype protocol and measurements in
`UI_FRAMEWORK_EVIDENCE.md`; absence of evidence is `NOT_RUN`, not approval.
## 9. CourseHarbor App Shell vertical slice (rc17)

The desktop entry point now opens one PySide6 shell with Library, Due dates, Courses,
Connections, Health & About, plus a typed Open local library action. The shell remains a
presentation and lifecycle boundary: sync, credential, due, configuration, and folder
effects continue to enter through application-layer services rather than CLI output.

The due-date page separates This week and Semester into independent typed scopes. Each
scope owns its Load action, in-memory snapshot, export directory, and explicit Export
action. Refresh may update the existing last-good due cache; choosing another directory
only retargets that scope's snapshot. Semester retargeting preserves completion/manual
notes from the latest report in that directory. Files remain create-only and are written
only by the selected scope's Export action.

Courses reads a bounded profile-local sync-report history. A course row expands into
timestamped apply runs, and each run expands into files added, updated, deduplicated,
blocked, or failed. Reports contain display metadata, action counts, and contained local
paths only; credentials, remote payloads, and raw provider exceptions are excluded.

Reviewed discovery uses four-way bounded source concurrency. Canvas uses the Modules
`include[]=items` response when available, the Course Files index instead of repeated
per-file metadata calls, and a bounded pool for module-linked Page bodies. Fallbacks
retain the original provider-isolated behavior when Canvas omits inline items or blocks a
bulk index. Result ordering remains configuration-stable and apply stays serial.

The existing reviewed Library workspace is currently hosted by a compatibility adapter.
A later slice should extract it into a first-class `QWidget` page before removing the
legacy standalone window wrapper. Course reconfiguration also remains a reviewed PySide6
setup window launched from the Courses page; no terminal workflow is exposed by the shell.

## 10. Desktop startup and profile routing (rc17)

The no-argument GUI entry point now builds a local-only typed `StartupPlan`. No profile
routes to First run, one healthy profile opens the App Shell, and multiple or unreadable
profiles route to a Profile chooser. The chooser carries only `ProfileOverview` values;
it never resolves credentials or contacts a provider. A damaged config is visible but
cannot be opened or overwritten. **Switch profile** reuses page shutdown boundaries
before replacing the current window.

The same router is injected through First run into its reviewed Setup window. After a
successful save, **Open daily dashboard** loads the saved `ProfileOverview` and replaces
the setup journey in-process. Closing Setup emits one lifecycle result and also closes
the hidden First-run host, preventing a headless Qt process. Standalone setup omits the
router injection and preserves the detached App Shell fallback.

When embedded Setup closes without opening the App Shell, the First-run host now asks
the router to rebuild the Profile chooser before it closes. Profile removal is a typed
local application operation rather than a Qt file deletion: review binds the exact
config path and revision, confirmation names the selected profile, and commit moves only
that config into a private `.removed` archive on the same filesystem. Course libraries,
native credentials, state, logs/cache, and remote tokens are intentionally retained.

The installed `courseharbor` GUI script removes the normal Windows console for ordinary
environment installs. The private rc17 PyInstaller onedir prerelease passes automated
Windows startup, dependency, keyring-backend, inventory, relocation, and owner-visible
App Shell checks. Packaging evidence and the remaining installer, signing,
upgrade/uninstall, frozen credential round-trip, and macOS `NOT_RUN` gates are recorded in
`DESKTOP_STARTUP_AND_PACKAGING.md`.
