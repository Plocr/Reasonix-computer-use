# Reasonix Computer Use

**Reasonix 跨平台桌面自动化插件** — 让 AI Agent 能观察并操作真实桌面应用（GUI 自动化）。

精准层优先（**UIA / AXAPI / AT-SPI2**）+ 视觉层兜底（**EasyOCR**），归一化坐标协议，
一套代码驱动 **Windows / Linux / macOS** 三平台，内置 MCP 工具、Skills、Hooks 与记忆系统。

> **重构版说明**：本仓库是 beta.4 完整重构后的版本，与早期 alpha 系列（0.8.0-alpha.x）完全不同——
> 旧版为 Windows 专用单体实现；新版抽出独立分层（protocol / platform / perception / services / tools），
> 为三平台构建统一抽象接口，同一套公共时序与协议代码。

> **项目状态（2026-11）**：上游 Reasonix 已停止维护，本仓库随之**归档**——不再跟进新版兼容，
> 也不做功能迭代。代码保留为一份可运行的历史实现：三平台感知与输入层、归一化坐标协议、
> MCP 工具面、技能与钩子都按原样可用（`pip install -e ".[dev]"` 后 `pytest` 全绿）。
> 比代码更有复用价值的是下面「设计要点（可复用）」一节里那批取舍——它们与宿主无关，
> 可以整体搬到别的 computer-use 运行时或研究代码里。最后一批修复（0.9.0-preview）已并入
> `main`：manifest 对 Reasonix **v1.17 与 v1.22+ 双向可装**（实测 `--dry-run` 均 `compatibility: full`），
> `mode: "ocr"` 生效，单步失败不再中止批次，MCP 主循环不再因心跳行/热更新退出。

## ✨ 特性

| 能力 | 说明 |
|---|---|
| 🖥️ **三平台统一** | Windows（UIA+SendInput）/ Linux X11（AT-SPI2+XTEST）/ macOS（AXAPI+CGEvent），共用 `platform/common.py` 单一事实源 |
| 🎯 **精准层优先** | 无障碍树（UIA/AXAPI/AT-SPI2）输出结构化元素（ID/角色/文本/物理像素坐标），Agent 直接引用元素操作 |
| 👁️ **视觉层兜底** | EasyOCR（GPU 可用，中文 95%+ 准确率）处理精准层不可用的场景（自绘 UI、Wayland、无权限） |
| 📐 **归一化坐标协议** | CLAUDE_1024 / GEMINI_1000 / PIXEL / ELEMENT_REF 四种空间，内部自动换算物理像素 |
| 🤖 **Agent 级容错** | 动作名拼错自动建议（`dblClick → double_click`）；`expect` 验证（text_present/absent/contains）；防循环规则（重试≤2 次、替代证据、已生效即结束） |
| 🕐 **人性化输入节奏** | 点击按下 60ms、双击间隔 200ms——自绘 UI 不再把机器级双击识别成两次单击 |
| 🛡️ **fail-closed 安全** | 文本注入防重放（跨进程哈希熔断）、stale revision 拒绝执行、坐标校验、`blocked` 硬停止、禁止绕过 GUI 改用 Shell |
| 📸 **截图锚点** | 每次 observe 附新鲜截图路径 + UIA 稀疏提示（`quality_hint`），无障碍树不可信时 Agent 有最低成本视觉验证入口 |
| 🔌 **MCP 工具套件** | `screen_interactor` / `computer_system` / `web_navigator` + 兼容别名 + 隐藏工具 |
| 📊 **系统画像** | 显示器/DPI/缩放、应用发现（注册表/.desktop/.app）、常用目录（注册表/XDG/Foundation），三平台统一 schema |

## 🖥️ 平台支持

| 平台 | 精准层 | 输入注入 | 截图 | 录屏 | 状态 |
|---|---|---|---|---|---|
| **Windows** | ✅ UIA (comtypes) | ✅ SendInput | ✅ PIL | ✅ ffmpeg gdigrab | **已实现、可运行**（实战验证：QQ 音乐、QQ、Ollama Desktop、WPS 等） |
| **Linux (X11)** | ✅ AT-SPI2 (PyGObject) | ✅ XTEST | ✅ mss | ✅ ffmpeg x11grab | **已实现**（xvfb CI 验证） |
| Linux (Wayland) | ⬜ 受限 | ⬜ 不可用 | ⬜ | ⬜ | 全局坐标/注入不可用 → 自动降级视觉层 |
| **macOS** | ✅ AXAPI (PyObjC) | ✅ CGEvent | ✅ mss | ✅ ffmpeg avfoundation | **已实现**（macOS CI 验证），需**辅助功能 + 屏幕录制**权限 |
| macOS (无权限) | ⬜ 受限 | ⬜ 不可用 | ⬜ | ⬜ | 未授权 → 自动降级视觉层，注入操作给出明确指引 |

