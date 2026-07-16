---
description: 检查 Computer Use 插件、环境和 Reasonix 能力映射
argument-hint: [--live]
---

诊断 computer-use 插件。先调用 `computer_system(operation="diagnose")`，再运行静态的
`reasonix plugin doctor computer-use`。若当前 Reasonix 支持 capabilities 子命令，再运行
`reasonix doctor capabilities --json`。只有 `$ARGUMENTS` 明确包含 `--live` 时，才允许运行
`reasonix doctor capabilities --live --timeout 10s --json`。汇总错误和修复建议，不修改配置。
