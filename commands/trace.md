---
description: 查看或导出脱敏 Computer Use 轨迹
argument-hint: [status|list|export TRACE_ID DESTINATION]
---

使用 `computer_system(operation="trace")` 处理 `$ARGUMENTS`。默认执行 `status`；`list` 返回最近
轨迹；`export` 必须向用户确认目标路径后传 `confirmed:true`。说明导出内容不含截图、输入正文、
剪贴板、密码或完整用户路径。
