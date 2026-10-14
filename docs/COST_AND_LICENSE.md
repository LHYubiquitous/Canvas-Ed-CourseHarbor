# 成本与许可证审计

> 状态：发布前初审；不是法律意见。

## 默认费用策略

- 软件本体、本地同步和本地 MCP 不引入必需付费 API。
- 不自动购买、启用云端转录、对象存储或后台主机。
- 学校 Canvas/Ed/Gradescope 访问资格由用户所属机构决定；项目开源不代表平台服务免费或永久可用。

## 当前/拟用依赖

| Dependency/service | License / cost | Use | Action before release |
|---|---|---|---|
| 本项目上游 | MIT | 核心 MCP | 保留原 LICENSE/版权和 fork 贡献说明 |
| MCP Python SDK | MIT | MCP protocol | 当前 pin v1；升级 v2 独立验证 |
| httpx | BSD-3-Clause | HTTP | 在 lock/notice 中记录 |
| Pydantic | MIT | schema | 在 lock/notice 中记录 |
| Beautiful Soup 4 | MIT | HTML parse/render | 在 lock/notice 中记录 |
| gradescopeapi | MIT；服务非官方 | Gradescope optional | 默认 opt-in；说明 scraping/ToS/凭据风险 |
| keyring (proposed) | MIT | OS secrets | 检查 transitive/backend licenses，测试 Null backend |
| platformdirs (proposed) | MIT | config/state paths | 采用 |
| CanvasAPI (optional) | MIT | Canvas wrapper | 仅在能减少维护且 contract tests 通过时采用 |
| BrkBuilds Canvas-Downloader | GPL-3.0 | 仅研究/UX 参考 | 不复制/链接其源码进 MIT package |
| Pandoc / Poppler / Ghostscript / browsers | 各自许可证；本地外部程序 | optional exports | 不捆绑到 MVP；检测并提示，分发前逐项复核 |
| Canvas/Ed/Gradescope services | 机构账户/政策决定 | remote APIs | 无规避授权；文档注明限制 |

## 发布前知识产权 Gate

1. 明确 fork 基于原 MIT 项目，保留原 LICENSE，不把“MIT”误写成对课程内容再分发的许可。
2. 用户下载的课程材料不进入源码包、release asset、测试 fixture；测试只用合成数据。
3. 课程专用脚本若保留在 `legacy/`，先人工确认其中没有课程内容、账户标识、学校私有 URL 或第三方受限素材。
4. 生成 `THIRD_PARTY_NOTICES.md` 和锁定依赖清单；对可选二进制不打包即不宣称已获得再分发许可。
5. 运行秘密、绝对路径、个人信息、签名 URL 和 archive manifest 扫描，再人工检查 Git history；扫描通过不等于已获发布授权。
6. 用户尚未授权本次对外 push/public release；当前仅创建本地计划与脱敏快照。

## 隐性成本

- OAuth：可能需要学校管理员审批 Developer Key，主要是协调成本。
- Code signing：Windows/macOS 安装器若追求无警告，可能产生证书/开发者账号费用；source/pipx MVP 不需要。
- CI：公开 GitHub 项目的标准 CI 通常可免费使用，但额度/政策需发布时复核。
- 支持：Ed beta API 和多校 Canvas 差异会产生持续维护成本。
