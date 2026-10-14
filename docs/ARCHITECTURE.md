# 可迁移版架构

> 状态：PHASE 0—3 MOCK_VERIFIED ON WINDOWS / P4 AUTOMATED WORK COMPLETE / MANUAL INTEGRATION REQUIRED
> 现有行为基线见 `CURRENT_BASELINE.md`。

## 1. 推荐路线

采用 **compose + preserve**：保留现有下载/去重/保护用户编辑算法，把它包在可测试的 domain 层；复用 Python packaging、`platformdirs`、`keyring`、官方 MCP SDK 与 `httpx`，新增 provider adapter、配置向导、policy 和版本化 state。不要直接换成另一个 Canvas downloader，也不要一次性重写 5,000 行 MCP server。

## 2. 系统边界

```text
User
  │  setup/configure/sync/doctor (CLI; interactive + non-interactive)
  ▼
Config + confirmation ───────────────┐
  │                                  │
  ├─ secret references ──> OS keyring│
  ├─ course mappings                 │
  └─ download policies               │
                                     ▼
Sync orchestrator ──────> versioned state store
  │                         │ source signatures
  │                         │ managed hashes
  │                         │ tombstones/skips/failures
  ├─ Canvas adapter ───> Canvas instance API
  └─ Ed adapter ───────> Ed API (beta/unofficial)
          │
          ▼
Policy filter -> renderer/downloader -> safe installer -> user course folders

MCP server reuses the same config/providers but is not the owner of sync state.
```

## 3. 建议模块与职责

| Proposed component | Responsibility | Input / Output | Validation |
|---|---|---|---|
| `src/canvas_ed_mcp/config.py` | Pydantic 配置 schema、版本迁移、路径解析 | TOML ↔ typed config | schema/unit/golden files |
| `src/canvas_ed_mcp/secrets.py` | keyring/env credential references；禁止日志回显 | secret key name → token | fake keyring + redaction tests |
| `src/canvas_ed_mcp/cli.py` | `setup/configure/sync/doctor/mcp` 命令 | argv/TTY → plan/result | CLI tests, cancel tests |
| `src/canvas_ed_mcp/setup_flow.py` | 平台选择、API 验证、逐课确认、preview | provider catalogs → config plan | mock catalogs + snapshot tests |
| `src/canvas_ed_mcp/providers/base.py` | 最小 provider protocol | list courses/content/download | contract tests |
| `providers/canvas.py` | 可配置 Canvas base URL、pagination、content mapping | Canvas API ↔ domain records | official API fixtures/integration |
| `providers/ed.py` | Ed course/lesson/resource adapter | Ed API ↔ domain records | captured synthetic fixtures + smoke |
| `sync/model.py` | `SourceKey`, `RemoteItem`, `LocalOwnership`, decisions | stable typed records | pure unit tests |
| `sync/state.py` | schema v4、scope、迁移、原子保存 | state JSON/SQLite | migration/crash tests |
| `sync/policy.py` | 类型、MIME、扩展名、大小和 content category | RemoteItem → allow/skip reason | table-driven tests |
| `sync/engine.py` | 现有基线/签名/去重/编辑保护决策 | remote + state + index → plan | characterization tests |
| `sync/storage.py` | course-local 扫描、hash observation、路径边界、same-directory atomic no-clobber publish | plan → local files | filesystem/race/failure injection |
| `renderers/html.py` | 自包含 HTML、图片 data URI、URL 去敏 | page/lesson → HTML | current HTML tests |
| `server.py` | 根据配置注册/守卫 MCP tools | MCP calls → provider | in-memory MCP client |

文件名为设计建议，第一阶段可以先抽取最小模块；禁止只为“结构漂亮”创建空包。

P3-01 implementation note：`CourseCatalogProvider` 与新的 `CourseContentProvider` 分离，避免强迫 Canvas discovery 提前实现 content API。`EdProvider` 只公开 metadata-only `RemoteItem`、context-managed resource byte iterator 与 pure `RenderedHtml`；每次 fetch/render 必须匹配当前 discovery record。Lesson raw snapshot 只在内存中存在并与 semantic signature 绑定。API client 与 asset client 分离；asset URL 每跳要求 HTTPS、显式 trusted host 且拒绝 private literal IP，injected asset client 不得带 auth/cookies/credential headers/event hooks。signed/expiry query 不参与 Lesson revision，但普通 query 仍保留语义。

P3-04 implementation note：新的 package `server.py` 与 legacy 单文件 server 完全分离，只注册当前 provider contract 能真实支持的 read-only tools。工具目录由 validated config 的 enabled provider set 决定；secret resolution 和 provider construction 均按 provider lazy 执行。catalog 只返回与 confirmed `CourseSource(provider, instance, course_id)` 精确匹配的课程，course-scoped 调用先按 logical key guard，再触碰凭据或网络。stdout 保留给 stdio MCP protocol；errors 只返回稳定 category，不回显不可信输入、credential reference、URL 或 raw payload。

