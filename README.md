# Reasonix Computer Use

**Reasonix 跨平台桌面自动化插件** — 让 AI Agent 能观察并操作真实桌面应用（GUI 自动化）。

精准层优先（**UIA / AXAPI / AT-SPI2**）+ 视觉层兜底（**EasyOCR**），归一化坐标协议，
一套代码驱动 **Windows / Linux / macOS** 三平台，内置 MCP 工具、Skills、Hooks 与记忆系统。

> **重构版说明**：本仓库是 beta.4 完整重构后的版本，与早期 alpha 系列（0.8.0-alpha.x）完全不同——
> 旧版为 Windows 专用单体实现；新版抽出独立分层（protocol / platform / perception / services / tools），
> 为三平台构建统一抽象接口，同一套公共时序与协议代码。

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

## 🚀 快速开始

### 1. 安装

```bash
# 开发/链接安装
reasonix plugin install /path/to/reasonix-computer-use --link --replace --yes
```

**平台依赖**：

| 平台 | pip 依赖 | 系统依赖 |
|---|---|---|
| Windows | 自动安装（Pillow/comtypes/easyocr） | 无 |
| Linux | `pip install -e ".[dev,linux]"` | `apt install xvfb at-spi2-core python3-gi gir1.2-atspi-2.0`（输入中文需 `xclip` 或 `xsel`；Wayland 截图需 `grim`） |
| macOS | `pip install -e ".[dev,macos]"` | 系统设置 → 隐私与安全性 → 授予进程**辅助功能**（输入注入）与**屏幕录制**（截图）权限 |

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

**CI 三平台**（GitHub Actions `tests.yml`）：windows-latest + ubuntu-latest（xvfb + at-spi2-core）+ macos-14，每次 push 自动跑全量测试。

**测试规模**：157+ 测试（公共逻辑 / 三平台 provider / 感知层 / 系统画像 / 工具接口），fake 注入保证跨平台可跑。

> 📚 实战经验记录在 `docs/LESSONS.zh-CN.md`（本地保留，不上传）：QQ 音乐双击修复、防循环规则、三平台适配、CI 踩坑等 14 条教训。

## 📦 版本历史

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

## 📄 许可证

MIT