## 🧭 设计要点（可复用）

这一节是这个项目真正值得留下的东西：桌面自动化里那些**看上去是细节、实际决定成败**的取舍。
每一条都对应代码里的具体位置，可以单独摘出去用在别的宿主上。

| # | 设计 | 具体做法 | 作用 | 位置 |
|---|---|---|---|---|
| 1 | **感知分层 + 自适应黑名单** | 精准层（UIA/AXAPI/AT-SPI2）优先；返回空则把该窗口拉黑 60s 后重试，期间走视觉层 | 自绘 UI（QQ 音乐）、WebView2 应用不会让整条链路卡死在不可用的一层；UI 改版后自动恢复 | `perception/router.py` |
| 2 | **显式 mode 覆盖隐式缓存** | `observe_mode: auto\|ocr\|precision`；`ocr` 强制截屏+OCR，`precision` 忽略黑名单 | "上次 UIA 有结果"不能成为本次拒绝 OCR 的理由——无 aria 的纯图标按钮只有 OCR 能看见 | `tools/screen_interactor.py` / `perception/router.py` |
| 3 | **归一化坐标协议** | `ELEMENT_REF` / `CLAUDE_1024` / `GEMINI_1000` / `PIXEL` 四空间，窗口内映射，响应回显 `window_rect` 与物理 `x/y` | 模型输出的坐标系与真实屏幕解耦；落点可回验（`窗口内x = 物理x - window_rect[0]`），点空箱能当场定位 | `protocol/coordinates.py` |
| 4 | **revision + stale 拒绝** | 每次 observe 递增 revision；execute 带旧 revision 直接拒执行 | UI 变化后不会按过期元素坐标误点（误点搜索框/按钮是这类工具最常见的破坏性失败） | `tools/screen_interactor.py` |
| 5 | **单步失败不中止批次** | 批内任一动作失败/抛异常都继续，错误进顶层 `warnings`，另附 `after` 快照 | `type` 后紧跟的 `ENTER` 不会被前一步的校验失败吞掉；模型能按条自纠而不是整批重跑 | `tools/screen_interactor.py` |
| 6 | **不做事后 OCR 字面校验** | `type` 返回 `input_verification: "unverified"`，验证交给 `expect` / `after` | 计算器把 `1024*768` 渲染成 `1024 × 768`，字面匹配按构造不可能通过——校验方式错了，验证就永远失败 | `tools/screen_interactor.py` |
| 7 | **输入防重放的哈希熔断** | 写入前先跨进程落盘签名（应用/窗口类/任务/revision/文本哈希），TTL 600s，锁失败 fail-open | 崩溃重启后不会把同一段文本盲注第二遍；guard 自身故障绝不阻塞正常输入 | `input_guard.py` |
| 8 | **写文件类动作的"宁可失败"** | `save_as`：快捷键 → 校验前台窗口标题确实是文件对话框 → 注入路径 → 回车；**没检测到对话框一个字符都不输** | 路径写进正文是静默数据破坏；宁可返回 `save_dialog_not_detected` 让人/模型改走菜单 | `tools/screen_interactor.py` |
| 9 | **manifest 双写兼容** | `apiVersion: reasonix.io/plugin/v2` + `contributes`（含 `agents`），同时在根级保留 v1.x 认识的 `skills/commands/hooks/mcpServers`，且**根级不放 `agents`** | 同一份包在 v1.17（只读根级）和 v1.22+（只认 contributes，且拒绝根级 `agents`）都能装——实测两版 `--dry-run` 均 `compatibility: full` | `reasonix-plugin.json` |
| 10 | **默认关闭（fail-closed）** | 钩子按"显式任务"激活，普通对话不创建 trace、不授予操作权限 | 桌面自动化天然高危，能力必须由用户显式开启，而不是靠模型自觉 | `hooks/route_guard.py` |
| 11 | **MCP 传输层要"钝"** | 空行=心跳不是 EOF；坏 JSON 跳过；插件热更新返回可重试错误继续服务；诊断只写 stderr | stdio 协议上一个空行就能让服务端退出，宿主侧表现为"插件崩了必须重启" | `mcp_server.py` |

