# Computer Use 路由

Windows 桌面任务只使用四个工具：`computer_app`、`computer_state`、`computer_action`、`computer_system`。

1. 启动应用直接调用 `computer_app(operation="launch", query="应用名")`。仅在名称歧义时 search，不得用 Shell 搜索磁盘。
2. 调用一次 `computer_state`。优先使用 UIA ref，其次 OCR 文字；仅当 `source=visual` 时理解响应内附图片。
3. 使用最新 revision 调用 `computer_action`，确定的连续动作合并为一批，最多五个。
4. 相同 revision 不重复动作；失败后按工具给出的 `next_hint` 升级策略。
5. 保存 launch 返回的 `window_id`，聚焦和关闭时直接复用，不要额外 list_running。
6. 网页 DOM 操作交给 `chrome-devtools`；本插件只处理浏览器窗口和系统文件选择器。
7. 密码、验证码、UAC、支付、删除、协议确认和不可逆操作必须让用户接管或确认。
