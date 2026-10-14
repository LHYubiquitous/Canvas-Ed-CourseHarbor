# 风险与缓解

| ID | Risk | Probability | Impact | Detection | Mitigation / recovery |
|---|---|---|---|---|---|
| R-001 | 重构破坏“不覆盖用户编辑/不 backdate” | Medium | Critical | characterization + migration tests | 先锁测试，phase-by-phase adapter，默认 fail closed |
| R-002 | Canvas 手工 token 安装流程违反多用户 API policy | High if ignored | High | publication checklist | 个人 dev 与正式 OAuth 分轨；公开文档不伪装合规 |
| R-003 | Ed beta/unofficial endpoint 变化 | High | High | contract smoke、401/404/schema telemetry | provider isolation、可禁用、版本化 fixtures、清晰诊断 |
| R-004 | course code 猜测选错学期 | High currently | High | duplicate-code setup fixture | stable IDs + term display + per-item confirmation |
| R-005 | token 泄露进 repo/log/package | Medium | Critical | secret/history/package scans | keyring/env、redaction、allowlist package、rotate guidance |
| R-006 | state 损坏导致回填/覆盖 | Low–Medium | Critical | checksum/schema/migration tests | atomic save、backup、refuse unsafe state、dry-run |
| R-007 | output root 改动造成跨目录认领 | Medium | High | path containment + scope tests | explicit reattach/migration；不自动重写 paths |
| R-008 | 未知大小/大视频耗尽空间 | Medium | High | metadata/preflight/stream counters | policy before request + streaming hard cap + no partial install |
| R-009 | Windows/Linux path/keyring/launcher 不兼容 | Medium | High | three-OS CI + clean install matrix | console entry points、platformdirs、backend doctor |
| R-010 | MCP v2 breaking changes | High when upgrading | Medium–High | in-memory MCP tests | keep `<2` until isolated migration; follow official guide |
| R-011 | Gradescope scraping 失效或政策问题 | Medium | Medium–High | opt-in integration test | optional provider、默认关闭、明确非官方 |
| R-012 | 个人课程脚本/数据进入开源包 | High currently | High | manifest + absolute path/content scan | `src/`/`legacy/` 分离、allowlist build、人工 review |
| R-013 | 第三方课程内容被再分发 | Medium | Critical | archive content review | outputs/downloads/exports/data 永久排除；synthetic fixtures only |
| R-014 | 用户误认为设计已经实现 | Medium | Medium | docs status consistency check | 全文使用 VERIFIED/PROPOSED/NOT_IMPLEMENTED 状态 |
