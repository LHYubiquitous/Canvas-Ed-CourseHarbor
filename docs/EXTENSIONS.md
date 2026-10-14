# Future Extension Registry

> 下列均为 RESERVED，不是已实现承诺。

| ID | Extension | Priority | Proposed boundary | Entry condition |
|---|---|---:|---|---|
| EXT-001 | Canvas OAuth callback/setup helper | High | `auth/canvas_oauth.py` | 有机构 Developer Key 和 policy review |
| EXT-002 | GUI installer/course picker | Later | UI 调用同一个 setup plan API | CLI/config contract 稳定且三系统通过 |
| EXT-003 | Background scheduler | Later | OS scheduler adapter，不做常驻云端 | sync 幂等/锁/恢复成熟，用户显式 opt-in |
| EXT-004 | Permanent tombstone policy | Medium | `sync.policy.deleted_remote_update` | 用户确认远端更新后的删除语义 |
| EXT-005 | Provider plugin API | Later | entry-point based provider registry | 至少第三个真实 provider 需求出现 |
| EXT-006 | Panopto/lecture media | Later | optional media provider + strict size/rights gate | 授权、磁盘、依赖和许可方案清楚 |
| EXT-007 | Content conversion (PDF/Markdown) | Later | renderer plugin | 外部 binary 探测/许可/跨平台 CI 完成 |
| EXT-008 | Encrypted portable secret bundle | Later | explicit opt-in secret backend | OS keyring 无法满足的真实需求出现 |
| EXT-009 | Remote HTTP MCP | Later | separate authenticated server package | local stdio stable；完成 threat model |
| EXT-010 | Provider health/status telemetry | Later | local-only redacted diagnostics | 明确 opt-in、无课程内容/PII 外传 |

扩展必须复用同一 typed config、course identity、policy 和 state decision model；不得绕过 per-course confirmation、secret boundary 或 user-file protection。
