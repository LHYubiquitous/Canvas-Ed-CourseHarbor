# 调研来源

> 检索/复核日期：2026-10-14。只记录公开、无 token、无签名参数的 URL。

| ID | URL | Supports | Status |
|---|---|---|---|
| SRC-001 | https://github.com/r1ckyIn/canvas-ed-mcp | 当前公开上游、MIT、49 tools、Ed unofficial/beta 说明 | VERIFIED |
| SRC-002 | https://developerdocs.instructure.com/services/canvas/oauth2/file.oauth | Canvas token 安全、手工 token 限制、多用户 OAuth、native keychain 建议 | VERIFIED official |
| SRC-003 | https://developerdocs.instructure.com/services/canvas/basics/file.pagination | Canvas pagination 必须跟随 opaque Link header | VERIFIED official |
| SRC-004 | https://github.com/BrkBuilds/Canvas-Downloader | 同类完整产品的选课、sync、保护编辑/删除、keyring、Windows/macOS 能力 | VERIFIED repository |
| SRC-005 | https://github.com/ChristopherCC-Liu/canvas-downloader | MIT Canvas downloader、setup、modules/pages、pagination、完整性检查 | VERIFIED repository |
| SRC-006 | https://github.com/ucfopen/canvasapi | MIT Python Canvas API wrapper 与生态 | VERIFIED repository |
| SRC-007 | https://github.com/ucfopen/canvasapi/releases | 2026 release activity；具体版本应在实施时重查 | VERIFIED repository |
| SRC-008 | https://github.com/bunizao/edstem-cli | Ed CLI/MCP、course selection、lesson files 与 existing-file protection 参考 | VERIFIED repository |
| SRC-009 | https://github.com/hschafer/edstem | MIT、非官方 Python Ed wrapper | VERIFIED repository |
| SRC-010 | https://edstem.org/au/settings/api-tokens | Ed 澳洲区 token 入口；需要登录，页面路径由 owner device 确认 | OWNER-DEVICE CONFIRMED authenticated |
| SRC-011 | https://github.com/modelcontextprotocol/python-sdk | 官方 MCP Python SDK；v2 stable、v1 pin/migration 信息 | VERIFIED official repo |
| SRC-012 | https://github.com/jaraco/keyring | Python system keyring backends、限制和 MIT license | VERIFIED repository |
| SRC-013 | https://platformdirs.readthedocs.io/en/stable/ | 跨平台 config/state/user-media path | VERIFIED docs |
| SRC-014 | https://packaging.python.org/en/latest/specifications/entry-points/ | console/gui entry points 标准 | VERIFIED official docs |
| SRC-015 | https://pypi.org/project/gradescopeapi/ | 当前版本、Python compatibility、MIT license；非官方服务风险仍存在 | VERIFIED PyPI metadata |
| SRC-016 | https://github.com/encode/httpx | BSD-3-Clause HTTP client | VERIFIED repository |
| SRC-017 | https://github.com/pydantic/pydantic | MIT、Python compatibility | VERIFIED repository |
| SRC-018 | https://pypi.org/project/beautifulsoup4/ | MIT HTML parser metadata | VERIFIED PyPI metadata |
| SRC-019 | https://developerdocs.instructure.com/services/canvas/oauth2/file.oauth_endpoints | Canvas OAuth authorization/refresh/revocation endpoints、Authorization header、token lifetime | VERIFIED official |
| SRC-020 | https://developerdocs.instructure.com/services/canvas/oauth2/file.developer_keys | Canvas Developer Key scope 与管理员控制 | VERIFIED official |
| SRC-021 | https://www.epfl.ch/education/educational-initiatives/wp-content/uploads/2025/04/QUICKSTART_EdDiscussion_exercises_lectures.pdf | Ed API token settings path 与 beta/stability warning；institutional secondary evidence | VERIFIED secondary; Ed public API docs DEGRADED |
| SRC-022 | https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/migration.md | MCP Python SDK v1→v2 breaking changes 与依赖变化 | VERIFIED official |
| SRC-023 | https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/whats-new.md | `MCPServer` rename、2026-07-28 protocol 与 backward negotiation | VERIFIED official |
| SRC-024 | https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.2.0 | 当前 reviewed stable v2 release 与行为变化 | VERIFIED official release |
| SRC-025 | https://static.au.edusercontent.com/ | owner-device 真实 Ed Lesson 审核观察到的精确静态资源 host；仅记录 origin，不保留路径或签名 query。Ed 公开 API/asset 文档仍不可用 | OWNER-DEVICE OBSERVED; official documentation DEGRADED |
| SRC-026 | https://developerdocs.instructure.com/services/canvas/resources/modules | Canvas Modules 与 paginated module items；File/Page module item 类型和顺序 | VERIFIED official |
| SRC-027 | https://developerdocs.instructure.com/services/canvas/file.all_resources/files | Course Files list、File metadata、size/content-type/download URL | VERIFIED official |
| SRC-028 | https://developerdocs.instructure.com/services/canvas/resources/pages | Page stable locator、title/body 与 single-page lookup | VERIFIED official |
| SRC-029 | https://community.instructure.com/en/kb/articles/485223-canvas-domain-email-and-server-management | Instructure 官方列出的 Canvas browser-security/content domains，包括动态 `*.canvas-user-content.com` 与 Canvas images/file storage 使用的 `*.inscloudgate.net` | VERIFIED official resource document |
| SRC-030 | https://developerdocs.instructure.com/services/canvas/resources/assignments | Canvas course Assignments list、`due_at`、`points_possible`、quiz assignment indicator、`order_by=due_at` 与 user-applicable assignment overrides | VERIFIED official |

动态版本、release、平台支持和政策在真正发布前必须重新检查，不能把本表当作永久事实。
