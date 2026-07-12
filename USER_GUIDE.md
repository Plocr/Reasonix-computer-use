# Reasonix Computer Use 使用说明

## 常规任务

用户直接描述目标，例如“打开 QQ，进入设置并切换主题”。Agent 应按以下顺序调用：

1. `computer_app(operation="search", query="QQ")`
2. `computer_app(operation="launch", app_id="...")`
3. `computer_state(window_id="w1", goal="进入设置")`
4. `computer_action(window_id="w1", revision="...", actions=[...], expect={...})`

启动时也可以直接传 `query`，插件会选取精确名称且有有效启动路径的候选。搜索结果最多十项，不会把完整软件目录加入会话。

## 状态来源

- `source=memory`：命中该应用过去验证成功的路径。
- `source=uia`：直接使用返回的 `ref`，不需要截图。
- `source=ocr`：直接按返回的文字调用 `click_text`，不需要视觉模型。
- `source=visual`：响应会附带当前窗口图片、窗口原点、尺寸和 revision。坐标只能用于该 revision。
- `unchanged=true`：不要再次截图或执行相同坐标。

## 动作批次

一个 `computer_action` 最多包含五个动作。任何一步失败都会停止后续动作。常用动作包括：

- `click_ref`、`click_text`、`click_point`
- `move`、`hover`、`double_click`、`right_click`、`middle_click`
- `drag`、`scroll`
- `type`、`press`、`key_down`、`key_up`、`wait`

输入内容不会出现在结果日志中。

## 系统画像更新

首次 `SessionStart` 自动创建 `system.md` 和 `system-index.json`。以下情况自动或按需刷新：

- 应用搜索没有命中
- 保存的 exe 不存在
- 用户调用 `computer_system(operation="refresh")`
- 应用版本或运行窗口特征发生变化

查询路径或软件信息使用 `computer_system(operation="profile", target="关键词")`。

## 浏览器任务

网页表单、DOM查询、点击和内容提取使用 `chrome-devtools`。本插件只处理浏览器启动、窗口切换、下载对话框和系统文件选择器。纯画布、远程桌面或调试不可用时才使用视觉回退。

## 需要用户介入

密码、验证码、UAC、支付、删除、卸载、协议确认和不可逆系统命令会返回 `confirmation_required`。完成用户操作后，重新调用 `computer_state` 获取新的 revision 即可继续。