P3-05 implementation note：Gradescope 继续是 sync/mapping domain 外的独立 optional provider；schema v1 新增 default-off platform section，但不把它加入 `CourseSource`。package server 仅在显式启用时注册两个 read-only tools，dependency/secret/login 均 lazy 且失败隔离。assignment lookup 只接受当前 catalog 中出现过的 exact course ID；third-party objects 经固定字段投影。同步 provider I/O 使用 worker thread，runtime lock 将每个 provider 的 lazy init 与调用串行化，慢 Gradescope 不阻塞 MCP event loop 或 Canvas/Ed/status。

P3-06 verification note：`mcp>=1.29,<2` 是当前 protocol-test floor。测试通过 SDK connected-memory transport 执行真实 initialize/list/call/lifespan，通过 Windows subprocess stdio 执行 package CLI initialize/list/status。stdio smoke 使用 enabled-but-empty synthetic provider，证明 tool discovery/status 不解析 credential、不构造 client、不访问网络；config bytes 与 output path 保持不变。

P3-02 implementation note：content discovery 返回 immutable `ContentBatch`，逐 kind 标出 `COMPLETE / PARTIAL / FAILED` coverage，并只携带 allowlisted、脱敏的 category/item issue fields。401 是全局 credential failure；403、单个 detail failure、malformed entry 和 duplicate stable ID 在可判定 scope 内隔离。所有 cache update 先 staging，fatal 时零变化，partial 时只发布本次成功 items；failed category 不混入旧 items。未知但有可显示内容的 slide 使用 generic safe renderer 并报告 `generic_slide_rendered`，无可显示内容则使用 self-contained placeholder 并报告 `unsupported_slide_kind`。`tests/fixtures/ed/v1/` 是 fixture format version，不代表上游 Ed API version，也不构成真实 beta schema integration evidence。

## 4. 配置模型（proposed）

非秘密配置放在平台惯例 config directory；secret 只保存 keyring reference。示意：

```toml
schema_version = 1
profile = "default"
output_root = "/user-confirmed/path"

[platforms.canvas]
enabled = true
base_url = "https://canvas.example.edu"
credential = "keyring:canvas-ed-mcp/default/canvas"

[platforms.ed]
enabled = false
region = "global"

[[courses]]
key = "canvas:canvas.example.edu:12345"
folder = "COURSE1001"
confirmed_at = "2026-10-13T00:00:00+11:00"

[courses.canvas]
id = "12345"
code = "COURSE1001"
term = "2026 Semester 2"

[courses.content]
files = true
pages = true
lessons = false

[courses.policy]
max_file_bytes = 104857600
exclude_extensions = [".mp4", ".mov"]
```

规则：

- `enabled=false` 的 provider 不读取 secret、不发请求、不注册为同步失败。
- 课程必须包含 provider instance identity + stable course ID；code/name 仅用于显示和默认 folder。
- 每门课可来自 Canvas、Ed 或二者映射；两边 ID 必须分别确认，不凭同名自动合并。
- config 写入前显示完整 diff；确认动作也写入可审计的非敏感 metadata。
- 文件大小/类型 policy 有 global default 和 course override；最终有效配置可由 `doctor --show-effective-config` 脱敏显示。

## 5. Setup / reconfigure 流程

1. 选择 `canvas` / `ed` / `both`，默认不预选。
2. 对每个启用 provider 输入 instance/region 和 credential；立即做最小只读身份校验。
3. 拉取课程列表，显示平台、课程名、code、term 和不可混淆的 ID。
4. 逐项 yes/no；不能“一键默认全选”。可提供显式 `select all`，但仍需最终汇总确认。
5. 对入选课程配置本地 folder、内容类别、扩展名/MIME、大小限制和初次 baseline 模式。
6. 校验路径边界、重复目录、ID 冲突、磁盘权限；生成 dry-run plan。
7. 用户确认后原子写 config；再单独初始化 baseline。取消时零写入。
8. `configure` 重走相同流程，先展示 add/remove/change；remove 只停止同步，不删除文件/state。

## 6. State 与同步决策

### Source identity

`provider + normalized_instance + platform_course_id + kind + remote_id`。这避免不同学校、学期或平台间碰撞。

### 每个 source entry 至少记录

- remote signature 和最小非敏感 metadata；
- `first_seen_at`、`last_seen_at`、`last_changed_at`；
- last action：`baselined/downloaded/replaced/preserved_user_edit/skipped_policy/failed`；
- sync-managed local relative path 和写入时 hash；
- user-deleted/tombstone 语义或由“路径缺失+已知 source”推导；
- retry flag 和脱敏失败分类。

### 决策表

| Remote | State | Local | Result |
|---|---|---|---|
| baseline init | absent | any | 只记 signature，不下载 |
| unchanged | known | present/missing | 不下载（missing 即当前“不 backdate”） |
| changed | managed hash unchanged | present | 原位原子替换 |
| changed | hash differs | present | 保留用户文件，新版另存 |
| changed | known but missing | missing | 默认下载新版；策略可改为永久 tombstone |
| new | no state | duplicate content exists | 不重复落盘，也不认领用户文件 |
| new/changed | denied by policy | any | 记 skip reason，之后 policy 变化可重新评估 |

