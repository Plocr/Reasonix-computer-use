---
name: spreadsheet-control
description: 使用 Reasonix Computer Use 在 Microsoft Excel 或 WPS 表格中定位单元格、选择范围、批量输入、填充公式、查找替换、切换工作表并验证结果。遇到 .xlsx/.xls/.csv、单元格地址、行列范围或用户明确要求操作 Excel/WPS 表格时使用。
---

# 表格快捷操作

## 固定流程

1. 用 `computer_app` 启动或聚焦 Excel/WPS，保存 `window_id`。
2. 用 `computer_state` 确认工作簿标题和当前状态。
3. 精确定位使用 `select_cell(cell="A1")`；连续范围使用 `select_range(range="A1:A101")`。
4. 只有动作结果返回 `selected:true` 才继续输入或填充。不得按 F5、猜网格坐标或反复点击名称框。
5. 将确定的快捷键和输入合并到一次 `computer_action`，每批最多五步。
6. 首次保存使用 `save_as(path="Known Folder 下的绝对路径")`；已有文件用 `Ctrl+S`。只有 `verified:true` 且目标文件存在才算保存成功。
7. 保存后通过标题、可见首尾值、公式栏或重新定位关键单元格验证。不要用 Python 读取文件冒充 GUI 验证。

## 高价值快捷键

仅在目标表格窗口位于前台且选区已确认后使用：

| 目的 | 按键 |
|---|---|
| 编辑当前单元格 | `F2` |
| 保存 | `Ctrl+S` |
| 撤销 / 重做 | `Ctrl+Z` / `Ctrl+Y` |
| 复制 / 剪切 / 粘贴 | `Ctrl+C` / `Ctrl+X` / `Ctrl+V` |
| 查找 / 替换 | `Ctrl+F` / `Ctrl+H` |
| 跳到数据区边界 | `Ctrl+方向键` |
| 扩展选区到数据区边界 | `Ctrl+Shift+方向键` |
| 逐格扩展选区 | `Shift+方向键` |
| 选择整列 / 整行 | `Ctrl+Space` / `Shift+Space` |
| 同值填充整个已选范围 | 输入内容后 `Ctrl+Enter` |
| 向下 / 向右填充 | `Ctrl+D` / `Ctrl+R` |
| 自动求和 | `Alt+=` |
| 上一张 / 下一张工作表 | `Ctrl+PageUp` / `Ctrl+PageDown` |
| 到工作表开头 / 最后使用区域 | `Ctrl+Home` / `Ctrl+End` |

## 批量操作

- 连续常量：先 `select_range`，一次键入以换行分隔的单列数据或以制表符分隔的多列数据。
- 相同内容：选中范围，输入一次，再按 `Ctrl+Enter`。
- 递推公式：在首个目标单元格输入公式，选择完整目标范围，再用 `Ctrl+D` 向下填充。
- 替换原数据：优先使用辅助列计算，验证首尾结果后复制，并通过“选择性粘贴为值”覆盖原列；不要直接覆盖尚未验证的源数据。
- 用户要求“逐个执行”时保留该过程；用户只要求结果时才使用等价批量填充。

## 验证与停止

- 点击或按键返回 `changed:false` 时，不得宣称成功，也不得重复原动作。
- 输入后至少验证一个首部单元格和一个尾部单元格；长表格使用精确定位，不靠滚动猜位置。
- 遇到兼容性差异时先重新观察，再读取 [快捷键参考](references/shortcuts.md)。同一快捷键失败两次后升级到 UIA/OCR 或请求用户介入。
- 删除行列、覆盖原数据或关闭未保存文件前请求确认。
