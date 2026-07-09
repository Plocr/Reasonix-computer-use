---
description: 在 Windows 上控制非浏览器应用——文件资源管理器、系统设置、IDE 等
  当用户想要操作桌面应用、系统设置或原生软件时使用
---

# 应用控制（Windows 桌面）

通过 UI 自动化工具控制任何 Windows 桌面应用。

## 何时使用

- 任务在**浏览器外**（文件资源管理器、控制面板、Visual Studio、Word 等）
- 用户要求打开应用、修改系统设置或与原生程序交互
- `chrome-devtools` MCP 无法触及，因为不是网页

## 工作流

### 步骤 1：查找应用（如果需要）
调用 `computer_app_list`，传入搜索词，找到目标应用的可执行文件路径。

### 步骤 2：启动应用
调用 `computer_app_launch`，传入 `path`（可选 `args`）。

如果应用已在运行，跳过到步骤 3。

### 步骤 3：等待应用就绪
调用 `computer_window_list` 验证应用窗口已出现。
调用 `computer_window_activate` 置顶窗口。

### 步骤 4：检查 UI 树
调用 `computer_ui_tree` 获取所有交互元素。
树包含自动化 ID、名称、控件类型和屏幕坐标。

### 步骤 5：定位目标元素
调用 `computer_find_element`，传入具体条件（automation_id、名称、控件类型）。

例如，查找名为"保存"的按钮：
```json
{
  "criteria": { "name": "保存" },
  "match_type": "partial"
}
```

### 步骤 6：与元素交互
使用返回的 `bounding_rect` 计算中心坐标：
- `center_x = (left + right) // 2`
- `center_y = (top + bottom) // 2`

然后调用：
- `computer_mouse_move` 移动到中心
- `computer_mouse_click` 使用合适的按钮/点击类型

文本输入：
- 先 `computer_mouse_click` 输入框
- 再 `computer_keyboard_type` 输入文本

### 步骤 7：验证结果
调用 `computer_screenshot`（`annotate: true`）验证操作是否成功。
如果出现意外对话框或错误，暂停并询问用户。

## 特殊情况

### UAC 提示
如果弹出用户账户控制（UAC）对话框，你**无法绕过**——暂停并请用户手动处理。

### 可滚动列表
如果目标元素在可滚动列表中且不可见：
1. 在滚动区域调用 `computer_mouse_scroll`
2. 重新调用 `computer_ui_tree` 获取更新后的坐标
3. 重试

### 最小化的窗口
如果目标窗口已最小化：
1. 调用 `computer_window_activate` 恢复窗口
2. 等待 300ms
3. 继续

## 安全约束

- 未经用户确认，绝不点击破坏性按钮（删除、格式化、卸载）
- 出现警告对话框时，停止并报告
- 点击"保存"或"应用"按钮前，确认操作是用户想要的
- 如果 UAC 提示阻止操作，立即停止并询问用户