如果只搬三样：**2（显式 mode）**、**5+6（失败语义）**、**8（写文件守卫）**——
它们解决的都是"不报错但结果错"的静默失败，比任何识别精度提升都更能决定任务成功率。

## 🚀 快速开始

### 1. 安装

Reasonix 插件包通过 `reasonix plugin` 命令安装和管理，按全局范围写入
Reasonix home 目录（`~/.reasonix/plugins/<name>/`，安装状态记录在
`~/.reasonix/plugin-packages.json`）。

**方式一：从 Git 仓库安装（推荐）**

```bash
# 预检：只预览安装计划，不写文件
reasonix plugin install git:github.com/<owner>/reasonix-computer-use --dry-run

# 确认计划后安装
reasonix plugin install git:github.com/<owner>/reasonix-computer-use --yes
```

也可以直接给 GitHub 仓库 URL，或带分支/子目录的 URL
（`https://github.com/<owner>/reasonix-computer-use/tree/<branch>/<subdir>`）。
`install` 只有在带有 `--dry-run` 或 `--yes` 时才写文件。

**方式二：本地目录安装（开发模式）**

```bash
# 开发/链接安装：不复制插件，直接链接本地目录；移动或删除目录会导致链接插件失效
reasonix plugin install /path/to/reasonix-computer-use --link --replace --yes
```

常用安装参数：

| 参数 | 作用 |
|---|---|
| `--dry-run` | 只规划和校验安装，不写文件 |
| `--yes` | 确认执行会写文件的安装 |
| `--replace` | 允许当前来源替换已安装的同名插件 |
| `--name <name>` | 覆盖插件 manifest 声明的名称，作为本次安装名称 |
| `--link` | 链接本地插件目录，而不是复制到插件存储目录 |

**桌面端安装**：打开 **设置 → 插件**，选择 **本地目录** 或填写 **Git 来源**，
先点 **预检** 校验来源并展示安装计划，再点 **安装插件**；**开发模式：链接源目录**
只在本地目录模式下出现。

**插件管理**：

```bash
reasonix plugin list              # 列出已安装插件
reasonix plugin show computer-use # 查看元数据、根目录、来源及导出的能力清单
reasonix plugin doctor computer-use  # 检查 manifest 与 skill roots 是否可读
reasonix plugin disable computer-use # 禁用（不卸载）
reasonix plugin enable computer-use  # 重新启用
reasonix plugin remove computer-use --yes  # 卸载（remove 可写成 uninstall）
```

会话内可用 `/plugins` 列出已安装插件，`/plugins show computer-use` 查看该插件
导出的 skills、hooks、MCP servers 和使用提示；`/computer-use:run <任务>` 显式启动
桌面自动化任务。

**平台依赖**：

| 平台 | pip 依赖 | 系统依赖 |
|---|---|---|
| Windows | 自动安装（Pillow/comtypes/easyocr） | 无 |
| Linux | `pip install -e ".[dev,linux]"` | `apt install xvfb at-spi2-core python3-gi gir1.2-atspi-2.0`（输入中文需 `xclip` 或 `xsel`；Wayland 截图需 `grim`） |
| macOS | `pip install -e ".[dev,macos]"` | 系统设置 → 隐私与安全性 → 授予进程**辅助功能**（输入注入）与**屏幕录制**（截图）权限 |

> **Linux/macOS 入口说明**：插件 manifest 中的 MCP 启动命令
> `reasonix-computer-use.bat` 为 Windows 入口。Linux/macOS 上请先完成上方
> pip 安装（生成 `reasonix-computer-use` console script），再把
> `reasonix-plugin.json` 中 `hooks` 与 `mcpServers` 的 `command` 改为
> `reasonix-computer-use`（console script 名，位于 PATH 中），hooks 的
> `args: ["--hook"]` 保持不变。

### 2. 显式使用

Computer Use 默认关闭（fail-closed），需显式激活：

```text
/computer-use:run <任务>
```

示例：`/computer-use:run 打开qq音乐，并播放许嵩的歌`

### 3. 工作原理

