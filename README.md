# Reasonix Computer Use 0.8.0-beta.5

Reasonix 跨平台桌面自动化插件。精准层优先（UIA/AXAPI/AT-SPI2），视觉层兜底（EasyOCR），归一化坐标协议支持多模型通信。

## 架构

```
reasonix_computer_use/
├── protocol/       — 归一化坐标协议 (CLAUDE_1024, GEMINI_1000, PIXEL, ELEMENT_REF)
├── platform/       — OS 抽象层 (Windows/macOS/Linux PlatformProvider)
├── perception/     — 精准层优先 + 视觉层兜底感知管道
│   ├── precision/  — Windows UIA / macOS AXAPI / Linux AT-SPI2
│   └── vision/     — EasyOCR + OpenCV 组件检测
├── services/       — 系统画像、Hook、Trace
├── tools/          — MCP 工具实现
│   └── hidden/     — 鼠标/键盘/截图/录屏（归一化坐标强制）
├── skills/         — Reasonix Skills (computer-use, web-navigator)
├── hooks/          — 生命周期钩子 (fail-closed 激活门禁)
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
3. **视觉层仅输出结构化坐标与文本**，不做决策。决策由宿主 Agent（VLM/LLM）完成。

## 安装

```bash
reasonix plugin install E:/Agent/reasonix-computer-use --link --replace --yes
```

**依赖**：Pillow, comtypes, easyocr（通过 pip 自动安装）

## 版本

**0.8.0-beta.5** — 审查修复与文档统一：

- 修复 trace 链路崩溃（`_read_index` NameError）
- 修复 drag 目标无效、revision 防过期缺失、annotated_image 语义
- 修复 recorder 路径穿越、`shell:` 注入、Unicode 代理对输入
- 重新接入 input_guard 防重放注入；非 Windows 平台 fail-fast
- 测试套件：167 失败 → 0 失败（legacy 测试归档为 skip）

**0.8.0-beta.4** — 完整重构：

- 归一化坐标协议（CLAUDE_1024 / GEMINI_1000 / PIXEL / ELEMENT_REF）
- 精准层优先 + 视觉层兜底感知架构
- `screen_interactor` 合并 observe + execute
- `web_navigator` Web 场景统一通道
- 隐藏工具归一化坐标强制
- 跨平台抽象层（Windows / macOS / Linux）
- SystemProfiler 含 scale_factor
- EasyOCR 替换 RapidOCR
