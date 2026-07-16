# Computer Use 能力测试

Alpha.12 使用通用行为契约验证 Computer Use，不在实现中保存 Edge、WPS、QQ 等应用的专属坐标。

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

Commands 是手动入口。自然语言桌面任务仍由 Skill、Hooks 和四个 MCP 工具自动路由。

## 发布门禁

- 合成应用行为契约全部通过。
- 脱敏测试不得泄漏输入、剪贴板、凭据或完整路径。
- 离线回放不得出现重复动作、旧 revision 或未授权降级。
- UIA 场景视觉调用为零。
- 单条 trace 不超过 256 KB，记录开销 P95 不超过 10 ms。
- 支持 capabilities 诊断的发布环境必须通过 `reasonix doctor capabilities --json`。

GitHub Hosted CI 构建 Windows、Linux 和 macOS 测试应用。真实 Windows GUI 契约由带 `reasonix-gui` 标签的 self-hosted runner 执行。Alpha.12 不提供 macOS/Linux Computer Use 后端。