```
用户指令 → /computer-use:run → operator 技能 (SKILL.md)
  → screen_interactor(observe)      # 精准层/视觉层输出结构化元素
  → screen_interactor(execute)      # 归一化坐标动作（点击/输入/拖拽/滚动）
  → expect 验证 + 替代证据          # 确认生效，防循环
  → 完成判定 → 结束任务
```

## 🔧 工具参考

### 公开工具（MCP）

| 工具 | 用途 | 关键参数 |
|---|---|---|
| `screen_interactor` | **核心工具**：观察窗口 + 执行操作 | `mode` (observe/execute), `actions[]`, `element_ref` |
| `computer_system` | 系统画像、诊断、依赖安装、窗口管理 | `operation` (profile/diagnose/setup/…) |
| `web_navigator` | Web 场景路由（Playwright MCP / mcp-chrome） | `operation` (navigate/snapshot/action) |

### 兼容别名（向后兼容）

- `computer_app` → 应用启动/聚焦/关闭
- `computer_state` → `screen_interactor(observe)`
- `computer_action` → `screen_interactor(execute)`

### 隐藏工具（归一化坐标强制，Agent 不可见）

- `mouse_action` — 点击/拖拽/滚动（归一化坐标）
- `keyboard_action` — 输入/按键/组合键
- `screenshot` — 截图 → 用户下载路径
- `screen_recorder` — 录屏 → 用户下载路径

## 🎮 动作类型

| 动作 | 说明 | 必需参数 |
|---|---|---|
| `click` / `double_click` / `right_click` | 点击（双击/右键） | `element_ref` |
| `type` | 输入文本（Unicode 全支持） | `text` |
| `press` | 按键组合（如 `["CTRL","C"]`） | `keys` |
| `scroll` | 滚动 | `amount`（正=下/右） |
| `drag` | 拖拽（人性化插值节奏） | `to_x`/`to_y` |
| `wait` | 等待 | `duration`（秒） |

**执行验证（expect）**：

```json
{"text_present": "许嵩", "text_absent": "加载中", "contains": true}
```

- `text_present` — 期望出现的文本（str 或 list）
- `text_absent` — 期望消失的文本（如"加载中"）
- `contains: true` — 子串匹配（宽容 UIA 文本首尾空白）

## 📐 坐标协议

所有坐标使用归一化空间，插件内部通过 `system-index.json` 的 `scale_factor` 换算为物理像素：

| 空间 | 范围 | 语义 |
|---|---|---|
| `CLAUDE_1024` | 0–1023 | **前台窗口内归一化**（有窗口时）：(0,0)=窗口左上角，(1023,1023)=窗口右下角；无窗口时为全屏 |
| `GEMINI_1000` | 0–999 | 同上（窗口内归一化） |
| `PIXEL` | 任意 | **物理屏幕像素**，原样使用（推荐配合截图定位） |
| `ELEMENT_REF` | N/A | 元素 ID 引用（最稳定，推荐纯文本 LLM） |

> ⚠️ **fallback 必须显式带 `space` 字段**（`{"x":…,"y":…,"space":"PIXEL"}`），否则默认按
> CLAUDE_1024 解释——这是最常见的点击落空原因。每个 point action 响应返回实际物理坐标
> `x`/`y` 与映射基准 `window_rect`（窗口 rect 为物理像素），可用 `物理x - window_rect[0]`
> 反推窗口内位置来验证点击是否命中目标。

## 👁️ 感知策略

**精准层优先 → 视觉层兜底**：

1. 精准层：UIA（Windows）/ AXAPI（macOS）/ AT-SPI2（Linux）——结构化元素（ID/角色/文本/bbox）
2. 视觉层：EasyOCR——仅输出结构化坐标与文本，**不做决策**（决策由宿主 Agent 完成）
3. 每次 observe 附新鲜截图锚点（`screenshot_path`）；精准层元素稀疏时返回 `quality_hint`
4. 视觉路由 fail-closed：当前模型未声明图片理解能力时，插件绝不假装已理解截图，而是交给已配置的外部视觉路由（如 MiMo MCP）

## 🏗️ 架构

