# 测试、部署与运行

> 当前 GitHub prerelease 为 `v0.2.0rc17`。该版本加入 CourseHarbor App Shell、图形化
> credential workflow、独立 due-date 页面、sync-run reports 与经过审核的 Windows
> portable bundle。`v0.2.0rc16` 保持不可变，旧版本证据仅作历史参考。实时发布状态以 public repository 的
> GitHub Release 页面为准；PyPI 未授权、未执行。

## 验证层级

1. **Static/schema**：格式、类型、config/state schema、package manifest。
2. **Mock/characterization**：合成 Canvas/Ed response 和临时文件系统。
3. **Integration**：MCP stdio/in-memory、真实 OS keyring backend、安装后的 console entry point。
4. **Real account**：经用户明确授权的只读 sandbox/course；输出必须脱敏。
5. **Release**：三系统 clean install + package/secret/license audit。

## Requirements test matrix

| Req | Scenario | Planned evidence | Current |
|---|---|---|---|
| REQ-001 | baseline/new/changed/edited/deleted/duplicate/HTML image | characterization matrix + decision/storage/renderer tests | legacy + portable engine/Ed-only CLI synthetic tests PASS; real baseline PASS; first real import-all quality DEGRADED; remediation HUMAN_VERIFIED_WITH_WARNINGS |
| REQ-002/003 | secret setup、redaction、revocation docs、OAuth warning | fake keyring + package scan + doc review | keyring/env/redaction MOCK PASS; Canvas docs REVIEWED; Ed evidence DEGRADED |
| REQ-004 | Canvas-only / Ed-only / both | provider matrix tests; no disabled-provider requests | Canvas-only/Ed-only/mixed portable CLI synthetic PASS; Windows real Canvas/Ed content `HUMAN_VERIFIED_WITH_WARNINGS` |
| REQ-005/006/012 | duplicate code/term、逐项确认、reconfigure diff | CLI golden/snapshot + config round trip | interactive setup/configure synthetic PASS; Windows Ed/Canvas/mixed discovery PASS |
| REQ-007 | output root/folder conflicts/unwritable path | temp dirs + platform path mocks | path/folder MOCK PASS; unwritable NOT RUN |
| REQ-008/009 | type/MIME/ext/max size | table tests + unknown-size streamed overflow | policy and portable engine wiring synthetic PASS; real Canvas/Ed runs exercised configured selection |
| REQ-010/014 | Windows/macOS/Linux clean install and entry points | CI matrix + built wheel install | Phase 5 private CI REMOTE VERIFIED across the declared OS/Python matrix and distribution gate |
| REQ-011 | profiles/output-root isolation/state migration | v3→v5 fixtures + crash/restart tests | state-v5 namespace/backup/migration and engine wiring synthetic PASS; isolated real profiles PASS |
| REQ-013 | dry-run/cancel | filesystem snapshot remains unchanged | setup/baseline/sync + configure cancel MOCK PASS |
| REQ-015 | diagnostics/redaction/exit codes | CLI tests + secret canaries | stable CLI categories/redaction MOCK PASS; real failure categories observed without secret disclosure |
| REQ-016/017 | allowlist release and compliance | archive manifest + secret/path/license audits | PUBLIC CLEAN-HISTORY RC PASS; GitHub prerelease published; PyPI NOT RUN |
| REQ-018 | provider-aware MCP | tool catalog/invocation matrix | direct calls + in-memory ClientSession + Windows stdio subprocess MOCK PASS |

## 当前可验证命令（VERIFIED 2026-10-13）

```bash
./.venv/bin/python -B -m unittest discover -s tests -v
./.venv/bin/python -B -m py_compile canvas_ed_mcp.py sync_courses.py setup_tokens.py verify_mcp.py export_ed_lessons.py export_ed_lessons_html.py export_ed_lessons_pdfs.py organize_ed_lessons.py
./.venv/bin/python -B -c "import mcp,httpx,pydantic,bs4,gradescopeapi; print('dependency imports: OK')"
```

