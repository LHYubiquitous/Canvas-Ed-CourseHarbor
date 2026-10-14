# Canvas Ed MCP

[![Python 3.10–3.15](https://img.shields.io/badge/Python-3.10%E2%80%933.15-3776AB)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Portable, local-first, read-only MCP access to explicitly configured Canvas and Ed
courses. Version `0.1.0rc1` is a release candidate: it is suitable for reviewed local
testing, but it has not been published to PyPI or released from a public repository.

## Release-candidate scope

The installed package provides:

- profile-isolated configuration, state, cache, and log paths through `platformdirs`;
- OS-keyring references by default, with process-scoped environment references for
  reviewed headless use;
- configurable HTTPS Canvas instances with same-origin pagination guards;
- an isolated Ed adapter with schema-drift handling and credential-safe asset fetches;
- explicit Canvas↔Ed source mapping—matching names never auto-select a course;
- a provider-aware read-only MCP server whose tool surface follows enabled providers;
- optional, disabled-by-default Gradescope read-only tools;
- no-clobber storage, state-v4, policy, locking, and reconfiguration primitives.

The portable sync orchestrator and interactive Ed/both setup are not connected yet.
`course-sync` is therefore a safe reserved command and does not download anything.
The historical 49-tool/write-capable server and personal sync scripts are retained
only in the private development repository; they are not part of the public source
candidate, wheel, or sdist.

## Install from a reviewed checkout or wheel

Use an isolated environment and do not install as administrator/root:

```text
python -m venv .venv
python -m pip install .
canvas-ed-mcp --version
canvas-ed-mcp doctor --show-paths
```

See [Cross-platform installation](docs/INSTALLATION.md) for Windows, macOS, Linux,
MCP-host, update, and rollback guidance. See [Authentication safety](docs/AUTHENTICATION.md)
before creating or storing a Canvas or Ed token.

## Configure safely

Run setup for a reviewed Canvas profile:

```text
canvas-ed-mcp setup --profile default --dry-run
canvas-ed-mcp setup --profile default
```

Setup is no-by-default: each platform and course must be explicitly selected and a
final preview confirmed. Configuration stores credential references such as
`keyring:canvas-ed-mcp/default/canvas`, never credential values.

The portable server is launched with:

```text
canvas-ed-mcp serve --profile default
```

Its MCP tools are limited to the configured read-only surface:

- `server_status`
- `canvas_list_configured_courses` when Canvas is enabled
- `ed_list_configured_courses` and `ed_list_course_content` when Ed is enabled
- `gradescope_list_courses` and `gradescope_list_assignments` only after explicit
  Gradescope opt-in and optional dependency installation

Disabled providers resolve no credential and make no network request.

## Security and privacy

- Never place API tokens, passwords, session cookies, signed URLs, or course material
  in source control, MCP JSON, screenshots, issues, or support logs.
- Treat personal Canvas/Ed tokens as password-equivalent and revoke them remotely
  before deleting their local keyring entries.
- Course access does not grant redistribution rights. Real course content is excluded
  from every distribution and test fixture.
- The current private development repository contains legacy compatibility history and
  must not be made public. A future public repository must start from the audited,
  clean-history source archive.
- Report vulnerabilities using the process in [SECURITY.md](SECURITY.md).

## Verification and release materials

The project runs synthetic tests across Windows, macOS, and Linux for Python
3.10–3.15. The release candidate also includes:

- `uv.lock` and the standard `pylock.toml` dependency lock;
- `sbom.cdx.json`, a CycloneDX 1.5 software bill of materials;
- [third-party notices](THIRD_PARTY_NOTICES.md);
- a fail-closed distribution and clean-source archive audit;
- a local clean-install and installed MCP protocol smoke test.

Exact verification boundaries and remaining `NOT_RUN` device checks are documented in
[Testing and operations](docs/TESTING_AND_OPERATIONS.md) and
[Release/security audit](docs/RELEASE_AUDIT.md).

## Development

Read [CONTRIBUTING.md](CONTRIBUTING.md) before proposing changes. Tests use synthetic
fixtures only:

```text
python -B -m unittest discover -s tests -v
```

Do not add live API calls to the default test suite.

## 中文说明

这是一个面向本地使用的 Canvas + Ed 只读 MCP release candidate。平台和课程必须逐项确认；
配置只保存 keyring/environment reference，不保存 token。当前 portable downloader 尚未接线，
`course-sync` 不会下载文件。私有开发仓库中的旧版写操作 MCP、个人同步脚本和历史不会进入公开候选包。
安装、凭据、验证边界请分别阅读 `docs/INSTALLATION.md`、`docs/AUTHENTICATION.md` 和
`docs/TESTING_AND_OPERATIONS.md`。

## Attribution and license

This fork is based on [r1ckyIn/canvas-ed-mcp](https://github.com/r1ckyIn/canvas-ed-mcp),
originally created by Ricky. The project remains licensed under the MIT License; see
[LICENSE](LICENSE). That license applies to the software, not to course content or
third-party platform data.
