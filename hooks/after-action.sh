#!/bin/bash
# after-action.sh — Reasonix Computer Use 插件的操作后日志与记忆钩子
#
# 该钩子在每次 computer use 工具执行后调用。
# 可以：
# - 记录操作和结果，用于审计
# - 捕获操作后截图，用于回放/调试
# - 更新记忆文件，供后续参考
#
# 可用的环境变量：
#   REASONIX_TOOL_NAME    — 刚刚调用的工具
#   REASONIX_TOOL_ARGS    — 传递的 JSON 参数
#   REASONIX_TOOL_RESULT  — 工具执行结果（成功时为 "ok"）
#   REASONIX_EXIT_CODE    — 工具退出码（0 = 成功）
#   REASONIX_WINDOW_TITLE — 当前窗口标题
#   REASONIX_SCREENSHOT_BEFORE — 操作前截图路径
#   REASONIX_MEMORY_DIR   — memory/ 目录路径（用于保存日志）
#   REASONIX_SESSION_ID   — 当前会话标识符（如果可用）

TOOL_NAME="${REASONIX_TOOL_NAME:-unknown}"
TOOL_ARGS="${REASONIX_TOOL_ARGS:-{}}"
EXIT_CODE="${REASONIX_EXIT_CODE:-0}"
MEMORY_DIR="${REASONIX_MEMORY_DIR:-./memory}"
SCREENSHOT_DIR="${MEMORY_DIR}/screenshots"
LOG_FILE="${MEMORY_DIR}/operation-log.md"

# ─── 配置 ───────────────────────────────────────────────────────────

# ─── 辅助函数 ─────────────────────────────────────────────────────────────────

log() {
    echo "[computer-use 钩子] $(date '+%Y-%m-%d %H:%M:%S') $*" >&2
}

ensure_dir() {
    mkdir -p "$1"
}

# 使用 PID + 计数器生成唯一的截图文件名，避免碰撞
screenshot_path() {
    # 使用 $$（PID）+ 秒 + 纳秒确保唯一性
    # 备用：如果不支持 %N（macOS），使用 PID + 随机数
    local timestamp
    timestamp=$(date '+%Y%m%d_%H%M%S' 2>/dev/null)
    local nano=""
    # 尝试纳秒，回退到基于 PID 的唯一性
    nano=$(date '+%N' 2>/dev/null)
    if [ "$nano" = "%N" ] || [ -z "$nano" ]; then
        nano="$$_$RANDOM"
    fi
    echo "${SCREENSHOT_DIR}/操作后_${TOOL_NAME}_${timestamp}_${nano}.png"
}

# ─── 截图捕获 ──────────────────────────────────────────────────────

capture_screenshot() {
    local output_path="$1"
    ensure_dir "$(dirname "$output_path")"
    
    # 使用 Python 截屏并保存到指定路径
    python3 -c "
import sys
try:
    import pyautogui
    screenshot = pyautogui.screenshot()
    screenshot.save(sys.argv[1], 'PNG')
    print('OK')
except Exception as e:
    print(f'Error: {e}', file=sys.stderr)
    sys.exit(1)
" "$output_path" 2>/dev/null
    
    if [ $? -eq 0 ] && [ -f "$output_path" ]; then
        log "截图已保存: $output_path"
        echo "$output_path"
    else
        log "截图捕获失败"
        echo ""
    fi
}

# ─── 记忆日志 ──────────────────────────────────────────────────────────

update_operation_log() {
    local timestamp
    timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    
    ensure_dir "$MEMORY_DIR"
    
    # 如果日志文件不存在，创建带标题的文件
    if [ ! -f "$LOG_FILE" ]; then
        echo "# Computer Use 操作日志" > "$LOG_FILE"
        echo "" >> "$LOG_FILE"
        echo "| 时间戳 | 工具 | 参数 | 结果 | 退出码 | 窗口 | 截图 |" >> "$LOG_FILE"
        echo "|--------|------|------|------|--------|------|------|" >> "$LOG_FILE"
    fi
    
    # 转义工具参数中的管道符
    local safe_args
    safe_args=$(echo "$TOOL_ARGS" | sed 's/|/\\|/g')
    
    # 追加日志条目
    echo "| $timestamp | $TOOL_NAME | $safe_args | ${REASONIX_TOOL_RESULT:-ok} | $EXIT_CODE | ${REASONIX_WINDOW_TITLE:-unknown} | ${LAST_SCREENSHOT:-} |" >> "$LOG_FILE"
    
    log "操作已记录到 $LOG_FILE"
}

# ─── 主逻辑 ──────────────────────────────────────────────────────────────

LAST_SCREENSHOT=""

# 字符串比较，避免 bash 整数问题
if [ "$EXIT_CODE" = "0" ]; then
    screen_path=$(screenshot_path)
    if [ -n "$screen_path" ]; then
        LAST_SCREENSHOT=$screen_path
    fi
fi

# 更新操作日志
update_operation_log

exit 0
