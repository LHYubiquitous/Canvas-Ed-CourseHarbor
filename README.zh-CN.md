# CourseHarbor

[English](README.md) | [简体中文](README.zh-CN.md)

CourseHarbor 是面向学生的本地桌面工具，可从 Canvas、Ed 或两者同步课程资料，
生成本周截止日期 TXT 与全学期 Markdown 清单，并为已确认的课程提供只读 MCP 工具。
它只向本地输出目录写文件，不会在 Canvas 或 Ed 上提交、编辑或删除内容。

`0.2.0rc18` 是当前 GitHub prerelease。请从
[v0.2.0rc18 Release](https://github.com/LHYubiquitous/Canvas-Ed-CourseHarbor/releases/tag/v0.2.0rc18)
下载对应桌面 App；本项目尚未发布到 PyPI。
本版本包含图形化 token 管理、CourseHarbor App Shell、due-date 页面、sync-run reports，
以及下文所述经过审核的 Windows portable bundle 和经 owner-device 测试的 arm64 macOS
DMG。该 DMG 使用 ad-hoc signing，尚未经过 Apple notarization。

## 1. 下载并打开桌面 App

普通用户直接下载桌面包即可，不需要另外安装 Python。

### Windows 10/11 x86_64

1. 下载 `CourseHarbor-v0.2.0rc18-windows-x86_64-portable.zip`。
2. 把整个 ZIP 解压到准备长期保留的目录；不要在压缩包内部直接运行，也不要只复制一个
   `.exe` 出来。
3. 打开 `CourseHarbor.exe`。

Windows 包是 unsigned portable app，不是 installer。系统可能显示“无法识别的应用”提示；
运行前请用 Release 中的 `SHA256SUMS` 核对下载文件。

### Apple silicon macOS

1. 下载 `CourseHarbor-v0.2.0rc18-macos-arm64.dmg`。
2. 打开 DMG，把 **CourseHarbor** 拖进 **Applications**。
3. 从 Applications 启动 CourseHarbor。

当前 DMG 仅支持 Apple silicon (`arm64`)，使用 ad-hoc signing，尚未经过 Apple
notarization，因此 Gatekeeper 状态为 `DEGRADED`；运行前请核对 `SHA256SUMS`。Intel / universal
build 仍为 `NOT_RUN`。

Python wheel、CLI/MCP 和源码构建集中放在后面的
[Python package 与源码构建](#6-python-package-与源码构建)，不与桌面 App 使用步骤混排。

## 2. 获取并保存 API token

### 2.1 Canvas API token

1. 登录学校的 Canvas 网站，并确认当前账号能正常打开需要同步的课程。
2. 进入 **Account → Settings（账户 → 设置）**。直接地址通常是
   `https://<your-canvas-host>/profile/settings`。
3. 向下找到 **Approved Integrations**，选择 **New Access Token**。
4. Purpose 可填写 `canvas-ed-mcp`；设置尽可能短且够用的有效期，然后生成 token。
5. Canvas 只显示新 token 一次。立即把它存入系统钥匙串，不要写入文本文件。

悉尼大学 Canvas 的 API base URL 是
`https://canvas.sydney.edu.au/api/v1`。如果设置页没有 **New Access Token**，说明学校
可能禁用了自助 token；应联系学校 Canvas 支持，不要绕过限制。官方操作说明见
[Instructure Canvas OAuth2: Manual Token Generation](https://developerdocs.instructure.com/services/canvas/oauth2/file.oauth#manual-token-generation)。

### 2.2 Ed API token

1. 登录能够访问目标课程的 Ed 账号。
2. 从当前登录账号所在区域进入 **Settings → API Tokens**。
3. 为 `canvas-ed-mcp` 新建一个独立 token，并在创建时复制。

Ed 接口目前属于 unofficial、schema-sensitive 集成。不要用 Canvas token、SSO 密码、
浏览器 cookie 或开发者工具中复制的请求代替 Ed API token。
本项目只验证过澳洲区 token 页面 `https://edstem.org/au/settings/api-tokens`；其他 Ed 区域
均为 `NOT_RUN`。应从已登录账号进入设置页，不要猜测其他区域的 hostname。

### 2.3 在桌面向导中保存并验证

打开 CourseHarbor，在 First run 中选择实际使用的 provider。若启用 Canvas，填写学校自己的
API base URL；把 token 粘贴到 masked **API token** 输入框，然后选择 **Save and test**。
应用会先检查安全的 native
credential store，再对单个 provider 执行只读课程列表请求；只有验证成功后才写入该
profile/provider 的确定性 keyring reference。任务开始时输入框会立即清空，token 不会进入
clipboard、config、state、命令参数或 UI 状态文本。

重启或诊断时使用 **Test existing**。replacement 输入框留空表示保留旧 credential；替换
前必须确认准确的 profile、provider 与本地 keyring target。验证失败或写入前取消不会改动
旧值；如果后续 config 保存失败，已验证的 credential 会保留，用户可直接重试 setup。

`env:` reference 属于 CLI/headless 配置中的 externally managed、read-only 高级选项；
application layer 可以测试已有引用，但不能覆盖它；当前 first-run keyring UI 不创建
`env:` reference。

### 2.4 终端高级 fallback

headless 或恢复场景仍可使用以下命令。它们会安全提示输入值，不把 token 放进命令历史；
只执行实际使用的 provider：

```text
python -m keyring set canvas-ed-mcp/default canvas
python -m keyring set canvas-ed-mcp/default ed
```

配置向导要求 credential reference 时分别填写：

```text
keyring:canvas-ed-mcp/default/canvas
keyring:canvas-ed-mcp/default/ed
```

如果 profile 叫 `semester-2`，服务名也应改成 `canvas-ed-mcp/semester-2`，引用则改成
`keyring:canvas-ed-mcp/semester-2/canvas` 与
`keyring:canvas-ed-mcp/semester-2/ed`。token 不得放进 TOML、启动脚本、MCP 配置、截图、
聊天消息或 Git 仓库。

## 3. 完成首次配置

所有启用的 connection 通过只读测试后，CourseHarbor 会发现课程并进入 setup。只使用
Canvas 或只使用 Ed 时，流程是 **Select → Folders → Review**；只有同时启用两者时才显示
**Map**，由用户明确决定 Canvas↔Ed 课程关系。程序不会因为名字相似而自动配对。
Token 输入框为 masked 且 transient；配置只接收
`keyring:canvas-ed-mcp/default/canvas` 这类确定性引用。最终确认会创建新配置，并为尚不存在的
课程创建空的 course folder；它不会下载课程内容、不创建 Library root、不写 sync state，
也不安装 launcher。

Library root 的选择被明确推迟到 Folders 步骤。该 root 同时包含课程文件夹、
`Canvas Ed MCP Launchers` 与 due reports，因此不应只称为 launcher directory。Library root
必须在进入 Review 前已经存在，setup 不会静默创建它。每门新的 logical course
都可以点击 **Browse…** 选择该 root 下的直接子文件夹；例如先把 Desktop
设为 Library root，再分别选择桌面课程文件夹。如果所选课程文件夹不在当前 root 下，可在
同一页修改 root 后重试，无需重新执行 provider discovery。保存时，已有目录会直接复用，
尚不存在的课程目录会立即创建。已有配置中的 root 与课程文件夹
仍保持只读，因为 configuration workflow 不会移动文件。

课程表会逐门确认要保留的课程，并让每门课选择首次同步策略：

- `baseline_from_now`：记录当前远端状态，以后只下载新增或变更内容；
- `import_all`：第一次正式同步时下载当前已有资料。

Review 页面会显示准确的 local layout，只有按下 **Save configuration** 才写入配置。

以后增删课程、重新配对或修改每门课的下载内容时，从 **Courses** 打开课程配置。编辑器会
自动载入并勾选
当前课程，保存前显示准确的配置差异。这个版本不会迁移文件夹：已有 folder name
只读；只有“没有同步历史，且原课程文件夹不存在或为空”的 source 可以重新配对，
否则界面会显示锁定原因。保存只修改配置，不移动或删除文件，也不会自动开始同步。从配置中
移除 source 不会删除已经下载的文件或历史 state。

## 4. 使用 CourseHarbor App Shell

没有 profile 时会进入 First run；只有一个健康 profile 时直接打开；存在多个 profile 或某个
profile 需要修复时显示 **Profile chooser**。这里仅检查本地 config/state metadata，不连接
Canvas 或 Ed。

### Library

选择 **Preview course material** 执行只读发现，查看每门课、每个 provider 的准确 action，
必要时展开 warning summary，再选择 **Apply reviewed changes**。Preview 不写课程文件；Apply
只执行刚刚审阅的 plan。耗时工作不阻塞 Qt event loop，并显示进度与取消状态。

### Due dates

**This week** 与 **Semester** 是两个独立视图，各自拥有 **Load**、export folder 和明确的
**Export**。Load 不会写文件，两类报告也可以使用不同目录。导出采用 create-only 策略；内容
变化时生成 numbered revision，不覆盖旧报告。

### Courses

按 Course → sync run → changed files 展开，可以看到每次新增或更新的文件和准确 local
destination，也可以从这里进入课程配置。本地报告历史不含 token 或 provider 原始异常文本。

### Connections

**Test existing** 通过 provider-isolated 的只读请求诊断当前 credential。轮换 token 时，把新值
粘贴到 masked **Replacement token**，再选择 **Save and test**；留空表示保留旧值。`env:`
reference 属于 externally managed credential，UI 不允许覆盖。

### Health & About、local files 与 profile

**Health & About** 显示不含 secret 的版本、profile、路径与 keyring readiness。**Open local
library** 打开 library root；setup 保存时已经确保每门课的 course folder 存在。后台任务运行时
关闭窗口，会先请求协作式取消并在安全边界释放 provider session。

**Switch profile** 返回 Profile chooser。**Remove profile…** 经过针对性确认后只把选中的
profile config 移入私有 `.removed` archive；不会删除课程文件、系统 credential、sync state、
logs/cache，也不会撤销远程 token。

### 可选 daily-use shortcuts

Setup 可以在 `<library root>/Canvas Ed MCP Launchers` 创建原生 shortcuts。用户手动修改过的
shortcut 永不覆盖；更新后的 managed candidate 使用 `.new` 后缀等待审核。

| 启动脚本 | 用途 |
|---|---|
| `canvas_ed_app` | 打开 CourseHarbor App Shell |
| `course_material_sync` | 更新所有已配置课程资料 |
| `this_week_due` | 生成 `Due Dates/this-week-due.txt` |
| `this_semester_due` | 按教学周生成全学期 Markdown checklist |
| `configure_courses` | 打开图形化课程重新配置 |
| `health_check` | 检查版本、路径、profile 与 keyring |
| `open_course_folder` | 打开课程输出目录 |

菜单中的课程同步会先 dry-run，再要求输入 `y` 才应用；直接双击
`course_material_sync` 是一键正式同步。due report 不覆盖旧文件：内容变化时会生成
`--r002`、`--r003` 等新版本；全学期清单会保留带稳定标记的已勾选项和手写备注。

## 5. 高级 CLI 与 MCP

常用 CLI：

```text
canvas-ed-mcp sync --profile default --dry-run
canvas-ed-mcp sync --profile default
canvas-ed-mcp setup --profile default --dry-run
canvas-ed-mcp due week --profile default
canvas-ed-mcp due semester --profile default
canvas-ed-mcp configure-gui --profile default
canvas-ed-mcp launchers install --profile default
canvas-ed-mcp open-course-folder --profile default
canvas-ed-app --profile default
```

只预览某门课时加入 `--course LOGICAL_COURSE_KEY`。移除 `--dry-run` 后只写本地 state
和输出目录，不会写入远端平台。`course-sync` 只是兼容占位命令，不执行下载。

启动只读 MCP server：

```text
canvas-ed-mcp serve --profile default
```

在 MCP host 中填写安装环境里 `canvas-ed-mcp` 可执行文件的绝对路径，arguments 填
`serve --profile default`。不要把 token 放进 host JSON；server 会通过 profile 中的
keyring reference 读取凭据。实际暴露的工具随已启用 provider 变化，至少包含
`server_status`，并可包含 Canvas/Ed 的已配置课程与 Ed 内容读取工具。

## 6. Python package 与源码构建

本节面向 developer、CLI/MCP 用户，以及没有 frozen desktop package 的平台；Windows portable
App 与 macOS DMG 用户不需要执行这些步骤。

### 安装经过审核的 wheel

支持 Python 3.10–3.14。从 Release 下载
`canvas_ed_mcp-0.2.0rc18-py3-none-any.whl`，在独立环境中安装 GUI extra：

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install .\canvas_ed_mcp-0.2.0rc18-py3-none-any.whl
.\.venv\Scripts\python.exe -m pip install "PySide6>=6.8,<7"
.\.venv\Scripts\courseharbor.exe
```

macOS / Linux 使用 `python3 -m venv .venv`，再通过 `./.venv/bin/python` 运行等价的 wheel
和 `PySide6>=6.8,<7` 安装命令。
本项目没有发布到 PyPI。

### 从源码构建并运行

```text
git clone https://github.com/LHYubiquitous/Canvas-Ed-CourseHarbor.git
cd Canvas-Ed-CourseHarbor
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[gui]"
.\.venv\Scripts\courseharbor.exe
```

macOS / Linux 把 `py` 换成 `python3`，再使用 `./.venv/bin/python` 与
`./.venv/bin/courseharbor`。Contributor 使用锁定环境时，安装 `uv 0.11.28` 后运行：

```text
uv sync --locked --all-extras --group dev
uv run --locked python -B ci/run_release_tests.py --root .
```

public release source 来自 audited clean-source archive，不包含 private development history。
详细 packaging 与验证边界见 [测试与运维](docs/TESTING_AND_OPERATIONS.md) 和
[Release audit](docs/RELEASE_AUDIT.md)。

## 7. 更新、回滚与卸载

更新前保留旧 wheel，并备份 `doctor --show-paths` 显示的非敏感 config/state。安装新
wheel 后重新生成启动脚本：

```text
python -m pip install --upgrade PATH_TO_NEW_WHEEL
canvas-ed-mcp --version
canvas-ed-mcp doctor --profile default --show-paths
canvas-ed-mcp launchers install --profile default
```

回滚使用 `python -m pip install --force-reinstall PATH_TO_PREVIOUS_WHEEL`。卸载命令
`python -m pip uninstall canvas-ed-mcp` 不会删除课程输出、profile/state 或系统钥匙串
条目；远端 token 的撤销与本地 keyring 删除需要分别处理。

## 8. 常见问题

- **`authentication_failed` / HTTP 401**：token 已失效或被撤销。重新在平台生成，写入
  同一个 keyring entry，再执行 dry-run；不要建立明文备用凭据。
- **HTTP 403**：账号或学校禁止该 endpoint。新建 token 通常不会增加账号权限；Canvas
  即使 course-wide Files 返回 403，module-linked pages/files 仍可能正常工作。
- **`missing_keyring` / `unsafe_backend`**：运行 `canvas-ed-mcp doctor --show-paths`，先修复
  native keyring，再进行任何 provider 请求。
- **due report 没生成**：某个必需 Canvas/Ed source 没有完整 snapshot。恢复访问后重试；
  程序不会用不完整数据覆盖旧报告。
- **启动脚本仍指向旧环境**：运行
  `canvas-ed-mcp launchers install --profile default`。用户手动编辑过的脚本会保留，新的
  managed candidate 会以 `.new` 后缀生成供审核；此时 GUI 只打开 shortcuts folder 供检查，
  不会把被保留的旧 canonical 文件当作新的 daily dashboard 执行。
- **Ed schema drift**：Ed API 没有稳定公开 schema。程序会 fail closed 并报告 partial
  item，不会静默丢弃后假装同步成功。

更详细的跨平台路径、headless Linux、MCP host、定时运行与安全 smoke test 见
[安装与运行](docs/INSTALLATION.md)；token 撤销、轮换和 OAuth 边界见
[认证安全](docs/AUTHENTICATION.md)；测试与发布证据见
[测试与运维](docs/TESTING_AND_OPERATIONS.md)。

## 9. 隐私、边界与许可

- token 只存系统 keyring；配置只保存 reference。
- 下载课程内容不代表取得再分发权，请把输出保留在你有权控制的位置。
- Gradescope due 支持默认关闭，只有显式安装 extra 并建立精确 mapping 后才启用。
- Linux owner-device 原生 keyring/desktop launcher 验证仍为 `NOT_RUN`。
- 软件漏洞请按 [SECURITY.md](SECURITY.md) 报告。

本 fork 基于 Ricky 创建的
[r1ckyIn/canvas-ed-mcp](https://github.com/r1ckyIn/canvas-ed-mcp)，软件继续使用
[MIT License](LICENSE)。MIT License 仅适用于软件，不适用于课程内容或第三方平台数据。
