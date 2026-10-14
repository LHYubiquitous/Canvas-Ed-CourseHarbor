# 开源与现成方案调研

> 检索日期：2026-10-13
> 外部检索只使用通用技术词和公开项目名；没有发送凭据、课程内容、私有日志或独特代码片段。

## 1. 结论先行

推荐 **compose**，不直接 adopt 某个完整项目：

- 保留本仓库 Canvas+Ed MCP 和已测试的同步安全语义。
- 从成熟 Canvas Downloader 借鉴 UX/state 设计，但不复制 GPL 代码。
- 复用标准 Python packaging、`platformdirs` 和 `keyring` 解决跨系统安装/目录/秘密问题。
- Canvas 继续使用 `httpx` 直接 adapter，或在 provider contract 稳定后评估 CanvasAPI；当前直接 API 已覆盖特殊端点且迁移成本较低。
- Ed 继续以隔离 adapter 维护；外部 Python wrapper 覆盖有限，`edstem-cli` 可作为行为/兼容性参考，但不作为运行时强依赖。

## 2. 候选比较矩阵

| Candidate | License / status (2026-10-13) | Capabilities | Fit | Gaps / risk | Decision |
|---|---|---|---|---|---|
| 当前 `canvas-ed-mcp` 上游 | MIT；公开仓库仍活跃 | 49 个 Canvas/Ed/Gradescope MCP tools；上游 README 已说明可配置 Canvas base URL | 最大化保留现有代码与历史 | 本地 personal sync 尚未成为通用 setup；Ed 非官方 | **Base** |
| BrkBuilds Canvas-Downloader | GPL-3.0；Windows/macOS desktop；大量测试 | 逐课选择、sync 不覆盖编辑、不恢复删除、keyring、课程内容与视频 | UX/state 需求高度相似，可验证需求不是孤例 | Canvas-only；GPL 与 MIT 代码整合有传播义务；macOS build 仅 Apple Silicon | **Reference only** |
| ChristopherCC-Liu canvas-downloader | MIT；较小型 Python toolkit | setup 验证 token、课程选择、modules/pages/announcements/assignments、pagination | 可借鉴零依赖 CLI 与完整性检查 | 无 Ed/MCP；维护/规模证据有限；同步语义不足 | Reference |
| `ucfopen/canvasapi` | MIT；成熟 Python wrapper，2026 有 release | Canvas objects、pagination、错误模型 | 可降低通用 Canvas API boilerplate | 引入映射迁移；对本项目特殊/新端点仍可能需要 raw requests | Evaluate later |
| `bunizao/edstem-cli` | MIT；CLI + MCP | Ed course/threads/lessons/files、配置与 MCP，现有文件保护 | Ed 行为和 CLI 设计很接近，可作 contract 参考 | Node runtime；不能直接复用 Python state/renderer；项目较新 | Reference / optional interoperability |
| `hschafer/edstem` | MIT；非官方 Python wrapper | 基础 Ed API object wrapper | 语言一致 | 公开功能覆盖/维护规模有限；不覆盖本项目 Lessons/Resources 全部路径 | Reject runtime dependency |
| `modelcontextprotocol/python-sdk` | MIT；官方 SDK；v2 stable，v1 维护线 | MCP server/client，stdio/HTTP，测试工具 | 必须继续使用官方 SDK | v2 对 v1 import/协议有 breaking changes | Keep v1 now, planned v2 migration |
| `keyring` | MIT；production/stable | macOS Keychain、Windows Credential Locker、Linux Secret Service/KWallet | 解决跨平台 token storage | Linux/headless backend 可能缺失；macOS Python executable access 模型需说明 | Adopt with backend checks |
| `platformdirs` | MIT；跨平台 | config/data/state/cache/user media paths | 直接解决路径硬编码 | desktop/user-media 在 Linux 可能不存在，仍需询问 | Adopt |

## 3. Adopt / Compose / Build

### A. Adopt 完整 Canvas downloader

优点：成熟 GUI、课程选择、自动同步和跨平台打包现成。缺点：没有 Ed/MCP；会丢失现有 HTML/文件夹规则；GPL 项目不能被当成 MIT 源码素材直接拷入。本项目目标不是 Canvas-only app，因此拒绝。

### B. Compose 现有核心 + 通用组件（推荐）

保留 sync algorithms 与 MCP tools；用 `platformdirs`/`keyring`/packaging 处理通用能力；以小型 provider interface 隔离 Canvas/Ed；从候选项目学习但不复制。它满足最多现有基线，长期维护面最小。

### C. Build from scratch

可以得到最干净模块，但会重新制造已解决的 edge cases，尤其是 user-edit ownership、source signature、atomic replace、HTML signed URL 和 Ed Lesson rendering。拒绝作为首选；只做渐进抽取。

## 4. 关键研究发现

1. Canvas 官方要求按 `Link` header 处理分页；当前代码已实现，不能回退为页数猜测。
2. Canvas 官方建议 Authorization header，不建议把 token 放 URL；当前代码符合，文档与新 adapter 必须继续保持。
3. Canvas 官方明确：手工 token 是 OAuth 未实现前的测试方式，多用户 app 必须 OAuth。公开版安装设计必须反映这一点。
4. OS keyring 是 native app 的官方建议方向；Python `keyring` 覆盖 macOS/Windows/Linux 主流 secret services，但必须检测后端。
5. 官方 MCP Python SDK 当前以 v2 为 stable，而本项目有意 pin `<2`；需要单独迁移，不应把依赖上限去掉后直接发布。
6. Ed API 缺乏稳定、完整的公开官方文档；公开资料和现有上游都将其描述为 beta/unofficial。这要求 contract tests、清晰错误和可禁用 provider。
7. 同类 Canvas Downloader 已实现“不覆盖本地编辑、不恢复用户删除”行为，支持将本项目这些规则提升为产品级 invariant。

## 5. 仍待核实

- 每个目标机构是否允许/愿意签发 Canvas OAuth Developer Key。
- Ed API token 页面、region/base URL 和端点在不同机构账户中的实际一致性。
- `gradescopeapi` 的长期兼容性和服务条款风险；它不是 Gradescope 官方 API。
- Windows/Linux 上可选 HTML→PDF 外部程序和打包策略。
- 当前上游分支的新改动是否应在开始重构前 merge/cherry-pick；本次没有修改 Git refs 或拉取远端代码。
