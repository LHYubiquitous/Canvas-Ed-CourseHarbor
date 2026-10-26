# 当前实现与不可回归基线

> 审计日期：2026-10-18
> 状态：代码现状审计；本文件中的“已实现”仅指本地源码和测试已核实，不代表跨平台或真实账户集成均已验证。

## 1. 当前项目包含什么

### MCP 服务（已实现）

- `canvas_ed_mcp.py`：单文件 Python stdio MCP server，共注册 **49 个工具**。
- Canvas：课程、公告、作业、成绩、文件、页面、模块、日历、syllabus、讨论、提交状态、本人提交与反馈、peer review、下载、提交作业和讨论发帖等。
- Ed：课程、threads、搜索、Lessons、Resources、资源下载、发帖/编辑/回复/采纳、Workspace CRUD。
- Gradescope：课程和作业只读查询；依赖非官方网页客户端。
- 跨平台：Unit Outline 与 Canvas assessment coverage 对账；其中 Unit Outline 解析目前是学校特定功能。

### 课程资料增量同步（已实现）

`sync_courses.py` 已具备以下行为，实施可迁移性改造时不得静默破坏：

1. **安全基线**：首次必须执行 `--initialize-baseline`，只记录远端现状，不补拉历史内容。
2. **不 backdate**：已进入 state 的远端项目，如果本地后来被用户删除且远端未变化，不会被重新下载。
3. **仅处理新增/变化**：来源签名不变时跳过；Canvas 下载 URL/签名参数轮换不算内容更新。
4. **保护用户编辑**：只有本地文件仍匹配同步器最后写入的 SHA-256 时才原位替换；用户改过的旧文件保留，新版本另存为带 `updated YYYY-MM-DD` 的文件。
5. **不认领用户文件**：内容与待下载文件相同的既有文件可用于去重，但不会被标成 sync-managed，后续不得覆盖。
6. **课程范围去重**：先用规范化文件名+大小索引，再以内容哈希确认；相同内容不会重复落盘。
7. **保留现有目录结构**：优先匹配现有 module/category 文件夹；无法匹配才写入 `_New Course Updates/<group>`。
8. **Canvas Page 自包含**：图片转 data URI 内嵌，不创建单独 assets 文件夹；去掉 `srcset`/`picture source` 的额外网络引用。
9. **不重复下载大附件到 HTML**：HTML 内的 PDF、视频、音频等保留去敏后的 Canvas 链接，由课程文件同步另行处理。
10. **无 raw JSON 导出**：课程资料目录不保存逐项 API JSON。
11. **下载完整性**：流式下载有连接/读取/总时长限制，声明大小不匹配时拒绝安装临时文件；最终替换采用同目录 staging + 原子 rename。
12. **错误隔离**：单个 Ed Resource 失败不会中断同课后续资源；warning/error 会汇总输出。
13. **输出目录隔离**：state v3 按绝对 output root 分 scope；旧 v2 state 会迁移，但旧路径不会被自动认定为 sync-managed。
14. **签名 URL 防泄露**：错误信息和 HTML 链接会移除 query/fragment；下载媒体时不向非 Canvas API 主机发送 Bearer token。

### 辅助工具（已实现但多为个人/平台特定）

- `setup_tokens.py`：隐藏输入，将 token 写入 mode `0600` 的本地 JSON；目前要求 Canvas+Ed，只有 `--ed-only` 例外。
- `run_mcp.sh`、`run_course_sync.command`：本地启动入口；后者相对路径可迁移，`Course Material Sync.command` 含绝对个人路径，不可开源。
- `verify_mcp.py`：语法、依赖、stdio 握手、工具列表及可选只读 API 检查。
- `export_ed_lessons*.py`、`organize_ed_lessons.py`：Ed Lesson 的 Markdown/HTML/PDF 导出；部分外部程序路径和课程数量写死。
- 课程专用导出、排序和词表脚本：属于 legacy/personal 工具，必须与通用发行包隔离，不能被误认为通用 API。

## 2. 现有验证证据

2026-10-13 在仓库现有 `.venv` 中执行：

```text
./.venv/bin/python -B -m unittest discover -s tests -v
Ran 19 tests ... OK

./.venv/bin/python -B -m py_compile <8 个核心/导出脚本>
dependency imports: OK
```

上述 19 个测试覆盖：HTML 图片内嵌、非图片不内嵌、失败重试占位、用户编辑保护、原位更新、截断/超时下载拒绝、用户文件不认领、Ed 单项失败隔离、state output-root 隔离、签名 URL 去敏等。