P2-02 implementation safety note：跨平台 Python stdlib 没有基于旧内容 hash 的 atomic compare-and-swap rename；portable storage 因此只暴露 hard-link create-if-absent publication。`decide_install(..., allow_in_place_replace=False)` 会把原本可替换的 unmodified managed file 显式计划为 `INSTALL_SEPARATELY / SAFE_REPLACE_UNAVAILABLE`。新 engine 接线时据此写入带版本的 sibling 并保留旧文件；不得把 decision 静默执行成另一种 action，也不得用 `exists/hash check -> os.replace`。legacy 同步器的原位替换暂时保持不变并由原 characterization tests 约束。

P2-03 policy note：`evaluate_item_policy` 先处理四类 content selection，再只对 file/resource 应用 extension、MIME 和 known-size preflight；exclude 优先于 include。未知大小响应必须在每个 chunk 写入 staging 前调用 `evaluate_stream_size`，超限返回稳定的 `stream_size_limit_exceeded`；future engine 负责立即终止流并清理 staging，当前 legacy downloader 尚未接线。

state 放在 platform-specific state directory，而非 repo；写入必须 temp + fsync/replace（按平台能力），并保留最近一次可读备份。移动 output root 不应只改字符串；提供显式 migration/reattach 流程。

P2-04 implementation note：state v4 文件内部再次绑定 `profile` 与 canonical absolute `output_root`，避免复制/误指向后跨 namespace 认领文件。正常保存只在旧 primary 可解析且 namespace 匹配时生成 previous-readable backup，再原子替换 primary；corrupt primary 不会静默 fallback。v3 migration 只选择显式目标 output scope，把 legacy 拼接 key 转为 structured `SourceKey`，仅保留带有效 hash 且位于 output root 内的 managed ownership，并以 hard-link create-if-absent 发布新 v4 state；legacy 文件保持只读。P2-06 course/profile lock 完成前，普通 v4 update 仍不宣称具备 multi-process serialization。

P2-05 implementation note：`build_configure_plan` 对同一 profile/output namespace 生成 deterministic course add/remove/change records；新增课程必须逐项给出 `baseline_from_now` 或 `import_all`。无 operational change 时保留原 `confirmed_at`；remove 只从新 config 移除，不触碰 output/state。preview 不显示 credential reference，confirmed commit 只原子写 config，并在磁盘 config 与 preview 基线不一致时拒绝提交。interactive CLI 尚未接线。

P2-06 implementation note：lock file 位于所有 profiles 共享的 application state lock directory，但文件是否存在不代表占锁；互斥由 Windows `msvcrt.locking` / POSIX `flock` 的 OS-held lock 决定，process exit 会由 OS 释放。course lock digest 使用 canonical output root + physical course root，避免不同 profile/key 指向同一目录；state lock 使用 profile + output root。portable publication、state save 与 v3 migration 都要求 matching held lock。当前仅 Windows 分支实测；POSIX 为 `NOT_RUN`。

## 7. 安全边界

- **Canvas personal/dev mode**：可支持个人手工 token，但安装文档必须显示官方 policy 警告。
- **公开多用户模式**：Canvas 使用 OAuth2；需要每个机构管理员签发 Developer Key。无法获得时不得声称正式合规部署。
- token 不出现在 argv、URL query、日志、state、diagnostic bundle 或 MCP tool result。
- keyring 运行前检查真实 backend；Null/明文 fallback 不可静默启用。Linux 无 Secret Service 时要求 env 注入或用户显式选择受限文件 fallback。
- 所有下载目标都经过 resolved-path containment；文件名清洗；archive 解包另设 traversal、总大小和压缩比限制。
- MCP 写操作和 Gradescope 默认关闭；启用时明确显示权限/风险。

## 8. 跨系统策略

| Concern | Windows | macOS | Linux |
|---|---|---|---|
| 安装 | `py -m venv` / `pipx` | `python3 -m venv` / `pipx` | distro Python + venv/pipx |
| 主入口 | console script，不依赖 shell | console script；`.command` 仅 convenience | console script |
| secrets | Credential Locker | Keychain | Secret Service/KWallet；headless 用 env |
| config/state | `platformdirs` AppData | Application Support | XDG config/state |
| desktop default | `platformdirs` 可发现时使用 | 同左 | XDG desktop；不可用时询问 |
| optional renderers | 自动探测且可禁用 | 自动探测且可禁用 | 自动探测且可禁用 |

## 9. 迁移顺序

1. 锁定 characterization tests，不移动逻辑。
2. 引入 typed config/IDs/policy，但保留 legacy CLI compatibility adapter。
3. 把 state 从 v3 迁移到新 store；原文件只读备份，迁移失败不运行同步。
4. 抽取 Canvas/Ed provider，先 Canvas vertical slice，再 Ed。
5. MCP server 改为复用 provider/config；随后单独评估 MCP SDK v2。
6. 分离 legacy scripts/data，完成 packaging 和三系统 CI。

回滚原则：每个 phase 都保持旧入口可运行；新 config/state 未通过验证时不接管用户现有资料。