```
reasonix_computer_use/
├── protocol/       — 归一化坐标协议 (CLAUDE_1024, GEMINI_1000, PIXEL, ELEMENT_REF)
├── platform/       — OS 抽象层：base 接口 + windows/linux/macos 实现 + common 公共逻辑
│   └── common.py   — 单一事实源：人类时序/键名规范化/拖拽插值/FfmpegRecorder
├── perception/     — 精准层优先 + 视觉层兜底感知管道
│   ├── precision/  — Windows UIA / macOS AXAPI / Linux AT-SPI2（均已实现）
│   └── vision/     — EasyOCR + OpenCV 组件检测
├── services/       — 系统画像 (SystemProfiler 三平台统一 schema)、Trace
├── tools/          — MCP 工具实现 (screen_interactor / computer_system / web_navigator)
│   └── hidden/     — 鼠标/键盘/截图/录屏（归一化坐标强制）
├── skills/         — Reasonix Skills (computer-use, web-navigator)
├── hooks/          — 生命周期钩子 (fail-closed 激活门禁、输入防重放)
├── commands/       — 斜杠命令 (run/doctor/benchmark/test/trace)
└── agents/         — operator 子代理配置
```

## 🛡️ 安全模型

- **显式激活**：Computer Use 默认关闭，仅 `/computer-use:run` 激活，PreToolUse 钩子拒绝其余场景
- **输入防重放**：`input_guard` 跨进程哈希熔断，崩溃重启的任务不会重复注入同一文本
- **stale revision**：元素快照过期后拒绝执行，防止 UI 变化后误点
- **禁止绕过**：`blocked` 硬停止，不允许改用 Shell 绕过 GUI 执行器
- **脱敏追踪**：任务 trace 自动脱敏（密码/路径/桌面文件夹），仅记录哈希与长度
- **坐标校验**：物理像素移动后回读验证，不匹配即报错

## 🔐 权限要求

| 平台 | 权限 | 缺失时的行为 |
|---|---|---|
| macOS | 辅助功能（输入注入）、屏幕录制（截图） | 明确 OSError 指引 + 自动降级视觉层 |
| Linux | 无特殊权限（X11 会话） | — |
| Windows | 无特殊权限 | — |

## 🧪 开发与测试

```bash
# 安装开发依赖
pip install -e ".[dev,linux]"     # Linux 需先装系统包
pip install -e ".[dev,macos]"     # macOS

# 运行测试
python -m pytest -q -p no:warnings
```

**EasyOCR 预加载**：默认情况下 OCR 模型在首次视觉请求时延迟加载（避免文本模型
会话白白占用数百 MB 显存/内存）。需要启动预加载时设置环境变量
`REASONIX_COMPUTER_USE_PREWARM_OCR=1`。

**CI 三平台**（GitHub Actions `tests.yml`）：windows-latest + ubuntu-latest（xvfb + at-spi2-core）+ macos-14，每次 push 自动跑全量测试。

**测试规模**：198 测试（公共逻辑 / 三平台 provider / 感知层 / 系统画像 / 工具接口 / issue 回归），fake 注入保证跨平台可跑。

> 📚 实战经验记录在 `docs/LESSONS.zh-CN.md`（本地保留，不上传）：QQ 音乐双击修复、防循环规则、三平台适配、CI 踩坑等 14 条教训。

## 📦 版本历史

**0.9.0-preview 收尾修复（对照下游反馈 #1–#7）**：

- **manifest 双向兼容**：声明 `apiVersion: reasonix.io/plugin/v2`，资源写进 `contributes`（含 `agents`），根级保留 v1.x 认得的字段但**不放 `agents`**；v1.17 与 v1.22+ 实测均可安装（`compatibility: full`）
- **`mode: "ocr"` 真正生效**：新增 `observe_mode: auto|ocr|precision`；显式 OCR 请求强制截屏 + OCR，不再被精准层结果短路；响应回传 `requested_mode` / `vision_available` / `vision_used`（#3）
- **批次不再被单步失败吞掉**：取消 `type` 的 OCR 字面匹配校验，改为 `input_verification: "unverified"`；单步失败继续执行后续动作，错误汇总到顶层 `warnings`（#1）
- **`save_as` 动作**：另存为/导出对话框驱动，未检测到对话框时绝不注入，避免路径跑进正文（#2）
- **MCP 传输层加固**：空白心跳行不再当作 EOF 退出；坏 JSON 跳过而非杀进程；插件热更新后继续服务并返回可重试错误；启动日志从 stdout 移到 stderr（`[startup]` 行混进 JSON-RPC 流会让宿主解析失败，看起来就是"服务崩了"）（#2）
- 新增 14 条回归测试，全量 **198 passed / 195 skipped**

