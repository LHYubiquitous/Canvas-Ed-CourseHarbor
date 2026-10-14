# 测试、部署与运行

> 当前结果是 `LOCAL RELEASE_CANDIDATE`：此前经授权的 Windows real-account 只读 catalog smoke 已完成；本阶段没有新增真实账户访问，也没有公开发布。

## 验证层级

1. **Static/schema**：格式、类型、config/state schema、package manifest。
2. **Mock/characterization**：合成 Canvas/Ed response 和临时文件系统。
3. **Integration**：MCP stdio/in-memory、真实 OS keyring backend、安装后的 console entry point。
4. **Real account**：经用户明确授权的只读 sandbox/course；输出必须脱敏。
5. **Release**：三系统 clean install + package/secret/license audit。

## Requirements test matrix

| Req | Scenario | Planned evidence | Current |
|---|---|---|---|
| REQ-001 | baseline/new/changed/edited/deleted/duplicate/HTML image | characterization matrix + decision/storage/renderer tests | 28 legacy sync + 15 domain + 15 storage + 5 renderer MOCK PASS; engine wiring pending |
| REQ-002/003 | secret setup、redaction、revocation docs、OAuth warning | fake keyring + package scan + doc review | keyring/env/redaction MOCK PASS; Canvas docs REVIEWED; Ed evidence DEGRADED |
| REQ-004 | Canvas-only / Ed-only / both | provider matrix tests; no disabled-provider requests | Canvas-only setup MOCK PASS; Ed adapter MOCK PASS; Ed-only/both wiring NOT RUN |
| REQ-005/006/012 | duplicate code/term、逐项确认、reconfigure diff | CLI golden/snapshot + config round trip | setup/stable ID + explicit Canvas↔Ed mapping + pure reconfigure MOCK PASS; interactive CLI pending |
| REQ-007 | output root/folder conflicts/unwritable path | temp dirs + platform path mocks | path/folder MOCK PASS; unwritable NOT RUN |
| REQ-008/009 | type/MIME/ext/max size | table tests + unknown-size streamed overflow | pure policy MOCK PASS; engine wiring pending |
| REQ-010/014 | Windows/macOS/Linux clean install and entry points | CI matrix + built wheel install | Phase 5 private CI REMOTE VERIFIED across the declared OS/Python matrix and distribution gate |
| REQ-011 | profiles/output-root isolation/state migration | v3→v4 fixtures + crash/restart tests | state v4 namespace/backup/migration MOCK PASS; engine wiring pending |
| REQ-013 | dry-run/cancel | filesystem snapshot remains unchanged | setup/baseline/sync + configure cancel MOCK PASS |
| REQ-015 | diagnostics/redaction/exit codes | CLI tests + secret canaries | NOT RUN |
| REQ-016/017 | allowlist release and compliance | archive manifest + secret/path/license audits | LOCAL RELEASE_CANDIDATE PASS; publication NOT RUN |
| REQ-018 | provider-aware MCP | tool catalog/invocation matrix | direct calls + in-memory ClientSession + Windows stdio subprocess MOCK PASS |

## 当前可验证命令（VERIFIED 2026-10-13）

```bash
./.venv/bin/python -B -m unittest discover -s tests -v
./.venv/bin/python -B -m py_compile canvas_ed_mcp.py sync_courses.py setup_tokens.py verify_mcp.py export_ed_lessons.py export_ed_lessons_html.py export_ed_lessons_pdfs.py organize_ed_lessons.py
./.venv/bin/python -B -c "import mcp,httpx,pydantic,bs4,gradescopeapi; print('dependency imports: OK')"
```

Phase 0—5 当前 Windows locked environment 的 synthetic/mock run 为 `Ran 226 tests ... OK (skipped=1)`。Fresh `0.1.0rc1` build 的 32-entry wheel 与当前 75-entry sdist 匹配 explicit allowlist；103-file exact-manifest clean-source archive 连续构建 SHA-256 一致，解包 source suite 为 194 tests OK（1 skipped）。此前 wheel、sdist 与 clean-source 各自在 clean environment 安装，checkout 外 CLI/MCP protocol smoke 和 `pip check` 全部通过；Phase 5 private CI 已远端验证完整 Windows/macOS/Linux、Python 3.10—3.15 matrix，以及 distribution/clean-source install/audit/MCP smoke。2026-10-14 的 Windows real-account Canvas-only、Ed-only、both course-catalog smoke 均为 `OK`，未保存 payload。同日，commit `c63e0a4` 在 arm64 owner Mac 上完成 59/59 stages（Python 3.10.19/3.15.0b3），包括 build/audit/clean-install/MCP protocol smoke 与 synthetic native Keychain cleanup；Codex MCP-host startup 和 host-side no-provider `server_status` 也为 `PASS`。macOS real-account calls、private legacy actual sync 与 publication 继续为 `NOT_RUN`，见 `docs/MACOS_AUTOMATED_RELEASE_TEST.md` 与 `docs/MACOS_MANUAL_RELEASE_TEST.md`。

不要用系统 `python3` 的失败代替项目结论：本机系统 Python 3.9 未安装 `httpx`，而仓库 `.venv` 测试全部通过。这也证明 clean-install 文档/CI 是必要工作。

## 拟议 CLI（PROPOSED，尚不可运行）

```text
canvas-ed-mcp setup
canvas-ed-mcp configure [--profile NAME] [--dry-run]
canvas-ed-mcp sync [--profile NAME] [--dry-run]
canvas-ed-mcp doctor [--profile NAME] [--show-effective-config]
canvas-ed-mcp serve [--profile NAME]
```

## CI matrix（REMOTE VERIFIED）

- OS：Ubuntu latest、Windows latest、macOS latest。
- Python：Windows/macOS 跑 3.10 与 3.15 边界；Ubuntu 另跑 3.11—3.14，覆盖完整声明范围。
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

Phase 5 local gate（2026-10-14）中，第 1—5 项均通过。第 6 项是唯一剩余人工 gate：当前 private development remote 可继续保存工作，但新的 public clean-history remote、visibility、tag、GitHub Release/PyPI destination 必须逐项确认。权威安全结论见 `docs/RELEASE_AUDIT.md`；archive 的 manifest 与 SHA-256 保存在 ignored `artifacts/phase5-rc1/source-final-a/`，不把自引用 digest 写入 archive 本身。
