---
description: 显式启动一次隔离的 Windows Computer Use 任务
argument-hint: <任务>
---

你已经位于 `/computer-use:run` 展开的执行上下文中。禁止再次调用 `slash_command`、
`/computer-use:run` 或 `/computer-use:agent:operator`，也不要枚举命令/Skill 注册表。Operator 契约
已经由插件注入，禁止使用文件工具读取 `agents/operator.md`；直接执行 `$ARGUMENTS`。仅当
`$ARGUMENTS` 非空时继续；缺少任务内容时
先询问用户。Reasonix 1.17 原生 manifest 未导出 Agent 时由本命令直接承担兼容映射，Hook 仍强制
相同工具白名单。不要改写用户指定的
双击、右键、中键、拖动、滚轮或指定应用等必要方法。Operator 返回后原样汇总其已验证
证据、阻断和实际方法，不得把“界面变化”推断为完成。
