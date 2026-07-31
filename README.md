# Reasonix Computer Use 0.8.0-beta.5

Reasonix 跨平台桌面自动化插件（重构版）。**精准层优先（UIA/AXAPI/AT-SPI2）、视觉层兜底（EasyOCR）**，
归一化坐标协议支持多种模型（纯文本 LLM / VLM）通信。

> 本仓库是 **beta.4 完整重构后的版本**，与早期的 alpha 系列（0.8.0-alpha.x）在架构上完全不同：
> 旧版为 Windows 专用单体实现；新版抽出独立分层（protocol / platform / perception / services / tools），
> 并为三平台构建了统一的抽象接口。

## 平台支持状态

| 平台 | 抽象层接口 | 精准层实现 | 视觉层 | 状态 |
|---|---|---|---|---|
| **Windows** | ✅ | ✅ UIA (comtypes) | ✅ EasyOCR | **已实现、可运行**（真实应用验证：QQ 音乐、QQ、Ollama Desktop、WPS 等） |
| macOS | ✅ | ⬜ AXAPI 占位 | ⬜ 待接入 | **架构就位，展示未开发** |
| Linux | ✅ | ⬜ AT-SPI2 占位 | ⬜ 待接入 | **架构就位，展示未开发** |

插件不是 Windows 专用——`platform/` 层为三平台定义了统一的 `PlatformProvider` 接口，
macOS/Linux 的 AXAPI / AT-SPI2 精准层与视觉层实现已预留位置，待后续开发。

## 架构

```
reasonix_computer_use/
├── protocol/       — 归一化坐标协议 (CLAUDE_1024, GEMINI_1000, PIXEL, ELEMENT_REF)
├── platform/       — OS 抽象层 (Windows/macOS/Linux PlatformProvider)
├── perception/     — 精准层优先 + 视觉层兜底感知管道
│   ├── precision/  — Windows UIA (已实现) / macOS AXAPI (占位) / Linux AT-SPI2 (占位)
│   └── vision/     — EasyOCR + OpenCV 组件检测
├── services/       — 系统画像 (SystemProfiler)、Hook、Trace
├── tools/          — MCP 工具实现 (screen_interactor / computer_system / web_navigator)
│   └── hidden/     — 鼠标/键盘/截图/录屏（归一化坐标强制）
├── skills/         — Reasonix Skills (computer-use, web-navigator)
├── hooks/          — 生命周期钩子 (fail-closed 激活门禁、输入防重放)
└── commands/       — 斜杠命令
```

## 显式使用

Computer Use 默认关闭，需显式激活：

```text
/computer-use:run <任务>
```

## 工具

### 公开工具

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

## 坐标协议

所有坐标使用归一化空间，插件内部通过 `system-index.json` 中的 `scale_factor` 换算为物理像素：

| 空间 | 范围 | 视口映射 |
|---|---|---|
| `CLAUDE_1024` | 0–1023 | 1024×768（推荐） |
| `GEMINI_1000` | 0–999 | 1000×1000 |
| `PIXEL` | 任意 | 直接物理像素 |
| `ELEMENT_REF` | N/A | 元素 ID 引用（最稳定，推荐纯文本 LLM） |

## 感知策略

精准层优先 → 视觉层兜底：

1. **精准层**：Windows UIA (comtypes) / macOS AXAPI / Linux AT-SPI2
2. **视觉层**：EasyOCR（GPU 可用，中文 95%+ 准确率）
3. 视觉层仅输出结构化坐标与文本，**不做决策**。决策由宿主 Agent（VLM/LLM）完成。

每次 `observe` 附带新鲜截图锚点（`screenshot_path`）；UIA 元素稀疏时返回 `quality_hint`
提示"自绘 UI 可能未暴露全部控件，请截图视觉验证"——无障碍树不可信时，Agent 有最低成本的视觉验证入口。

## 关键设计

- **人性化输入节奏**：点击按下保持 60ms、双击间隔 200ms（人类节奏，Windows 双击阈值内）——
  自绘 UI（QQ 音乐、CEF）不再把机器级双击识别成两次单击。
- **动作名容错**：拼错动作类型时错误消息自动建议最接近的合法动作（`dblClick → double_click`）。
- **完成判定与防循环**：同一步骤最多重试 2 次；快照无状态变化时改用截图/提示音等替代证据；
  目标动作已生效且无反向证据即判定完成。`expect` 验证支持 `text_present` / `text_absent` / `contains`。
- **fail-closed 安全**：文本注入防重放（input_guard 跨进程哈希熔断）、stale revision 拒绝执行、
  坐标物理像素校验、`blocked` 硬停止——不允许绕过 GUI 执行器改用 Shell。
- **视觉路由 fail-closed**：当前模型未声明图片理解能力时，插件绝不假装已理解截图，
  而是把截图交给已配置的外部视觉路由（如 MiMo MCP）处理。

## 安装

```bash
reasonix plugin install E:/Agent/reasonix-computer-use --link --replace --yes
```

**依赖**：Pillow, comtypes, easyocr（通过 pip 自动安装）

## 版本

**0.8.0-beta.5** — 重构后审查修复与文档统一：

- 修复 trace 链路崩溃（`_read_index` NameError）
- 修复 drag 目标无效、revision 防过期缺失、annotated_image 语义
- 修复 recorder 路径穿越、`shell:` 注入、Unicode 代理对输入
- 重新接入 input_guard 防重放注入；非 Windows 平台 fail-fast
- 测试套件：167 失败 → 0 失败（legacy 测试归档为 skip）

**beta.5 后续修复（实战驱动）**：

- 人性化点击节奏（按下 60ms / 双击间隔 200ms），修复自绘 UI 双击失效（QQ 音乐实测）
- 动作名容错建议（`dblClick → double_click`）
- `expect` 验证支持 `text_absent` / `contains`
- observe 附截图锚点（`screenshot_path`）+ UIA 稀疏提示（`quality_hint`）
- 防循环规则写入 SKILL：同一步骤最多重试 2 次、替代证据验证、已生效即结束

**0.8.0-beta.4** — 完整重构：

- 归一化坐标协议（CLAUDE_1024 / GEMINI_1000 / PIXEL / ELEMENT_REF）
- 精准层优先 + 视觉层兜底感知架构
- `screen_interactor` 合并 observe + execute
- `web_navigator` Web 场景统一通道
- 隐藏工具归一化坐标强制
- 跨平台抽象层（Windows / macOS / Linux）
- SystemProfiler 含 scale_factor
- EasyOCR 替换 RapidOCR
