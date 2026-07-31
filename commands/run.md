---
description: 显式启动一次隔离的 Windows Computer Use 任务
argument-hint: <任务>
---

你已经位于 `/computer-use:run` 展开的执行上下文中。上下文中已注入：

1. **任务计划** — 按 abc 编号的自动化步骤序列，包含难度评估和系统画像状态
2. **系统画像状态** — 如果缺失已自动生成 `system.md` + `system-index.json`
3. **依赖状态** — 如果缺少会自动提示安装
4. **视觉路由** — 当前 vision capability 状态

**执行流程**：
- (a) 初始化：检查上下文中"Computer Use 任务计划"，按步骤顺序执行
- (b) 如提示缺少依赖，先用 `computer_system(operation="setup", confirmed=true)` 安装
- (c) 如需系统画像，先查看 `memory/system.md`
- (d) 按照计划中的 (a)(b)(c)... 步骤依次执行，每步完成验证后再继续
- (e) 任务结束后自动清理截图缓存并汇报

禁止再次调用 `slash_command`、`/computer-use:run` 或 `/computer-use:agent:operator`，
也不要枚举命令/Skill 注册表。Operator 契约已经由插件注入。仅当
`$ARGUMENTS` 非空时继续；缺少任务内容时先询问用户。

不要改写用户指定的双击、右键、中键、拖动、滚轮或指定应用等必要方法。
Operator 返回后原样汇总其已验证证据、阻断和实际方法，不得把"界面变化"推断为完成。