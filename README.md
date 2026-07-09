# Reasonix Computer Use 插件包

一个为 Windows 提供**浏览器外 GUI 自动化**能力的 Reasonix 插件包。它补充了 `chrome-devtools` MCP，覆盖系统桌面、原生应用以及浏览器无法触及的 GUI 场景。

> 为 [Reasonix](https://github.com/esengine/DeepSeek-Reasonix) 设计——一个 DeepSeek 原生的终端 AI 编码助手。

---

## 🧩 架构

本插件包包含全部 4 种官方扩展类型：

| 组件 | 目录 | 作用 |
|---|---|---|
| **MCP 服务器** | `mcp/` | 核心执行层——截图、鼠标、键盘、UI 树、应用发现 |
| **Agent 技能** | `skills/` | 工作流层——教 Agent 如何在特定场景下使用 MCP 工具 |
| **钩子 (Hooks)** | `hooks/` | 安全与自动化层——操作前安全检查、操作后日志记录 |
| **记忆 (Memory)** | `memory/` | 学习层——记住用户偏好、已操作的 UI 元素、操作日志 |

---

## 🚀 安装

### 前置条件

- **Reasonix Desktop**（带插件管理界面）或 **Reasonix CLI**
- **Python 3.10+** 及 pip
- **Windows 10/11**（macOS 和 Linux 支持即将推出）
- 基础操作不需要管理员权限。部分提权应用（如任务管理器、系统设置）可能需要管理员批准。

### 方式一：本地目录安装（推荐）

1. 克隆或下载本仓库：

```bash
git clone https://github.com/你的ID/reasonix-computer-use.git
cd reasonix-computer-use
pip install -e .
```

2. Reasonix Desktop → 插件 → 安装插件 → **本地目录** → 选择本文件夹。

### 方式二：Git 仓库安装

Reasonix Desktop → 插件 → 安装插件 → **Git 仓库** → 粘贴本仓库 URL。

---

## 🔧 可用工具

### 截图与视觉

| 工具 | 说明 |
|---|---|
| `computer_screenshot` | 全屏截图、指定窗口截图或区域截图 |
| `computer_window_list` | 列出所有可见窗口及其元数据 |
| `computer_ui_tree` | 获取所有 UI 元素的无障碍树 |
| `computer_find_element` | 通过 automation_id、名称或控件类型查找元素 |

### 鼠标

| 工具 | 说明 |
|---|---|
| `computer_mouse_move` | 移动光标到绝对坐标 |
| `computer_mouse_click` | 左键/右键/中键单击或双击 |
| `computer_mouse_scroll` | 向上/向下滚动 N 行 |

### 键盘

| 工具 | 说明 |
|---|---|
| `computer_keyboard_type` | 逐字输入文本（支持中文等 Unicode） |
| `computer_keyboard_press` | 按键或组合键（Enter、Ctrl+C 等） |

### 应用控制

| 工具 | 说明 |
|---|---|
| `computer_app_list` | 通过读取 Windows 注册表列出已安装应用 |
| `computer_app_launch` | 通过可执行文件路径启动应用 |
| `computer_window_activate` | 激活（置顶）指定窗口 |

---

## 🎯 Agent 技能

预构建的工作流模板：

- **app-control.md** —— 操作非浏览器应用：文件资源管理器、系统设置、IDE
- **form-fill.md** —— 使用 UI 树填写桌面应用表单

使用方法：

```bash
# 在 Reasonix 会话中
/skill app-control "打开记事本并输入 Hello World"
```

---

## 🔒 安全钩子

本包自带预配置的安全钩子：

- **before-action.sh** —— 拦截破坏性操作（删除、格式化、密码框），请求用户确认
- **after-action.sh** —— 记录操作日志并截图保存到 `memory/`，便于回放/调试

---

## 🧠 记忆与学习

插件会记住：

- `memory/preferences.md` —— 你偏好的应用、常用 UI 元素标识符
- `memory/operation-log.md` —— 带时间戳的操作历史
- `memory/screenshots/` —— 操作后截图，用于调试

---

## 📋 示例对话

```
用户：打开记事本，输入"Hello from Reasonix Computer Use"，然后保存。

Agent：我来帮忙。先看看有哪些工具可用...

→ computer_app_launch("C:\Windows\System32\notepad.exe")
→ computer_window_activate(title="无标题 - 记事本")
→ computer_keyboard_type("Hello from Reasonix Computer Use")
→ computer_keyboard_press("Ctrl+S")
→ computer_keyboard_type("hello.txt")
→ computer_keyboard_press("Enter")
→ computer_screenshot(annotate=true)  ← 验证保存是否成功

Agent：完成！文件已保存。
```

---

## 🛡️ 安全边界

默认情况下，本插件**禁止**以下操作（除非用户明确授权）：

- 操作密码输入框
- 删除或格式化操作
- 系统重启/关机
- 修改系统注册表或偏好设置
- 任何应用中的付款按钮

在 `hooks/before-action.sh` 中自定义配置。

---

## 🗺️ 路线图

- [x] Windows v1 — 截图、鼠标、键盘、UI 树、应用发现
- [ ] macOS 版本 — 通过内嵌 Swift 助手使用 AX API
- [ ] Linux 版本 — 通过 D-Bus 纯 Python 调用 AT-SPI
- [ ] 更多技能 — IDE 调试、系统设置、多应用工作流
- [ ] 通过 `latest.json` 实现插件自动更新

---

## 📄 许可证

MIT — 与 Reasonix 核心相同。

由 Reasonix 社区为 Reasonix 生态构建。