Phase 0 在 2026-10-13 又增加 13 个 synthetic/mock tests；当前 Windows 完整套件为 **32 passed**。新增证据覆盖 baseline、用户删除后远端不变/后来变化、URL 轮换、目录复用/fallback、无 raw JSON、原子替换失败、跨文件名去重、错误脱敏，以及 Canvas base URL/Unit Outline SSRF allowlist。完整映射见 `CHARACTERIZATION_MATRIX.md`。

## Portable 0.2 status

The current private tree targets `0.2.0rc19`, a focused Courses-history responsiveness
successor; public `v0.2.0rc18` remains immutable.
The published `v0.1.0rc1` remains a separate immutable artifact. rc10 product behavior
completed the Windows/macOS owner review and three-system private CI. rc14 added
non-destructive graphical reconfiguration; rc15 added the macOS button-contrast fix.
The final rc15 macOS gate failed because course/source labels exposed internal keys
and the delivered runner was not executable as written. rc16 carries human-readable
course metadata through the reviewed-sync summary and rebuilds the runner. rc18 added
the audited arm64 macOS DMG. rc19 refreshes the Qt accessibility cell cache after a
Courses branch collapses so Windows UI Automation cannot navigate stale sibling indexes.

Portable `canvas-ed-mcp sync` now composes the same protected decision/storage/state
primitives for explicitly configured Canvas and Ed sources. Canvas discovery includes
module-linked Pages/Files in module order plus remaining course Files; the first module
reference owns grouping, while unlinked Pages are deliberately not enumerated. Pages
use the self-contained safe HTML renderer. Canvas API Bearer credentials stay on the
configured origin, and a separate credential-free client defaults to the exact Canvas
hostname allowlist. Reviewed Canvas image redirects may follow only the ordered exact
origin → `*.canvas-user-content.com` → `*.inscloudgate.net` chain, with one internal
inscloudgate delegation before exact-origin pinning; other DNS/CDN hosts fail closed.
Synthetic status is 332 tests passing with one expected Windows symlink-privilege
skip. Real Canvas content is `HUMAN_VERIFIED_WITH_WARNINGS`: isolated
import, embedded-image and HTML safety audits, idempotency, secret scan, plus desktop
and narrow Chromium rendering passed; course-wide Files remains partial after a
confirmed 403; the owner accepted the visible images and desktop/narrow layouts.

P6-03 adds create-only interactive setup for new Canvas-only, Ed-only, and explicit
Canvas+Ed profiles plus retain-by-default interactive configuration of existing
profiles. Disabled or unselected providers perform no secret lookup or request;
source mapping, detachment and initial mode remain explicit. Detachment preserves
local output and historical state. P6-03b is synthetic-complete. Windows owner-device
configure is `PASS` for real Ed-side, Canvas-side and mixed-provider discovery. The
isolated Ed copy additionally passed config-only commit and byte-exact rollback;
Canvas-only and mixed-provider no-op dry-runs exited 0 without content/state writes.

Phase 1 与 P2-01 将 Windows synthetic/mock 完整套件扩展到 **62 passed**：新增 portable config/secrets/setup/provider tests，以及 14 个 sync-domain tests。P2-01 已证明两阶段 decision table 可表达 baseline、no-backdate、remote change、retry、managed replacement、user-edit preservation、de-duplication 和 unsafe-path blocking。

P2-02 将完整套件扩展到 **83 passed, 1 skipped**：新增 safe-replace capability decision、15 个 storage tests 与 5 个 pure-renderer tests，覆盖 course-local hash/index、portable containment、Windows reparse flag、atomic no-clobber publish race/failure/cleanup、crash-leftover isolation，以及 HTML image embedding、active-content removal 和通用 URL surface 去敏。skip 是当前 Windows 账户不能创建真实 symlink；对应 synthetic reparse-point test 已通过。portable primitives 尚未替换 legacy I/O engine，且为避免并发用户编辑丢失，不暴露 hash-check 后原位覆盖 API。

P2-03 将完整套件扩展到 **96 tests OK, 1 skipped**：新增 13 个 pure policy tests，覆盖 files/pages/lessons/resources category、case-insensitive 与 compound extension、exact/wildcard MIME、exclude precedence、known-size preflight、unknown-size chunk guard，以及稳定 skip reason。默认 100 MiB 和 `.mp4`/`.mov` exclusion 已冻结；policy 尚未接入 portable engine 或 legacy downloader。

