---
description: 使用 UI 树填写桌面应用表单
  当用户要求填写表单、输入数据、录入信息或完成类似交互时使用
---

# 表单填写（桌面应用）

使用 UIAutomation 自动化填写 Windows 桌面应用表单。

## 何时使用

- 用户要求填写表单、输入信息、在桌面应用中填充字段
- 目标是 Windows 原生应用（非网页表单——那是 chrome-devtools 的领域）
- 示例：填写设置对话框、在 Word 表单中输入数据、完成安装向导

## 工作流

### 步骤 1：检查表单
在目标窗口或活动桌面上调用 `computer_ui_tree`。
识别所有输入框（control_type=`Edit`、`ComboBox`、`CheckBox`、`RadioButton` 等）。

### 步骤 2：定位每个字段
对每个要填写的字段，调用 `computer_find_element`，传入：
- `automation_id`（如果可用，最可靠）
- `name`（通常是字段旁边的标签文本）
- `control_type`（如果多个匹配则用于消歧）

### 步骤 3：与每个字段交互

#### 文本输入框（Edit）
1. `computer_mouse_move` 移动到输入框中心
2. `computer_mouse_click`（左键单击）聚焦
3. `computer_keyboard_type` 输入目标文本

#### 复选框（CheckBox）
1. `computer_mouse_move` 移动到复选框
2. `computer_mouse_click` 切换

#### 单选按钮（RadioButton）
1. `computer_mouse_move` 移动到单选按钮
2. `computer_mouse_click` 选择

#### 下拉列表（ComboBox）
1. `computer_mouse_move` 移动到下拉列表
2. `computer_mouse_click` 打开
3. 等待 500ms 让列表展开
4. 重新获取 UI 树找到列表项
5. `computer_find_element` 定位目标选项
6. `computer_mouse_click` 选择

#### 按钮（Button、Hyperlink）
1. `computer_mouse_move` 移动到按钮
2. `computer_mouse_click`（或双击，如果需要）

### 步骤 4：验证表单状态
调用 `computer_screenshot`（`annotate: true`）验证所有字段是否正确填充。
如果有字段填写失败，重试最多 2 次。

### 步骤 5：提交（如果适用）
如果用户想提交表单：
- 查找提交按钮（通常命名为"确定"、"保存"、"提交"、"下一步"）
- 用 `computer_mouse_click` 点击

## Windows 中常见的字段类型

| 控件类型 | 说明 | 交互方式 |
|---|---|---|
| Edit | 文本输入框 | 聚焦 → 输入 |
| ComboBox | 下拉列表 | 点击 → 选择项 |
| CheckBox | 复选框 | 点击切换 |
| RadioButton | 单选按钮 | 点击选择 |
| Button | 操作按钮 | 点击 |
| Hyperlink | 链接 | 点击 |
| ListItem | 列表项 | 点击 |
| Slider | 滑块 | 拖动或方向键 |
| TabItem | 选项卡页 | 点击选项卡标题 |

## 提示

- 每次重大操作后都调用 `computer_ui_tree`——树会变化
- 对于有标签的字段，用标签的 `name`（用户可见的文本）搜索
- 如果字段被禁用（`enabled: false`），报告给用户——可能不可编辑
- 对于密码字段，直接调用 `computer_keyboard_type`（字段会遮蔽输入）

## 安全

- 点击"确定"、"保存"、"提交"、"下一步"、"安装"前，确认所有字段都正确
- 如果出现确认对话框，停止并给用户看截图
- 绝不自动接受许可协议或条款——先问用户
