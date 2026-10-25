# Architecture Decision Records

> 日期：2026-10-13；全部决策均可在实现证据变化时修订。

## ADR-001 — Preserve + compose，而非替换或整库重写

- Status: ACCEPTED FOR PLAN
- Context: REQ-001、REQ-010、REQ-016。
- Options: (A) 采用成熟 Canvas Downloader；(B) 组合现有项目 + 标准库；(C) 从零重写。
- Decision: 选择 B。保留当前经过 19 个测试覆盖的同步语义，复用 `platformdirs`、`keyring`、MCP SDK、Python packaging；外部 Canvas downloader 仅作为设计参考。
- Why: 没有候选同时覆盖 Canvas + Ed + MCP + 现有“不覆盖用户编辑/不 backdate/内嵌 HTML 图片”语义。GPL Canvas Downloader 不适合直接拷贝进 MIT 项目；替换也会丢失 Ed 和 MCP 能力。
- Trade-off: 需要自己维护 orchestration/state/provider glue。
- Revisit: 若出现许可兼容且完整覆盖 Canvas+Ed+MCP 的成熟库。

## ADR-002 — 课程必须使用 stable ID 并逐平台确认

- Status: ACCEPTED FOR PLAN
- Context: REQ-005、REQ-006、REQ-012。
- Options: code 模糊匹配；自动同名合并；用户确认 stable ID。
- Decision: stable provider course ID + instance identity 为主键；code/name/term 只用于显示。Canvas 与 Ed 分别确认，跨平台映射也显式确认。
- Why: 当前“同 code 取最大 ID/最新 year”在复读、跨学期或多个实例时会选错。
- Trade-off: setup 多一步，但可审计且可修改。
- Revisit: provider 提供可信、全局稳定的跨平台 course mapping 时。

## ADR-003 — Secrets 默认进入 OS keyring

- Status: PROPOSED
- Context: REQ-002、REQ-003、REQ-010。
- Options: repo JSON mode 600；MCP host env 明文；OS keyring + env fallback。
- Decision: 首选 keyring；CI/headless 使用环境变量；明文 credential file 只能显式 opt-in、放在 platform config dir 且警告。
- Why: mode 600 不具备统一 Windows 语义，repo-local secret 容易进入快照；Canvas 官方也建议 native app 利用系统 keychain。
- Trade-off: Linux backend 可用性复杂，必须检测 Null/backend 失败。
- Revisit: OAuth token broker 或平台原生安装器接管秘密时。

## ADR-004 — Canvas 认证分个人开发模式与正式公开模式

- Status: ACCEPTED FOR PLAN
- Context: REQ-003、REQ-017。
- Decision: 个人源码运行可记录手工 token 路径及官方警告；公开多用户发行的合规路径必须是 OAuth2，且说明 institution Developer Key 前置条件。
- Why: Canvas 官方 API policy 文档明确，多用户 app 不能要求每位用户手工生成并输入 token。
- Trade-off: OAuth onboarding 不能做到对所有学校“一键开箱即用”。
- Revisit: Canvas 政策或授权产品形态变化时。

## ADR-005 — Config、state、content、secret 四类数据分离

- Status: ACCEPTED FOR PLAN
- Context: REQ-002、REQ-006、REQ-011。
- Decision: `platformdirs` 管 config/state/log；用户选的 output root 放课程内容；keyring 放 secret；repo 只放代码和示例。
- Why: 允许升级、多个 profile、换 output root 和安全打包。
- Trade-off: 需要 state v3 迁移工具和诊断命令。
- Revisit: 项目转为完全 portable single-folder app 时。

## ADR-006 — MCP SDK v2 不与可迁移性 MVP 同步强制迁移

- Status: PROPOSED
- Context: current `mcp>=1,<2`、官方 v2 已稳定。
- Decision: 先保持 v1 pin，完成配置/provider/同步垂直切片；随后独立分支升级 v2，并使用官方 migration guide 和 MCP in-memory tests。
- Why: 把协议大版本迁移与同步重构叠加会放大回归面。
- Trade-off: 短期继续处于维护中的 v1 line。
- Revisit: v1 结束安全支持或 host 只支持新协议时。

## ADR-007 — 通用发行包采用 allowlist

- Status: ACCEPTED FOR PLAN
- Context: REQ-016、REQ-017。
- Decision: build metadata 明确 include package/tests/docs/examples，默认排除 credentials、state、output/downloads/exports、个人课程资料、课程专用一次性脚本和绝对路径 launcher。
- Why: blocklist 无法可靠覆盖未来生成物和个人数据。
- Trade-off: 新增合法文件需更新 manifest。
- Revisit: 仓库结构完成 `src/`/`legacy/` 分离并有自动 package-content tests 后。

