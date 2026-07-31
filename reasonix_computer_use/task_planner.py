"""
Task Planner — analyzes user tasks and generates structured execution plans
for the /computer-use:run command.

Produces a step-by-step plan with difficulty assessment, determines whether
system profiling is needed (底层判断), and identifies dependency requirements.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ── Plan model ──────────────────────────────────────────────────────────────

@dataclass
class TaskStep:
    """A single step in the task plan."""
    id: str  # "a", "b", "c", etc.
    description: str
    tool: str  # "computer_app", "computer_state", "computer_action", "computer_system"
    action: str  # "launch", "observe", "click", "type", etc.
    target: str = ""
    verification: str = ""  # How to verify this step succeeded


@dataclass
class TaskPlan:
    """A complete task plan."""
    goal: str
    difficulty: str  # "simple", "medium", "complex"
    needs_system_profile: bool  # Whether 系统画像 is required
    needs_dependencies: list[str] = field(default_factory=list)  # Missing deps
    steps: list[TaskStep] = field(default_factory=list)
    estimated_steps: int = 0

    def to_context_string(self) -> str:
        """Render the plan as a human-readable context block for the Agent."""
        lines = [
            "## Computer Use 任务计划",
            "",
            f"**目标**: {self.goal}",
            f"**难度**: {self.difficulty}",
            f"**预计步骤数**: {self.estimated_steps}",
            "",
        ]

        if self.needs_system_profile:
            lines.append("> ⚠️ 系统画像不存在或已过期，已自动生成。请先检查 `memory/system.md`。")
            lines.append("")

        if self.needs_dependencies:
            lines.append(f"> ⚠️ 缺少依赖: {', '.join(self.needs_dependencies)}，已触发安装。请等待安装完成后继续。")
            lines.append("")

        lines.append("### 执行步骤")
        lines.append("")
        step_labels = "abcdefghijklmnopqrstuvwxyz"
        for i, step in enumerate(self.steps):
            label = step_labels[i] if i < len(step_labels) else str(i)
            lines.append(f"**({label})** {step.description}")
            lines.append(f"  - 工具: `{step.tool}` → `{step.action}`")
            if step.target:
                lines.append(f"  - 目标: {step.target}")
            if step.verification:
                lines.append(f"  - 验证: {step.verification}")
            lines.append("")

        # Execution guidance
        lines.append("### 执行指引")
        lines.append("")
        lines.append("1. 按步骤顺序执行，每步完成后验证再进入下一步")
        lines.append("2. `computer_app(operation=\"launch\")` 启动应用")
        lines.append("3. `computer_state(mode=\"observe\")` 观察屏幕，获取 ELEMENT_REF")
        lines.append("4. `computer_action(actions=[...])` 执行鼠标/键盘操作")
        lines.append("5. 每一步后检查 `task_completion.verified` 和 `task_completion.pending`")
        lines.append("6. 遇到 `blocked: true` 或连续两次失败立即停止并报告")
        lines.append("")

        return "\n".join(lines)


# ── Task analysis ───────────────────────────────────────────────────────────

# Keywords that indicate a need for system profiling (底层操作)
_SYSTEM_KEYWORDS = [
    # File system operations
    "保存", "另存为", "导出", "下载", "文件", "文件夹", "桌面", "文档",
    "save", "export", "download", "file", "folder", "desktop", "document",
    # System-level actions
    "系统", "设置", "控制面板", "属性", "分辨率", "显示器",
    "system", "settings", "control panel", "properties", "display",
    # Multi-app coordination
    "打开.*并", "然后", "之后", "复制到", "移动到", "拖到",
    "open.*and", "then", "after", "copy to", "move to", "drag to",
    # Complex operations
    "编辑", "修改", "替换", "批量", "转换",
    "edit", "modify", "replace", "batch", "convert",
]

# Task difficulty heuristics
_SIMPLE_ACTIONS = {
    "打开", "启动", "关闭", "最小化", "最大化", "全屏",
    "open", "launch", "close", "minimize", "maximize", "fullscreen",
    "按", "点击", "输入", "搜索",
    "press", "click", "type", "search",
}
_MEDIUM_ACTIONS = {
    "保存", "另存为", "截图", "复制", "粘贴", "删除",
    "save", "screenshot", "copy", "paste", "delete",
    "导航", "跳转", "滚动",
    "navigate", "scroll",
}
_COMPLEX_ACTIONS = {
    "编辑", "修改", "填写", "表单", "注册", "登录",
    "edit", "modify", "fill", "form", "register", "login",
    "安装", "配置", "设置", "卸载",
    "install", "configure", "uninstall",
    "对比", "比较", "分析", "合并",
    "compare", "analyze", "merge",
}


def _needs_system_profile(task: str) -> bool:
    """Determine if the task requires a system profile (底层判断)."""
    task_lower = task.lower()
    for pattern in _SYSTEM_KEYWORDS:
        if re.search(pattern, task_lower):
            return True
    return False


def _assess_difficulty(task: str) -> tuple[str, int]:
    """Assess task difficulty and estimate step count.

    Returns (difficulty_label, estimated_steps).
    """
    task_lower = task.lower()
    scores: dict[str, int] = {"simple": 0, "medium": 0, "complex": 0}

    for keyword in _SIMPLE_ACTIONS:
        if keyword in task_lower:
            scores["simple"] += 1
    for keyword in _MEDIUM_ACTIONS:
        if keyword in task_lower:
            scores["medium"] += 1
    for keyword in _COMPLEX_ACTIONS:
        if keyword in task_lower:
            scores["complex"] += 1

    # Additional signals
    if "并" in task or "然后" in task or "之后" in task or "and" in task_lower:
        scores["complex"] += 3  # Multi-step
    if len(task) > 50:
        scores["medium"] += 1
    if len(task) > 120:
        scores["complex"] += 2
    if "所有" in task or "全部" in task or "all" in task_lower:
        scores["complex"] += 1

    if scores["complex"] >= 3:
        return ("complex", max(3, scores["complex"]))
    if scores["medium"] >= 2:
        return ("medium", max(2, scores["medium"]))
    return ("simple", 1)


def _parse_actions(task: str) -> list[dict[str, str]]:
    """Parse the task string into a sequence of actions.

    Returns a list of {'action': ..., 'target': ..., 'tool': ...}.
    """
    actions: list[dict[str, str]] = []
    task_lower = task.lower()

    # Detect app launch
    launch_patterns = [
        (r"打开\s*['\"]?([^'\"\s，,。]+)['\"]?", "launch"),
        (r"启动\s*['\"]?([^'\"\s，,。]+)['\"]?", "launch"),
        (r"运行\s*['\"]?([^'\"\s，,。]+)['\"]?", "launch"),
        (r"open\s+['\"]?([^'\"\s，,.]+)['\"]?", "launch"),
        (r"launch\s+['\"]?([^'\"\s，,.]+)['\"]?", "launch"),
    ]
    for pattern, action in launch_patterns:
        match = re.search(pattern, task_lower)
        if match:
            app_name = match.group(1)
            actions.append({
                "action": action,
                "target": app_name,
                "tool": "computer_app",
            })
            break

    # Detect click actions
    click_patterns = [
        (r"(?:点击|单击|按下)\s*['\"]?([^'\"\s，,。]+)['\"]?", "click"),
        (r"click\s+['\"]?([^'\"\s，,.]+)['\"]?", "click"),
    ]
    for pattern, action in click_patterns:
        for match in re.finditer(pattern, task_lower):
            target = match.group(1)
            # Skip if it's just an app name (already handled by launch)
            if target in [a.get("target", "") for a in actions if a.get("action") == "launch"]:
                continue
            actions.append({
                "action": action,
                "target": target,
                "tool": "computer_action",
            })

    # Detect type/input actions
    type_patterns = [
        (r"(?:输入|键入|填写)\s*['\"]?([^'\"\s，,。]+)['\"]?", "type"),
        (r"type\s+['\"]?([^'\"\s，,.]+)['\"]?", "type"),
    ]
    for pattern, action in type_patterns:
        for match in re.finditer(pattern, task_lower):
            actions.append({
                "action": action,
                "target": match.group(1),
                "tool": "computer_action",
            })

    # Detect file/save actions
    if re.search(r"(?:保存|另存为|save)", task_lower):
        actions.append({
            "action": "save",
            "target": "文件",
            "tool": "computer_system",
        })

    # Default: if no specific actions were parsed, add observe + execute steps
    if not actions:
        if any(kw in task_lower for kw in ["打开", "启动", "open", "launch"]):
            # Just a simple app launch
            pass
        else:
            actions.append({
                "action": "observe",
                "target": "屏幕",
                "tool": "computer_state",
            })
            actions.append({
                "action": "execute",
                "target": task[:80],
                "tool": "computer_action",
            })

    return actions


def generate_plan(task: str) -> TaskPlan:
    """Generate a task plan from a user's task description.

    Args:
        task: The raw task string (e.g. "打开记事本并输入Hello World").

    Returns:
        A TaskPlan with difficulty, dependencies, and steps.
    """
    if not task or not task.strip():
        return TaskPlan(
            goal="(空任务)",
            difficulty="simple",
            needs_system_profile=False,
            steps=[],
            estimated_steps=0,
        )

    task = task.strip()
    difficulty, estimated = _assess_difficulty(task)
    needs_profile = _needs_system_profile(task)
    parsed = _parse_actions(task)

    # Build steps
    steps: list[TaskStep] = []
    step_id = 0

    # Step 0: Always check/refresh system profile first if needed
    if needs_profile:
        steps.append(TaskStep(
            id="a",
            description="初始化环境：检查并生成系统画像（system.md + system-index.json）",
            tool="computer_system",
            action="profile",
            verification="系统画像文件已存在且不早于 24 小时",
        ))
        step_id += 1

    # Step 1: Observe screen to establish baseline
    steps.append(TaskStep(
        id=chr(ord("a") + step_id),
        description="观察当前屏幕状态，建立元素索引",
        tool="computer_state",
        action="observe",
        verification="获取到 ELEMENT_REF 列表，确定目标元素位置",
    ))
    step_id += 1

    # Step 2-N: Execute parsed actions
    for action_info in parsed:
        steps.append(TaskStep(
            id=chr(ord("a") + step_id),
            description=f"执行{action_info['action']}操作：{action_info.get('target', '')}",
            tool=action_info["tool"],
            action=action_info["action"],
            target=action_info.get("target", ""),
            verification="操作成功执行，界面状态发生变化",
        ))
        step_id += 1

    # Final step: Verify completion
    steps.append(TaskStep(
        id=chr(ord("a") + step_id),
        description="验证任务完成：确认最终状态符合预期",
        tool="computer_state",
        action="observe",
        verification="task_completion.verified=true",
    ))

    return TaskPlan(
        goal=task,
        difficulty=difficulty,
        needs_system_profile=needs_profile,
        steps=steps,
        estimated_steps=len(steps),
    )


def plan_to_json(plan: TaskPlan) -> str:
    """Serialize a TaskPlan to JSON for storage/logging."""
    return json.dumps({
        "goal": plan.goal,
        "difficulty": plan.difficulty,
        "needs_system_profile": plan.needs_system_profile,
        "needs_dependencies": plan.needs_dependencies,
        "estimated_steps": plan.estimated_steps,
        "steps": [
            {
                "id": s.id,
                "description": s.description,
                "tool": s.tool,
                "action": s.action,
                "target": s.target,
                "verification": s.verification,
            }
            for s in plan.steps
        ],
    }, ensure_ascii=False, indent=2)
