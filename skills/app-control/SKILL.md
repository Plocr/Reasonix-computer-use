---
name: app-control
description: 使用低 token 的 UIA、OCR、视觉回退完成 Windows 桌面任务
---

# Windows Computer Use

桌面应用固定使用以下流程：

```text
computer_app(launch, query="应用名")
→ computer_state(goal)
→ computer_action(revision, actions, expect)
```

- 任一桌面工具若返回 `setup_required`，先说明依赖将安装到当前用户目录。用户确认后调用 `computer_system(operation="setup", params={"confirmed":true})`，再调用 `computer_system(operation="setup_status", params={"wait_seconds":20})`；等待由插件内部完成，禁止通过 Shell sleep 或 pip。
- 启动应用默认直接调用 `computer_app(operation="launch", query="应用名")`。只有名称歧义或启动失败时才先 search；不要把应用名填入 `app_id`。
- 保存 launch 返回的 `window_id`。聚焦和关闭时直接复用，不要为了找回窗口调用 `list_running`。
- 禁止转用 Bash 或 PowerShell 全盘查找应用。
- `computer_state` 已在内部按应用记忆、UIA、本地 OCR、窗口图片排序。不要自行调用图片理解工具。
- `source=uia` 使用 `click_ref`；`source=ocr` 使用 `click_text`；只有 `source=visual` 才使用当前 revision 的 `click_point`。
- `actions[]` 的动作名字段固定为 `type`，不是 `action` 或 `command`。示例：`{"type":"click_ref","ref":"e1"}`、`{"type":"type","text":"周杰伦"}`、`{"type":"press","keys":["ENTER"]}`。
- 视觉图片坐标是窗口内物理像素，`click_point` 默认 `coordinate_space=window`；不要把旧截图或桌面坐标用于当前 revision。
- 确定的输入、按键和点击合并到一次 `computer_action`，最多五步。执行器会验证并在失败处停止。
- `unchanged=true` 时不要重复观察或截图。`repeat_blocked` 时必须换策略或请求用户介入。
- 任一响应 `blocked=true` 时立即停止工具循环并报告最小阻断，不得转用 Shell、浏览器或新会话重复同一目标。
- `computer_system(command)` 仅用于单条只读诊断；不得用 PowerShell、SendKeys 或 Win32 脚本绕过 `computer_action`。
- 网页内容交给 `chrome-devtools`，本插件负责浏览器外壳、桌面和系统对话框。
- 密码、验证码、UAC、支付、删除、协议确认和不可逆操作由用户处理。
