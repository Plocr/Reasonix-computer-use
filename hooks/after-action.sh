#!/bin/bash
# after-action.sh — Reasonix Computer Use 插件的操作后日志与记忆钩子
#
# 该钩子在每次 computer use 工具执行后调用。
# 截图清理策略：
# - 操作成功：删除涉及截图，避免臃肿
# - 操作失败：保留最近截图供调试，清理超过 60 分钟的旧截图
# - 每次执行后：清理超过 60 分钟的历史截图
#
# 可用的环境变量：
#   REASONIX_TOOL_NAME    — 刚刚调用的工具
#   REASONIX_TOOL_ARGS    — 传递的 JSON 参数
#   REASONIX_TOOL_RESULT  — 工具执行结果
#   REASONIX_EXIT_CODE    — 工具退出码（0 = 成功）
#   REASONIX_WINDOW_TITLE — 当前窗口标题
#   REASONIX_SCREENSHOT_BEFORE — 操作前截图路径
#   REASONIX_MEMORY_DIR   — memory/ 目录路径

TOOL_NAME="${REASONIX_TOOL_NAME:-unknown}"
TOOL_ARGS="${REASONIX_TOOL_ARGS:-{}}"
EXIT_CODE="${REASONIX_EXIT_CODE:-0}"
MEMORY_DIR="${REASONIX_MEMORY_DIR:-./memory}"
SCREENSHOT_DIR="${MEMORY_DIR}/screenshots"
LOG_FILE="${MEMORY_DIR}/operation-log.md"
SCREENSHOT_BEFORE="${REASONIX_SCREENSHOT_BEFORE:-}"

# ─── 配置 ───────────────────────────────────────────────────────────

# 截图保留时间（分钟），超过此时间的截图自动清理
SCREENSHOT_KEEP_MINUTES=60

# ─── 辅助函数 ─────────────────────────────────────────────────────────────────

log() {
    echo "[computer-use hook] $(date '+%Y-%m-%d %H:%M:%S') $*" >&2
}

ensure_dir() {
    mkdir -p "$1"
}

# ─── 截图清理 ──────────────────────────────────────────────────────────

cleanup_old_screenshots() {
    if [ ! -d "$SCREENSHOT_DIR" ]; then
        return
    fi
    
    local cleaned=0
    local keep_seconds=$((SCREENSHOT_KEEP_MINUTES * 60))
    local now=$(date +%s)
    
    for f in "$SCREENSHOT_DIR"/screenshot_*.png; do
        [ -f "$f" ] || continue
        local mtime=$(stat -c %Y "$f" 2>/dev/null || stat -f %m "$f" 2>/dev/null || echo "0")
        local age=$((now - mtime))
        if [ "$age" -gt "$keep_seconds" ]; then
            rm -f "$f" 2>/dev/null
            cleaned=$((cleaned + 1))
        fi
    done
    
    if [ "$cleaned" -gt 0 ]; then
        log "已清理 $cleaned 张超过 ${SCREENSHOT_KEEP_MINUTES} 分钟的旧截图"
    fi
}

# ─── 主逻辑 ──────────────────────────────────────────────────────────────

LAST_SCREENSHOT=""

if [ "$EXIT_CODE" = "0" ]; then
    # 操作成功：不保留截图，直接清理相关截图
    if [ -n "$SCREENSHOT_BEFORE" ] && [ -f "$SCREENSHOT_BEFORE" ]; then
        rm -f "$SCREENSHOT_BEFORE" 2>/dev/null
        log "操作成功，已清理操作前截图: $SCREENSHOT_BEFORE"
    fi
else
    # 操作失败：保留截图供调试，生成时间戳命名
    timestamp=$(date '+%Y%m%d_%H%M%S' 2>/dev/null)
    LAST_SCREENSHOT="${SCREENSHOT_DIR}/failed_${TOOL_NAME}_${timestamp}.png"
    
    # 如果存在操作前截图，保留它
    if [ -n "$SCREENSHOT_BEFORE" ] && [ -f "$SCREENSHOT_BEFORE" ]; then
        # 重命名为失败截图
        mv "$SCREENSHOT_BEFORE" "$LAST_SCREENSHOT" 2>/dev/null
        log "操作失败，保留截图供调试: $LAST_SCREENSHOT"
    fi
fi

# 每次执行后都清理超过保留时间的旧截图
cleanup_old_screenshots

# ─── 操作日志 ─────────────────────────────────────────────────────────────────

ensure_dir "$MEMORY_DIR"

if [ ! -f "$LOG_FILE" ]; then
    echo "# Computer Use 操作日志" > "$LOG_FILE"
    echo "" >> "$LOG_FILE"
    echo "| 时间戳 | 工具 | 退出码 | 保留截图 |" >> "$LOG_FILE"
    echo "|--------|------|--------|----------|" >> "$LOG_FILE"
fi

timestamp=$(date '+%Y-%m-%d %H:%M:%S' 2>/dev/null)
echo "| $timestamp | $TOOL_NAME | $EXIT_CODE | ${LAST_SCREENSHOT:-无} |" >> "$LOG_FILE"

exit 0
