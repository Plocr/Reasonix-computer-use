# Reasonix Computer Use — 用户指南

> 一个完整的 Windows GUI 自动化插件包

---

## 📋 目录

1. [快速开始](#快速开始)
2. [安装](#安装)
3. [可用工具](#可用工具)
4. [使用技能](#使用技能)
5. [安全与权限](#安全与权限)
6. [记忆与学习](#记忆与学习)
7. [故障排除](#故障排除)

---

## 🚀 快速开始

安装完成后，你可以立即开始使用：

```
用户：截一张桌面截图

Agent：[调用 computer_screenshot] 完成，保存到 C:\Users\你\AppData\Local\Temp\reasonix_screenshot_1720612345.png
```

```
用户：打开计算器，按 7，再按 +，再按 9，再按 =，告诉我结果

Agent：[调用 computer_app_launch、computer_window_activate，然后按键]
       结果是 16。
```

```
用户：找到记事本里的所有按钮并点击"文件"

Agent：[调用 computer_ui_tree → computer_find_element → computer_mouse_click]
       已点击"文件"菜单。子菜单已展开。
```

---

## 🔧 安装

### 前置条件

- Windows 10 或 11
- Python 3.10+ 及 pip
- Reasonix Desktop 1.16+（带插件管理界面）或 Reasonix CLI

### 步骤 1：安装插件包

```bash
git clone https://github.com/你的ID/reasonix-computer-use.git
cd reasonix-computer-use
pip install -e .
```

### 步骤 2：安装到 Reasonix

**方式 A — Desktop UI：**

打开 Reasonix Desktop → 插件 → 安装插件 → **本地目录** → 选 `reasonix-computer-use/` 文件夹。

**方式 B — 编辑 config.toml：**

```toml
[[plugins]]
name    = "computer-use"
command = "python"
args    = ["-m", "reasonix_computer_use.mcp_server"]
type    = "stdio"
```

### 步骤 3：验证

在 Reasonix 会话中运行 `/mcp`，应能看到 `reasonix-computer-use` 显示为已连接。

---

## 🔧 可用工具

### 截图与视觉

| 工具 | 说明 | 何时使用 |
|---|---|---|
| `computer_screenshot` | 全屏/窗口/区域截图 | 需要验证 UI 状态、获取操作前后证据 |
| `computer_window_list` | 列出所有窗口（hwnd、标题、坐标） | 需要找特定窗口 |
| `computer_ui_tree` | 获取 UIAutomation 树 | 操作前需要查看元素 |
| `computer_find_element` | 按名称/ID/类型查找元素 | 有目标元素，需要获取坐标 |

### 鼠标

| 工具 | 说明 | 何时使用 |
|---|---|---|
| `computer_mouse_move` | 移动到绝对坐标 | 点击前需要悬停 |
| `computer_mouse_click` | 左键/右键/中键单击或双击 | 点击按钮、链接、菜单 |
| `computer_mouse_scroll` | 向上/向下滚动 N 行 | 表单、列表、网页 |

### 键盘

| 工具 | 说明 | 何时使用 |
|---|---|---|
| `computer_keyboard_type` | 逐字输入（支持 Unicode/中文） | 填写文本框、在编辑器中输入 |
| `computer_keyboard_press` | 按键或组合键 | 快捷键（Ctrl+C、Enter、Esc） |

### 应用控制

| 工具 | 说明 | 何时使用 |
|---|---|---|
| `computer_app_list` | 列出已安装应用（不扫描磁盘！） | 查找应用可执行文件路径 |
| `computer_app_launch` | 按路径或名称启动应用 | 启动应用程序 |
| `computer_window_activate` | 置顶窗口 | 切换到已运行的应用 |

---

## 🎯 使用技能

本包自带两个技能，教 Reasonix 如何在工作流中使用工具。

### app-control

**触发条件：** 用户想控制桌面应用（非浏览器内）。

**示例提示：**
- "打开文件资源管理器并导航到 D:\Downloads"
- "打开外观设置，将主题改为深色"
- "关闭当前记事本窗口"

**工作流：** 应用列表 → 启动 → 窗口置顶 → UI 树 → 查找元素 → 交互 → 截图

### form-fill

**触发条件：** 用户想在桌面应用中填写表单。

**示例提示：**
- "在注册对话框中填写：姓名=张三，邮箱=("<EMAIL>)"
- "在设置对话框中勾选'开机自启'并点击确定"

**工作流：** UI 树 → 找到输入框 → 聚焦 → 输入 → 找到提交按钮 → 点击 → 验证

---

## 🔒 安全与权限

### 管理员权限

大部分操作**不需要管理员权限**。例外情况：

| 场景 | 会发生什么 |
|---|---|
| 操作任务管理器、系统设置 | 出现 UAC 对话框 → Agent 暂停 → 询问用户 |
| 点击触发 UAC 的按钮 | Agent 检测到对话框 → 请求确认 |
| `computer_app_launch` 启动仅管理员应用 | Shell 弹出"以管理员身份运行"提示 → Agent 等待 |

### 被拒绝的操作

默认情况下，`before-action.sh` 钩子在无确认情况下拦截以下操作：

- `rm -rf`、`format`、`del /f`、`shutdown`、`reboot`
- `reg delete`、`taskkill`、`sudo`、`passwd`
- 任何对密码输入框的操作

钩子会在以下操作前发出警告：

- 删除、移除、卸载、格式化
- 发送邮件、提交、购买、付款

编辑 `hooks/before-action.sh` 可自定义。

### 速率限制

为了防止滥用：

- 鼠标/键盘事件之间最少间隔 50ms
- 窗口操作之间最少间隔 200ms
- 截图限制为每 2 秒 1 次（在钩子中）

---

## 🧠 记忆与学习

### 记住什么

- `memory/preferences.md` — 你偏好的应用、默认设置
- `memory/operation-log.md` — 每次操作的带时间戳日志表
- `memory/screenshots/` — 操作后截图，用于调试

### 记忆如何帮助

运行几个会话后，插件将学会：

> "当用户说打开 Visual Studio 时，可执行文件路径是 `C:\Program Files\Microsoft Visual Studio\2022\Common7\IDE\devenv.exe`"
>
> "GitHub Desktop 中的提交按钮的 automation_id 是 `CommitButton`"

这样后续交互所需的截图/UI 树调用更少——更快、更省成本。

---

## 🔧 故障排除

### "comtypes not installed" 错误

```bash
pip install comtypes
```

### "Window not found" 错误

Windows 可能有多个同名不可见窗口。尝试：

1. `computer_window_list`，参数 `visible_only: false` 以查看所有窗口
2. 使用 `hwnd` 代替标题进行精确匹配
3. 使用 `method: "class"` 按窗口类名匹配

### "截图是黑色的"

某些应用（DRM 保护视频、硬件加速窗口）会阻止截图。尝试：

1. `computer_mouse_click` 目标应用先给它焦点
2. 使用 `region` 截图代替全屏
3. 检查应用是否有"阻止截图"设置

### 键盘输入没有到达目标应用

1. 确保目标窗口已激活：`computer_window_activate`
2. 激活后等待 300ms 再输入
3. 部分游戏和安全应用会阻止模拟输入——这是系统设计

### UAC 提示阻止自动化

UAC（用户账户控制）对话框**按设计**不允许模拟输入。
Agent 会检测到并请用户手动点击。

---

## 🛠️ 扩展插件

### 添加新技能

在 `skills/` 中创建一个 markdown 文件：

```markdown
---
description: 这个技能做什么。描述触发条件。
---

# 你的技能名称

分步工作流...
```

### 添加新工具

在 `reasonix_computer_use/` 中创建一个新模块：

```python
from mcp_server import register_tool

@register_tool(
    name="computer_your_tool",
    description="它做什么...",
    schema={"type": "object", "properties": {...}}
)
async def your_tool(args: dict) -> str:
    # 实现代码
    return '{"status": "ok"}'
```

然后更新 `reasonix_computer_use/tools.py` 导入你的模块。

---

## 📄 许可证

MIT — 与 Reasonix 核心相同。
