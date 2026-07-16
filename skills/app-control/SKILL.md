---
name: app-control
description: 使用低 token 的 UIA、OCR、视觉回退完成 Windows 桌面任务
---

# Windows Computer Use

Hook 会按用户要求选择执行边界：明确要求指定应用、逐个点击或禁止脚本时为 `strict_gui`；普通桌面任务为 `gui_preferred`；只要求结果或明确要求 CLI/Python 时为 `result_only`。`gui_preferred` 只有在至少 3 次 Computer Use 调用且发生 2 次真实失败，或工具明确返回 `blocked:true` 后，才允许安全降级。降级后必须如实说明实际方法，不得声称完成了未执行的 GUI 步骤。

桌面应用固定使用以下流程：

```text
computer_app(launch, query="应用名")
→ computer_state(goal)
→ computer_action(revision, actions, expect)
```

- 任一桌面工具若返回 `setup_required`，先说明依赖将安装到当前用户目录。用户确认后调用 `computer_system(operation="setup", params={"confirmed":true})`，再调用 `computer_system(operation="setup_status", params={"wait_seconds":20})`；等待由插件内部完成，禁止通过 Shell sleep 或 pip。
- 启动应用默认直接调用 `computer_app(operation="launch", query="应用名")`。只有名称歧义或启动失败时才先 search；不要把应用名填入 `app_id`。
- 打开已有文件使用 `computer_app(operation="open_file", path="绝对路径", query="可选应用名")`；禁止把文件名、桌面或目录名作为应用启动。
- 保存 launch 返回的 `window_id`。聚焦和关闭时直接复用，不要为了找回窗口调用 `list_running`。
- 禁止转用 Bash 或 PowerShell 全盘查找应用。
- `computer_state` 已在内部按应用记忆、UIA、本地 OCR、窗口图片排序。不要自行调用图片理解工具。
- `source=uia` 使用 `click_ref`；`source=ocr` 使用带完整 `text` 的 `click_text` 或直接使用 `click_ref`；只有 `source=visual` 才使用当前 revision 的 `click_point`。
- OCR 返回的 `o*` 短引用可以直接交给 `click_ref`。`click_text` 必须传返回元素的完整 `name`，禁止只传 `ref`、空文字，或把 OCR `rect` 改成 `click_point`。
- `actions[]` 的动作名字段固定为 `type`，不是 `action` 或 `command`。示例：`{"type":"click_ref","ref":"e1"}`、`{"type":"type","text":"周杰伦"}`、`{"type":"press","keys":["ENTER"]}`。
- 浏览器地址导航使用同一批次的 `{"type":"press","keys":["CTRL","L"]}`、`type(URL)`、`ENTER`；也兼容 `keys:["CTRL+L"]`。不得点击网页搜索框代替地址栏。
- 页面内搜索连续受阻时，允许合成同一站点的 `/search?...`、`/s?wd=...` 等结果页 URL 完成目标；必须校验域名与结果，网页 DOM 可用时仍优先交给 `chrome-devtools`。
- Edit/ComboBox 的 `type` 默认替换已有内容；仅明确需要追加时设置 `replace:false`，避免重试产生重复文本。
- 表格单元格必须用 `select_cell(cell="A1")`，连续范围使用 `select_range(range="A1:A101")`；禁止按 F5 或根据网格像素猜测位置。动作结果中的 `selected:true` 或状态中的 `focused:true`、`selected:true` 才表示选择成功。
- Excel/WPS 首次保存到指定位置使用 `save_as(path="绝对路径")`；路径从 `computer_system(profile)` 的 Known Folder 获取。只有返回 `verified:true` 且文件实际出现才算保存成功。
- 连续数据优先一次粘贴制表符/换行文本。计算器可在一次 `type` 中键入完整的 `1+2+...+100` 表达式，不需要逐个鼠标点击。
- 用户明确指定某个应用作为处理步骤时，不得静默改用 Python、公式或 CLI。应用名称未命中时先搜索同类软件和 StartApps；仍不可用才说明并请求用户决定。用户只要求结果、未指定方法时才可主动切换 CLI。
- `computer_state` 的 UIA、OCR矩形和视觉图片全部使用窗口内物理像素；`click_point` 默认 `coordinate_space=window`。屏幕绝对坐标必须显式声明 `coordinate_space=screen`，不要自行按 DPI 缩放或叠加窗口原点。
- 确定的输入、按键和点击合并到一次 `computer_action`，最多五步。执行器会验证并在失败处停止。
- `unchanged=true` 时不要重复观察或截图。`repeat_blocked` 时必须换策略或请求用户介入。
- 任一响应 `blocked=true` 时立即停止工具循环并报告最小阻断，不得转用 Shell、浏览器或新会话重复同一目标。
- `computer_system(command)` 仅用于单条只读诊断；不得用 PowerShell、SendKeys 或 Win32 脚本绕过 `computer_action`。
- 网页内容交给 `chrome-devtools`，本插件负责浏览器外壳、桌面和系统对话框。
- 密码、验证码、UAC、支付、删除、协议确认和不可逆操作由用户处理。