Phase 0—5 当前 Windows locked environment 的 synthetic/mock run 为 `Ran 226 tests ... OK (skipped=1)`。Fresh `0.1.0rc1` build 的 32-entry wheel 与当前 75-entry sdist 匹配 explicit allowlist；103-file exact-manifest clean-source archive 连续构建 SHA-256 一致，解包 source suite 为 194 tests OK（1 skipped）。此前 wheel、sdist 与 clean-source 各自在 clean environment 安装，checkout 外 CLI/MCP protocol smoke 和 `pip check` 全部通过；Phase 5 private CI 已远端验证完整 Windows/macOS/Linux、Python 3.10—3.15 matrix，以及 distribution/clean-source install/audit/MCP smoke。2026-10-14 的 Windows real-account Canvas-only、Ed-only、both course-catalog smoke 均为 `OK`，未保存 payload。同日，commit `c63e0a4` 在 arm64 owner Mac 上完成 59/59 stages（Python 3.10.19/3.15.0b3），包括 build/audit/clean-install/MCP protocol smoke 与 synthetic native Keychain cleanup；Codex MCP-host startup 和 host-side no-provider `server_status` 也为 `PASS`。macOS real-account calls、private legacy actual sync 与 publication 继续为 `NOT_RUN`，见 `docs/MACOS_AUTOMATED_RELEASE_TEST.md` 与 `docs/MACOS_MANUAL_RELEASE_TEST.md`。

不要用系统 `python3` 的失败代替项目结论：本机系统 Python 3.9 未安装 `httpx`，而仓库 `.venv` 测试全部通过。这也证明 clean-install 文档/CI 是必要工作。

## CLI status

```text
canvas-ed-mcp setup
canvas-ed-mcp configure [--profile NAME] [--dry-run]
canvas-ed-mcp sync [--profile NAME] [--dry-run]
canvas-ed-mcp app [--profile NAME]
canvas-ed-mcp due week|semester [--profile NAME]
canvas-ed-mcp launchers install [--profile NAME]
canvas-ed-mcp open-course-folder [--profile NAME]
canvas-ed-mcp doctor [--profile NAME] [--show-paths]
canvas-ed-mcp serve [--profile NAME]
```

`sync` 支持显式配置的 Canvas/Ed source、`--config`、重复的 `--course` 和
`--dry-run`。未选中的 provider 不解析 secret；mixed apply 在同一 state lock 下按配置顺序执行。
Windows owner-device 真实 Ed 与 Canvas content run 均为
`HUMAN_VERIFIED_WITH_WARNINGS`，见 `ED_SYNC_MANUAL_TEST.md` 与
`CANVAS_SYNC_MANUAL_TEST.md`。这些结论都不外推到其他 OS 或 legacy launcher。

## CI matrix（REMOTE VERIFIED）

- OS：Ubuntu latest、Windows latest、macOS latest。
- Python：Windows/macOS 跑 3.10 与 3.14 边界；Ubuntu 另跑 3.11—3.13，覆盖完整声明范围。
- test jobs 安装 source、运行 full suite/CLI/pip check；build job 构建 wheel/sdist、执行 explicit manifest audit、安装 exact wheel，并在 checkout 外执行 CLI + MCP stdio smoke。
- 合成 fixture 禁止含真实 course IDs、账号、token、学校私有 URL 或课程内容。
- 真实 provider smoke 不在 fork PR 默认运行；只在受保护 secret/environment 下手动触发并脱敏。

## 故障与恢复原则

| Failure | Default behavior | Recovery |
|---|---|---|
| secret/backend unavailable | fail before network | `doctor` 指明 keyring backend/env 名，不打印值 |
| config invalid | no sync/no state write | 输出字段级错误，保留上个 config |
| state unknown/corrupt | no download/no overwrite | 从备份恢复或显式重新 baseline |
| course missing/permission lost | isolate course; continue others | reconfigure/remove mapping；保留 local files |
| single item timeout/schema error | record sanitized failure; continue | retry next run with bounded backoff |
| disk full/size cap | no partial final file | 清理 temporary，调 policy/output |
| user cancel | zero mutation before commit point | rerun setup/configure |

