# Computer Use 路由

Windows 桌面任务只使用四个工具：`computer_app`、`computer_state`、`computer_action`、`computer_system`。

1. 启动应用直接调用 `computer_app(operation="launch", query="应用名")`。仅在名称歧义时 search，不得用 Shell 搜索磁盘。
2. 调用一次 `computer_state`。优先使用 UIA ref，其次 OCR 文字；仅当 `source=visual` 时理解响应内附图片。
3. 使用最新 revision 调用 `computer_action`，确定的连续动作合并为一批，最多五个。
   `actions[]` 必须使用 `type` 字段，例如 `{"type":"click_ref","ref":"e1"}`、`{"type":"type","text":"内容"}`、`{"type":"press","keys":["ENTER"]}`。
4. 相同 revision 不重复动作；失败后按工具给出的 `next_hint` 升级策略。
   返回 `blocked=true` 时立即停止，不得切换 Shell、浏览器或另开流程重试原任务。
5. 保存 launch 返回的 `window_id`，聚焦和关闭时直接复用，不要额外 list_running。
6. 网页 DOM 操作交给 `chrome-devtools`；本插件只处理浏览器窗口和系统文件选择器。
7. 密码、验证码、UAC、支付、删除、协议确认和不可逆操作必须让用户接管或确认。
8. `computer_system(command)` 只允许单条只读诊断，禁止用 PowerShell/SendKeys/Win32 脚本替代 GUI 工具。
9. 每次修改功能、接口、安装方式、依赖或发布流程时，同步更新 README.md 的对应说明和版本状态。
10. 任一桌面工具返回 `setup_required` 时，先告知用户将下载依赖；确认后调用 `computer_system(operation="setup", params={"confirmed":true})`，再调用 `computer_system(operation="setup_status", params={"wait_seconds":20})`。等待由插件内部完成，不得使用 Shell sleep 或 pip。
11. 浏览器地址导航使用同一批次的 `press(["CTRL","L"]) → type(URL) → press(["ENTER"])`，不得点击网页搜索框代替地址栏。编辑框输入默认替换原内容，仅明确追加时使用 `replace:false`。
12. 页面内搜索连续受阻时，可通过同一站点的搜索结果 URL 完成目标，但必须确认域名和结果。OCR 的 `o*` ref 使用 `click_ref`；`click_text` 必须提供非空 `text`。
