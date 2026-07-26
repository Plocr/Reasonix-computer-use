---
description: 手动运行隔离、可验证的 Computer Use Operator
argument-hint: <任务>
---

这是 Reasonix 1.17 原生 manifest 尚未导出 `agents` 时的兼容入口。当前命令已经展开，禁止再次
调用 `slash_command`、本命令或 `/computer-use:run`，也不要枚举命令/Skill 注册表。仅处理
`$ARGUMENTS`。契约已由插件注入，禁止使用文件工具读取 `agents/operator.md`；严格应用其执行规则：
工具限于 `mcp__computer-use__*`、配置存在时精确的
`mcp__mimo-mcp__understand_image`、Skill 和 AskUserQuestion；先补齐缺失参数；保留用户指定的应用和鼠标/键盘方法；UIA、相关 OCR、
视觉按任务充分性选择；所有必要步骤必须有语义证据；blocked 后停止；最终返回必要方法、
实际方法、验证证据和未完成事项。不得使用 Shell、Python、文件工具或其他代理替代 GUI 步骤。