**0.9.0-preview** — 三平台统一预览版：

- **三平台完整实现**：Windows（UIA+SendInput）/ Linux X11（AT-SPI2+XTEST）/ macOS（AXAPI+CGEvent），共用 `platform/common.py` 单一事实源
- **系统画像三平台统一 schema**：显示器/缩放/应用发现/常用目录/硬件（注册表、.desktop、.app、XDG、Foundation、sysctl）
- CI 三平台全绿（windows + linux/xvfb + macOS），157+ 测试
- 完整对外文档（README 重写）、GitHub 元信息（About/Topics）

**0.8.0-beta.5** — 重构后审查修复与文档统一：

- 修复 trace 链路崩溃（`_read_index` NameError）
- 修复 drag 目标无效、revision 防过期缺失、annotated_image 语义
- 修复 recorder 路径穿越、`shell:` 注入、Unicode 代理对输入
- 重新接入 input_guard 防重放注入；非 Windows 平台 fail-fast
- 测试套件：167 失败 → 0 失败（legacy 测试归档为 skip）

**beta.5 后续（实战驱动）**：

- 人性化点击节奏（60ms/200ms），修复自绘 UI 双击失效（QQ 音乐实测）
- 动作名容错建议、`expect` 验证（text_absent/contains）、截图锚点 + quality_hint
- 防循环规则写入 SKILL；**三平台适配完成**（Linux X11 + macOS），CI 三平台全绿
- macOS/Linux 系统画像（sysctl/system_profiler、.desktop/.app 发现、XDG/Foundation 目录）

**0.8.0-beta.4** — 完整重构：

- 归一化坐标协议（CLAUDE_1024 / GEMINI_1000 / PIXEL / ELEMENT_REF）
- 精准层优先 + 视觉层兜底感知架构；`screen_interactor` 合并 observe + execute
- `web_navigator` Web 场景统一通道；隐藏工具归一化坐标强制
- 跨平台抽象层（Windows / macOS / Linux）；SystemProfiler 含 scale_factor
- EasyOCR 替换 RapidOCR

## 🙏 致谢

**谢谢 Reasonix 生态。** 在 *DSH 还没成型的那段时间*，它是少数真能当 agent 用、
并且真能驱动桌面的东西——`reasonix plugin` 那套 manifest + MCP + skill + hook 的
扩展模型，让「桌面自动化」这种原本要自带一整套框架才做得成的想法，变成一个能
`install` 进宿主的小包。这个仓库里所有实战都不是纸上推演，是挂在它上面跑出来的：
QQ 音乐的双击节奏、WPS 的另存为、Ollama Desktop 的窗口切换——所以它停维护了，
这些经验还在，也被搬进了 DSH。

也谢谢在 issue 里报现场的人。他们带来的是一手证据，不是"感觉不能用"：

| 反馈者 | 带来的现场 |
|---|---|
| [@qingduzhai](https://github.com/qingduzhai) | 计算器把 `1024*768` 渲染成 `1024 × 768`，暴露了"字面匹配做校验"这个设计错误（#1） |
| [@Linearl](https://github.com/Linearl) | WebView2 里无 aria 的纯图标按钮 + OCR 被缓存短路，逼出了显式 `mode`（#2 #3） |
| [@qi20040406](https://github.com/qi20040406) | 补 `apiVersion` 后仍报 `unknown field "agents"`，精确定位到 v2 校验边界（#5） |
| [@lixi523](https://github.com/lixi523) | 多模型配置下的同一装不上问题，说明这不是个例（#4） |
| [@taominjigithub002](https://github.com/taominjigithub002) | 自己写好 `contributes` 迁移并给出实测结果，PR #6 是最后双写方案的起点 |
| [@esengine](https://github.com/esengine) | 市场侧把"为什么装不上、怎么重发"讲得足够具体，省掉了整套猜测（#7） |

它们的共同价值是：**报的都是"不报错但结果不对"**——批次被静默截断、OCR 从没执行、
契约对不上。这类问题自己测永远测不出来，只能靠真实用户在真实机器上撞。

最后，谢谢所有在 README 之外读这份代码的人。设计可以过时，踩过的坑不会。

## 📄 许可证

MIT
