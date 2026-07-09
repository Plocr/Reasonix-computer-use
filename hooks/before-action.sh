#!/bin/bash
# before-action.sh — Reasonix Computer Use 插件的操作前安全钩子
#
# 该钩子在每次 computer use 工具执行前调用。
# 可以：
#   - 阻止被拒绝的操作（返回退出码 1）
#   - 记录操作用于审计
#   - 允许操作继续（返回退出码 0）
#
# 可用的环境变量：
#   REASONIX_TOOL_NAME    — 正在调用的工具（如 "computer_mouse_click"）
#   REASONIX_TOOL_ARGS    — 传递给工具的 JSON 参数
#   REASONIX_WINDOW_TITLE — 当前窗口标题（如果已知）
#   REASONIX_SCREENSHOT_BEFORE — 操作前截图路径（如果可用）

TOOL_NAME="${REASONIX_TOOL_NAME:-unknown}"
TOOL_ARGS="${REASONIX_TOOL_ARGS:-{}}"
WINDOW_TITLE="${REASONIX_WINDOW_TITLE:-unknown}"

# ─── 配置 ───────────────────────────────────────────────────────────
# 
# 拒绝列表：危险操作的模式（不区分大小写）
# 使用 grep -E 的单词边界来避免误匹配。
# 具体来说：
#   - rm\s+-rf : 匹配 "rm -rf" 但不匹配 "remove" 或 "perform"
#   - format\b : 匹配 "format" 但不匹配 "formatting" 或 "reformat"
#   - delete\b : 匹配 "delete" 但不匹配 "canceldelete" 或 "undelete"
#   - shutdown\b : 匹配 "shutdown" 但不匹配 "shutdownprocess"
#   - reboot\b : 匹配 "reboot" 但不匹配 "rebootprocess"
#   - sudo\s : 匹配 "sudo " 但不匹配 "sudoes"
#
# 单词边界（或后缀模式）防止意外匹配。

DENY_PATTERNS=(
    "rm\s+-rf"
    "rm\s+-fr"
    "format\b"
    "delete\b"
    "shutdown\b"
    "reboot\b"
    "reg\s+delete"
    "taskkill"
    "sudo\s+"
)

# 警告列表：需要确认但不阻止
WARN_PATTERNS=(
    "uninstall\b"
    "send.*mail"
    "submit.*form"
    "purchase\b"
    "payment\b"
)

# ─── 安全检查 ────────────────────────────────────────────────────────────

log() {
    echo "[computer-use hook] $(date '+%Y-%m-%d %H:%M:%S') $*" >&2
}

deny() {
    log "DENY: $1"
    echo "BLOCKED: $1" >&2
    exit 1
}

warn() {
    log "WARN: $1"
    echo "WARNING: $1" >&2
}

allow() {
    log "ALLOW: tool=$TOOL_NAME window=$WINDOW_TITLE"
    exit 0
}

# 检查工具参数是否包含被拒绝的操作
check_args() {
    local args="$1"
    local pattern
    
    for pattern in "${DENY_PATTERNS[@]}"; do
        if echo "$args" | grep -qiE "$pattern"; then
            deny "检测到危险操作: $pattern"
        fi
    done
    
    for pattern in "${WARN_PATTERNS[@]}"; do
        if echo "$args" | grep -qiE "$pattern"; then
            warn "潜在破坏性操作: $pattern"
        fi
    done
}

# 检查当前窗口是否涉及敏感上下文
check_window_context() {
    local title="$1"
    
    # 如果窗口标题涉及管理员/提升上下文，发出警告
    if echo "$title" | grep -qiE "(admin|elevated|uac|user account control|system configuration)"; then
        warn "操作涉及管理员/提升上下文: $title"
    fi
}

# ─── 主逻辑 ──────────────────────────────────────────────────────────────

# 始终允许这些只读工具
case "$TOOL_NAME" in
    computer_screenshot|computer_window_list|computer_ui_tree|computer_find_element|computer_app_list)
        allow
        ;;
esac

# 检查参数是否有危险操作
check_args "$TOOL_ARGS"

# 检查窗口上下文
check_window_context "$WINDOW_TITLE"

# 所有检查通过 — 允许操作
allow