## ADR-008 — 首发 MCP scope 默认只读，Gradescope 与写操作显式 opt-in

- Status: ACCEPTED FOR IMPLEMENTATION
- Context: P0-04、REQ-002、REQ-004、REQ-018、R-011。
- Decision: 首个可迁移版本以 Canvas/Ed 课程同步和只读 MCP 工具为默认公开 scope。Gradescope 保留为独立 optional provider，但默认关闭且其凭据缺失不得阻塞 Canvas/Ed。Canvas 提交、Canvas 讨论发帖、Ed 发帖/编辑/回复/采纳以及 Ed Workspace create/update/delete 均保留兼容代码，但在 provider-aware MCP 中默认不注册；只有用户显式启用 write capability 并通过配置预览/确认后才暴露。
- Why: 只读默认面足以覆盖课程资料拉取，减少非官方 Gradescope 登录依赖、凭据面和误操作风险，同时不删除现有用户可能依赖的能力。
- Compatibility: legacy 单文件 server 在迁移期间继续保持原工具面；新 package 的默认配置不继承 legacy 写权限。
- Revisit: 完成真实只读 integration、权限提示和写操作 contract tests 后，再决定公开版本是否提供显式写能力预设。

## ADR-009 — PySide6 / Qt Widgets 作为正式 GUI 实现框架

- Status: **ACCEPTED FOR IMPLEMENTATION; RELEASE GATES OPEN**
- Context: 300-course framework evidence spikes、2026-10-22 owner-only Windows 人工测试，以及首次配置中大量课程选择造成的认知负担。
- Options: (A) PySide6 / Qt Widgets；(B) wxPython；(C) Tkinter / ttk；(D) 暂停 desktop GUI，回到 enhanced TUI 或 packaged local web UI。
- Decision: 后续 GUI implementation 只推进 PySide6 / Qt Widgets。wxPython 与 Tkinter 停止 feature work，现有 spike、测试和测量结果作为历史比较证据保留。GUI 必须继续通过 typed application-service boundary 复用现有 setup、sync、due、provider、configuration、secret 与 MCP 语义，不得从 Qt event handler 调用 CLI、解析 stdout 或复制 domain logic。
- Why: 在 owner-only Windows 的迭代人工测试中，PySide6 的 course catalogue 信息层级、search/filter、批量选择、显式 mapping、link/separate 决策撤回、folder settings 与 exact review 流程最符合用户预期。Qt model/view、queued signals、threading primitives 与 cross-platform accessibility foundation 也更适合后续 thin adapter。Tkinter 在当前显示缩放下清晰度不足，且 modifier-key multiple selection 不适合整学期选课。wxPython spike 仍存在未修的 mapping recovery 缺陷：`Keep as separate courses` 会移除 pending suggestion，却没有可见 Undo；这是本项目 spike 缺陷，不是对 wxPython 一般能力的否定。
- Trade-offs: PySide6 runtime 与冻结包较大；community binaries 的 LGPLv3 合规、notices 与 replacement/relinking 权利需要正式方案。当前 source-widget initial-render maximum 为 735 ms，超过 provisional 500 ms threshold。Windows onedir startup/relocation smoke 已有 rc17 `PASS` evidence；full interactive frozen journey、RSS、Physical keyboard、Narrator/NVDA、VoiceOver、macOS bundle、signing 与 notarization 仍为 `NOT_RUN` 或 `DEGRADED`。
- Constraints: 框架选择仅授权 implementation，不代表 release readiness。CLI、launchers 与 MCP 必须继续独立可用；network/filesystem work 不得阻塞 GUI thread；review/apply、stale-plan、partial-warning、redaction 与 safe cancellation 规则保持不变。
- Revisit: 若 PySide6 无法形成可接受的 license-compliance packet、Windows/macOS 可复现安装包、assistive-technology journey，或在等价 packaged measurement 中无法满足经确认的启动/响应阈值，则重新评估 wxPython 或其他产品形态。

## ADR-010 — Setup 的首个正式写入仅创建已确认配置