## 发布前验证

1. `git status` 和 intended diff 人工检查。
2. wheel/sdist/archive file list 与 allowlist 对比。
3. 扫描 token patterns、private keys、credentials/state、绝对 home paths、signed queries。
4. `pip-licenses` 或 lockfile-based license report（工具需先审查/安装）。
5. 文档链接、版本、`VERIFIED/PROPOSED` 状态一致性检查。
6. 用户明确授权目标仓库/可见性后，才执行 push/release。

Phase 5 gate（2026-10-14）第 1—6 项均已完成。当前 private development remote 继续保存私人历史；public clean-history repo、`v0.1.0rc1` tag 和 GitHub prerelease 已从 audited archive 发布。PyPI 未授权、未执行。权威安全结论见 `docs/RELEASE_AUDIT.md`；archive 的 manifest 与 SHA-256 保存在 ignored release artifacts 中，不把自引用 digest 写入 archive 本身。

Phase 6 P6-01 的当前 synthetic gate（2026-10-15）为 266 tests OK（1 个 Windows symlink privilege skip）。真实 Ed baseline 已通过；首个隔离 `import_all` 为 `DEGRADED`。精确 AU static host、无 asset credential 转发、固定可信 reading CSS、render issue 汇总与 hostile sibling test 修复后的第三个隔离 profile 现为 `HUMAN_VERIFIED_WITH_WARNINGS`：321/325 图片真实内嵌，4 个非可见占位符，65/65 HTML reading CSS，零外链图片；第三次稳定执行为 `skip=69`、零 manifest 变化，76-file secret scan 全零。首次 apply 的三个 content-quality issue 正确导致 exit 1 并由 owner 视觉审核接受，因此不得记录为 issue-free automated PASS。完整证据见 `docs/ED_SYNC_MANUAL_TEST.md`。

Phase 6 最终 synthetic gate 为 309 tests OK（1 个预期 Windows symlink privilege
skip）；Windows P6-03 Ed/Canvas/mixed configure discovery 均为 `PASS`。随后 rc5–rc10
完成了 daily app、跨平台 launchers、due reports、Windows timezone、macOS venv 路径、
Canvas warning review 和 weekly count 修复。Windows 双击菜单、Canvas/Ed 实际内容、
macOS `.command`、MCP host、Canvas catalog、同步与 due report 均已由 owner 核验；
Canvas Files 403 和 Ed 已接受的内容质量 warning 仍按
`HUMAN_VERIFIED_WITH_WARNINGS` 记录，不提升为无 warning 的自动化 PASS。

rc10 commit `f178c27` 的 private CI 已在 Windows/macOS/Linux、Python 3.10–3.15
全部通过，并包含确定性构建、wheel/clean-source 安装、CLI 和 MCP protocol smoke。
rc11 只调整版本、公开项目链接和文档。其本地 gate 为 331 tests PASS（1 个预期
Windows symlink skip），固定 `uv 0.11.28` 的 lock/SBOM 审计、确定性 packet、
Python 3.14 wheel/clean-source 双安装和 MCP smoke 均为 PASS；private remote CI
已经通过。rc12 进一步增加完整中文 README、Canvas token 设置路径和澳洲区 Ed token
入口；本地 gate 为 332 tests PASS（1 个预期 skip），42-entry wheel、99-entry sdist
与 127-file clean-source 确定性审核 PASS。commit `7340b14` 的 rc12 remote CI 已在
Windows/macOS/Linux、Python 3.10–3.15 以及独立 distribution build/audit job 全部通过。
Linux owner-device keyring/desktop launcher 仍为
`NOT_RUN`，PyPI 仍为 `NOT_RUN / NOT AUTHORIZED`。
