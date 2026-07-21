---
description: 使用 Computer Use 操作电脑桌面（打开应用、点击、输入等）
argument-hint: <任务描述，如"打开记事本，输入hello world">
---

正在操作电脑，请稍候...

用户任务：$ARGUMENTS

请按以下流程执行：

1. **启动/聚焦应用**：`computer_app(operation="launch", query="应用名")`
2. **截图观察**：`computer_state(window_id, goal="目标")` 获取当前界面截图
3. **执行操作**：`computer_action` 点击/输入/按键
4. **验证结果**：再次 `computer_state` 确认完成

注意事项：
- 每次操作前确保目标窗口在前台（`computer_app(operation="focus")`）
- 截图后仔细分析再决定下一步
- 坐标使用窗口内物理像素（`coordinate_space: "window"`）
- 遇到 `blocked=true` 立即停止并报告