- Status: **IMPLEMENTED IN SOURCE; PACKAGING GATES OPEN**
- Context: PySide6 已能生成 exact setup preview，但 synthetic preview 必须继续保持 zero-write；真实首次配置仍缺 Profile/Connect/Discover host。
- Decision: Review 页面本身不拥有写权限。只有 host 显式注入 `ConfigOnlySetupSaver` 时才显示 `Save configuration`。保存对象必须是当前 `SetupController` 中同一个、仍为 current 的 review；proof 覆盖 config、可见 preview 与 per-course initial mode，并在写入前再次校验目标 revision。当前能力只允许 create-new TOML config，既不覆盖已有配置，也不创建课程目录、保存 secret、执行下载/initial sync、修复 launcher 或调用 due/MCP。
- UX and failure boundary: 点击保存先显示 profile、config path、output root、course/source counts 与 zero-download 说明；文件写入在 Qt worker thread 完成。写入期间禁止 Back、Exit 与重复保存；成功后进入明确的 saved/close 状态。UI 只显示 stable issue code 对应的安全文案，不显示 exception text、provider payload 或 credential。
- Compatibility: 未注入 capability 的 synthetic launcher 仍为 `Preview only`，没有隐藏的 commit action。CLI、sync、due、MCP 与 provider 实现未改变。
- Host integration: `FirstRunWindow` 已接入真实 profile path、`env:`/`keyring:` reference、后台只读 Canvas/Ed discovery、显式 partial acceptance 与现有 `SetupController`。迟到 worker result 由 immutable generation 拒绝；失败 provider 在“仅继续可用平台”后会被禁用。安装 `gui` 可选依赖后的正式入口为 `canvas-ed-setup`；源码目录仍可使用 `python -m canvas_ed_mcp.ui.first_run_window`。
- Folder ownership: Profile 页只确定 profile 与 config target；Library root 的最终选择归 Folders step 所有。setup mode 可在该页修改 root，并为每门新 logical course 选择其 direct child；进入 exact review 前，application boundary 要求 root 已存在且为目录。root mutation 进入 application revision 与 exact review，但不创建或迁移文件。configure mode 在 UI、controller 与 configure plan 三层拒绝 existing-root relocation。
- Revisit: raw token 输入和 keyring 写入必须另行设计可审核、可恢复的 secret transaction；packaging、installer/launcher integration 与首次同步 apply 也不扩大本 ADR 的 config-only 写入范围。

## ADR-011 — Validate credentials before persistence; never attempt secret rollback

- Status: **ACCEPTED FOR IMPLEMENTATION**
- Context: graphical Canvas/Ed token onboarding, token rotation, and the lack of an atomic transaction spanning native keyring, provider requests, and config commit.
- Decision: the application-layer `CredentialController` owns credential inspection, read-only testing, and persistence. For a new or replacement token it validates the target and native keyring readiness first, performs one provider-isolated read-only connection test with a short-lived `SecretValue`, closes the provider session, checks cancellation again, and only then writes the token to the deterministic `keyring:canvas-ed-mcp/<profile>/<provider>` reference. Qt code must not call `store_secret()` directly.
- Replacement: the controller inspects only presence and never returns an existing value. An existing keyring item requires explicit replacement confirmation before provider or write I/O. A blank replacement field means keep the existing credential; `Test existing` may resolve it inside the worker but never returns it to Qt.
- Failure and cancellation: unsafe/unavailable keyring fails before provider construction. Authentication, network, schema, close, or cancellation failure before persistence leaves the old value unchanged. Once a native keyring write starts it is not cancellable. A write error returns sanitized `credential_state_unknown`; it does not guess that the old value survived.
- Config boundary: a successfully validated and stored credential remains if the user later abandons first run or config commit fails. It is a reusable local orphan at a deterministic reference, not a reason to read or retain the previous token for rollback. Retrying setup or `Test existing` is the recovery path.
- Externally managed references: `env:` credentials are read-only in the GUI. They may be tested, but the application cannot save, replace, or delete them.
- Deletion: local deletion is deferred from this vertical slice. A future action must state that deleting a local keyring item does not revoke the Canvas or Ed token remotely.
- Secret lifetime: raw values may exist only in the masked input and the minimum worker/provider/keyring call chain. They must not enter config, state, logs, exception text, command arguments, environment variables, clipboard, Qt models, accessibility text, screenshots, fixtures, or snapshots. Clearing a widget reduces exposure but is not a claim of physical memory zeroization.
- 中文说明：采用“先只读验证、后写入 keyring”，使无效 token、网络失败和写入前取消都不会破坏旧凭据；config 后续失败时保留已验证的新凭据并允许重试，不读取旧 token 做不可靠回滚。
