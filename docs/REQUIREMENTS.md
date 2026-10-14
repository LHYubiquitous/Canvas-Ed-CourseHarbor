# 可迁移与开源改造需求

> 状态：PHASE 0—3 MOCK_VERIFIED / P4 AUTOMATED WORK COMPLETE / P4-04 MANUAL AUTH REQUIRED / P5 NOT STARTED
> 日期：2026-10-13

## 目标用户与结果

目标用户是使用 Canvas、Ed 或两者的学生/个人用户。用户应能在自己的 Windows、macOS 或 Linux 电脑上完成安全安装，逐平台选择当前学期课程，确认每门课程的本地映射和下载规则，并在学期中安全修改；现有同步保护规则必须继续成立。

## 需求台账

| ID | Requirement | Priority | Source | Acceptance | Status |
|---|---|---:|---|---|---|
| REQ-001 | 保留 `CURRENT_BASELINE.md` 中 14 条同步保护规则 | MUST | user + code | Given 旧/新/编辑/删除/重复四类本地状态，When 同步运行，Then characterization tests 全通过 | CONFIRMED |
| REQ-002 | 安全本地部署，不把 secret 写进仓库、日志或文档 | MUST | user | setup 后 Git/status/package 扫描无 token；首选 OS keyring，环境变量为受控 fallback | CONFIRMED |
| REQ-003 | 提供 Canvas 与 Ed 凭据获取指导，并区分个人测试 token 与正式 OAuth 政策 | MUST | user + research | 安装文档明确 Canvas policy、撤销方法、最小权限与 Ed beta 风险 | DOCUMENTED; Ed authenticated UI DEGRADED |
| REQ-004 | Canvas-only、Ed-only、both 三种模式均可安装和运行 | MUST | user | 任一平台未配置时不会被初始化、请求或报告为失败 | CONFIRMED |
| REQ-005 | 每个平台、每门课程都必须由用户逐项确认，不自动全选 | MUST | user | setup 显示 platform/course ID/term/name，默认未选；确认后才写配置 | CONFIRMED |
| REQ-006 | 每学期课程集合可变，支持安全 `configure` 增删改 | MUST | user | 修改课程后先 preview diff；保留旧 state/本地文件；新增课单独选择 baseline 行为 | CONFIRMED |
| REQ-007 | 安装时询问 output root、课程文件夹名；默认“桌面/课程代码”但允许修改 | MUST | user | 每门课显示最终绝对路径并确认；冲突/不可写提前失败 | CONFIRMED |
| REQ-008 | 下载内容类型可配置，并支持平台/课程级 override | MUST | user | files/pages/lessons/resources 等类别可选；未选类别不扫描或不落盘 | CONFIRMED |
| REQ-009 | 支持 include/exclude 扩展名/MIME 和可配置文件大小上限 | MUST | user | 已知大小在请求前跳过；未知大小在流式读取中硬性终止且无残留 | CONFIRMED |
| REQ-010 | Windows、macOS、Linux 有安装、配置、启动、更新和卸载路径 | MUST | user | CI 三系统通过 unit/smoke；无绝对个人路径和 shell-only 主入口 | IMPLEMENTED; Windows LOCAL_PASS; remote/macOS/Linux NOT_RUN |
| REQ-011 | 配置、状态、日志和用户资料分离，支持多个 profile/output root | MUST | inferred | 一个用户可创建两个 profile，互不认领/覆盖 state | PROPOSED |
| REQ-012 | 课程映射使用稳定平台 ID + instance identity，不再只按 code 猜测 | MUST | code gap | 同 code 不同 term 同时存在时能明确选择且不会串课 | PROPOSED |
| REQ-013 | 安装/重配置必须有 dry-run/preview 和明确确认；默认无破坏性动作 | MUST | safety | 取消确认时 config/state/资料均不变 | PROPOSED |
| REQ-014 | 打包成标准 Python project，提供稳定 console commands | SHOULD | portability | clean venv 可安装，`canvas-ed-mcp --help` / `course-sync --help` 可运行 | PROPOSED |
| REQ-015 | 建立脱敏日志、诊断包和明确退出码 | SHOULD | operations | 失败可定位且日志不含 Bearer/signed query/账号原始 payload | PROPOSED |
| REQ-016 | 分离通用产品代码与 legacy/personal scripts/data | MUST | publication | release manifest 不含课程专用数据、绝对路径、凭据/state/cache | PROPOSED |
| REQ-017 | 公开发布前完成依赖许可、版权归属、秘密与历史扫描 | MUST | user + publication gate | audit + 人工 review 通过并保留 THIRD_PARTY_NOTICES | PROPOSED |
| REQ-018 | MCP 工具按启用 provider 暴露或给出清晰 unavailable 状态 | SHOULD | portability | Canvas-only 不要求 Ed；Ed-only 不要求 Canvas/Gradescope | MOCK_VERIFIED via direct + in-memory protocol + Windows stdio tests |