P2-04 将完整套件扩展到 **109 tests OK, 1 skipped**：新增 13 个 state tests，覆盖 deterministic v4 round-trip、profile/output namespace mismatch、previous-readable backup、primary/backup crash injection、corrupt state fail-closed、single-scope v3 migration、managed ownership containment 和 concurrent v4 destination preservation。legacy v3 文件不会被修改；state v4 尚未接入 portable engine/CLI，普通更新的 multi-process serialization 留给 P2-06。

P2-05 将完整套件扩展到 **118 tests OK, 1 skipped**：新增 9 个 configure tests，覆盖 deterministic add/remove/change diff、逐新增课程 `baseline_from_now/import_all`、credential-safe preview、no-op confirmation metadata preservation、remove 不删除 output/state、cancel zero-write 和 post-preview config conflict。interactive `configure` CLI 尚未接线。

P2-06 将完整套件扩展到 **128 tests OK, 1 skipped**：新增 8 个 Windows OS-lock tests，并扩展 state/storage/path tests，覆盖 same-course contention、different-course independence、bounded timeout、exception release、subprocess crash residue、shared application lock path，以及 file publication/state save/v3 migration 的 mandatory matching-lock guard。course lock 以 physical output folder 为 scope，跨 profile 保护同一目录；POSIX lock branch 仍为 `NOT_RUN`。

P3-01 将完整套件扩展到 **147 tests OK, 1 skipped**：新增 19 个 Ed provider tests，将 course/Lesson/Resource 映射到 stable provider/domain identity，并覆盖 discovery-bound resource streaming、redirect auth isolation、forged/stale item preflight、schema fail-closed、trusted asset-host/SSRF boundary、signed URL redaction/revision semantics 与 self-contained Lesson HTML。该 adapter 尚未接 setup/portable engine/MCP；真实 Ed beta schema/account 和 macOS/Linux 均为 `NOT_RUN`。

没有在本次审计中调用真实 Canvas/Ed/Gradescope 账户；因此状态是 `MOCK_VERIFIED`，不是 `INTEGRATION_VERIFIED`。

## 3. 已发现的可迁移性缺口

| 缺口 | 当前证据 | 影响 |
|---|---|---|
| 课程写死 | `sync_courses.py::COURSES` | 其他学期/用户无法安全选择课程 |
| Canvas 域名写死 | `canvas_ed_mcp.py`、`sync_courses.py` | 只能直接适配单一学校实例 |
| 双平台强耦合 | `Api` 和 `load_credentials()` 同时要求两个 token | Canvas-only 不可用；Ed-only 只在部分辅助工具存在 |
| 课程按 code 猜测 | `find_*_course()` 选择最新匹配 | 同 code 多学期可能选错；没有逐平台确认 |
| state/credentials 位于 repo | hidden JSON 在项目根目录 | 升级、复制、开源、多个 profile 容易混淆 |
| 明文 token 文件 | mode 600 仅 Unix 有意义 | Windows 语义不同；不如 OS keyring；MCP host config 也可能泄露 |
| 没有下载 policy | 没有通用类型/大小过滤 | 大视频等只能下载后失败或占空间 |
| launcher 不统一 | `.command`/shell/`.venv/bin/python` | Windows 不可用，Linux 桌面环境差异未覆盖 |
| 个人脚本混在根目录 | 多个课程名/绝对路径/固定数量 | 开源包包含噪声并可能泄露个人学习结构 |
| 依赖不完全锁定 | `requirements.txt` 仅宽范围 | 不同时间部署可能得到不兼容组合 |
| MCP SDK v1 边界 | `mcp>=1,<2` 且使用 `FastMCP` | 当前可运行，但官方 v2 已为稳定线，需独立迁移决策 |
| 真实集成测试缺失 | 仅 mock tests | API 端点、权限、Ed beta 漂移仍未知 |

## 4. 基线变更规则

- 任何改造先把本文件第 1 节转成 characterization tests，再拆分模块。
- state schema 必须版本化且提供迁移；无法证明安全时默认“不下载/不覆盖”。
- 配置变更不得隐式重建 baseline、隐式选课或隐式启用平台。
- 删除配置中的课程只停止未来同步，不删除本地资料；重新加入时必须再次确认 baseline 策略。
- 任何真实写入 API 的 MCP tool 保持显式、最小权限，并与只读课程镜像分离。
- HTML 图片内嵌、自包含输出、用户编辑保护和“不回填已删除且未更新内容”属于 MUST 回归门。
