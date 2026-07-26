# Computer Use 能力测试

Alpha.13 使用通用行为契约验证显式激活的 Computer Use Operator，不在实现中保存 Edge、WPS、QQ 等应用的专属坐标。

## 测试层次

- `quick`：轨迹回放、脱敏和测试项目完整性，不启动 GUI。
- `full`：包含环境矩阵；传入测试应用时，在 Windows 上执行真实 UIA 输入和按钮调用。
- `replay`：离线检查旧 revision、重复动作和未授权降级，不操作桌面。
- `benchmark`：从脱敏轨迹生成 JSON 或 Markdown 评分。
- `matrix`：校验语言、DPI、显示器、Known Folder 和应用类型配置。

## 本地运行

```powershell
python -m reasonix_computer_use.capability_runner quick
python -m reasonix_computer_use.capability_runner matrix
dotnet publish capability_app\Reasonix.CapabilityApp.csproj -c Release -r win-x64 --self-contained true -p:PublishSingleFile=true -o capability-dist\win-x64
python -m reasonix_computer_use.capability_runner full --app capability-dist\win-x64\Reasonix.CapabilityApp.exe
```

在线回放仅接受 `Reasonix.CapabilityApp`。真实应用只用于手工基准，真实应用轨迹不能重新注入桌面。

## Reasonix 13 Commands

- `/computer-use:doctor`：静态诊断；只有 `--live` 才运行在线探测。
- `/computer-use:test`：运行 quick 或 full 能力测试。
- `/computer-use:trace`：查看或导出脱敏轨迹。
- `/computer-use:benchmark`：生成能力评分。

`/computer-use:run` 是唯一普通任务入口，并委派给 `/computer-use:agent:operator`。自然语言不会自动触发；四个 MCP 工具在未激活时由 Hook 硬拒绝。

## 发布门禁

- 合成应用行为契约全部通过。
- 脱敏测试不得泄漏输入、剪贴板、凭据或完整路径。
- 离线回放不得出现重复动作、旧 revision 或未授权降级。
- UIA 场景视觉调用为零。
- 自动模式必须在 UIA/OCR 连续失败后到达视觉；显式 UIA、OCR、视觉模式不得调用其他感知通道。
- 非零窗口原点和 125% DPI 下，UIA/OCR 矩形必须保持窗口局部物理坐标；MCP 重启后引用必须重新定位。
- 文件搜索必须使用重定向 Known Folder 和有界目录遍历，拒绝磁盘根目录与递归 Shell。
- 单行搜索、地址和定位输入支持语义化 `submit`；文档及多行编辑器必须拒绝默认 Enter 提交。
- 输入成功、revision 更新并更换短 ref 后，下一次独立 Enter 仍可在真实焦点复核后提交；焦点离开时不得误判为提交。
- 首次观察缺少完整 `task_goal`、空 `computer_action` 和非浏览器快捷键猜测必须在执行前给出规范修正提示。
- 命令展开后不得再次调用 `slash_command` 或枚举全局命令；输入阶段提示必须阻止重复定位、重复键入和提交后的建议项误点。
- 命名目标必须从完整任务继承到结果观察；泛化应用记忆不得抢占指定目标，增强 OCR 应返回精确候选及带语义凭据的推荐动作。
- 外部视觉模型返回多候选、缺少矩形或身份消歧时不得生成可执行坐标。
- 启动前或启动时已经存在的目标标题、状态栏媒体名和静态暂停图标必须保持 `playback: pending`；只有动作前后语义验证可以设置 `task_completion.verified=true`。
- 未指定版本的命名媒体目标不得由 Live、DJ、remix、翻唱或演唱会等相关版本冒充；显式指定版本时才允许该变体。
- 外部视觉结果不得直接完成任务；pending 任务在 Stop 时必须标记 `incomplete`，不得生成成功回执。
- 连点、长按、拖动和持续按键必须在异常路径释放鼠标键/键盘键；水平滚轮与左右中键保持物理像素窗口边界。
- OCR 目标词未命中时允许一次本地放大增强，结果必须映射回原窗口坐标并去重；无文字图标不得伪造 OCR 标签。
- WMI 代理启动的合成 GUI 必须在测试 worker 所属的 `KILL_ON_JOB_CLOSE` Job 关闭后继续存活。
- 单条 trace 不超过 256 KB，记录开销 P95 不超过 10 ms。
- 支持 capabilities 诊断的发布环境必须通过 `reasonix doctor capabilities --json`。

GitHub Hosted CI 构建 Windows、Linux 和 macOS 测试应用。真实 Windows GUI 契约由带 `reasonix-gui` 标签的 self-hosted runner 执行。Alpha.13 不提供 macOS/Linux Computer Use 后端。
