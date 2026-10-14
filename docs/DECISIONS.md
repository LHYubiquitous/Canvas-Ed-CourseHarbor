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