## MUST 的 Given / When / Then 摘要

- **REQ-001**：Given 用户已修改 sync-managed 文件，When 远端内容更新，Then 用户文件原样保留且新版本单独保存。
- **REQ-002**：Given 新安装，When 输入 token 并生成诊断/快照，Then secret 不出现在项目、命令行参数、日志、文档和包中。
- **REQ-004/005**：Given 两个平台返回若干课程，When 用户配置，Then 只连接选定平台且仅同步每项确认过的课程。
- **REQ-006**：Given 学期中增删课程，When 重配置，Then preview 明确显示变更且历史资料/state 不被破坏。
- **REQ-007—009**：Given 用户配置目录/类型/大小，When 同步，Then所有下载均在 policy 内并落到确认路径。
- **REQ-010**：Given 三个支持系统之一，When 按文档从 clean environment 安装，Then相同 CLI 行为可运行。
- **REQ-012**：Given同名/同 code 多学期课程，When选择，Then配置记录确切 instance + platform course ID + term。
- **REQ-016/017**：Given准备 release，When生成分发包，Then只含 allowlist 文件并通过秘密/绝对路径/许可证人工复核。

## 可逆假设

- 默认 output root 取系统桌面（若可发现且可写），否则退回用户明确选择的目录。
- 默认课程文件夹名优先规范化 course code；冲突时附 term 或让用户编辑，绝不静默合并。
- 初次启用课程默认采用“从现在开始”的 baseline，不补拉历史；用户可显式选择全量初始导入，但这不是默认。
- 用户删除 sync-managed 文件后，远端未变化则不恢复；若远端后来真的更新，默认视为新变化并保存新版。该语义在实现前需用户最终确认。
- 大小限制以单文件压缩前字节数为准；archive 解压限制另设防 zip bomb 策略。
- Gradescope 不是本轮课程镜像 MVP 的必选 provider；保留现有 MCP 工具但单独做 opt-in。

## 开放问题（不阻塞架构）

1. “远端更新后是否恢复用户曾删除的文件”是否保持现有行为，还是永久 tombstone？建议保持现有行为并提供 per-course policy。
2. 默认单文件上限具体数值；建议向导给出“无视频 / 100 MiB / 自定义”预设而非硬编码唯一值。
3. Linux 是否包含 headless/server 场景；这会决定 keyring 不可用时的 secret fallback。
4. 首个公开版本是否保留 Gradescope 与所有写操作；建议先保留代码但默认禁用，减少凭据和误操作面。
5. 是否要求从 PyPI 安装，还是先提供 GitHub source + `pipx`/venv；建议 MVP 先 source release，稳定后再 PyPI。

## 不在 MVP 范围

- 自动获取学校的 Canvas OAuth Developer Key。
- 绕过学校/Canvas/Ed 的授权、DRM、访问截止或内容政策。
- 默认下载大型讲座视频、自动转录或云端上传。
- 自动删除用户本地课程资料。
- 无用户确认的后台定时任务。

## 隐私与发布范围

- 本次只读取并写入当前仓库；没有读取主目录、浏览器、钥匙串或其他仓库。
- 联网检索只使用通用项目/技术词和公开仓库名，没有发送 token、账户、课程内容或私有代码。
- 本地生成不等于获准公开；推送、创建公开仓库或发布包仍需用户另行明确授权。
