# Reasonix Computer Use 0.8.0-beta.1

Reasonix 专用的 Windows Computer Use 插件。纯视觉架构，截图 + 视觉模型理解，
与 Claude Computer Use、OpenAI CUA 等行业方案对齐。

## 执行顺序

```text
应用记忆 → 当前窗口截图 → 视觉模型理解 → 坐标点击/键盘输入 → 用户介入
```

插件只向 Reasonix 公开四个 MCP 工具：

| 工具 | 用途 |
|---|---|
| `computer_app` | 搜索、启动、打开文件、聚焦、列出和关闭应用 |
| `computer_state` | 返回应用记忆或当前窗口截图，供视觉模型理解 |
| `computer_action` | 在最新 revision 上批量执行并验证最多五个动作 |
| `computer_system` | 系统画像、刷新、诊断、文件和窗口管理、受限命令 |

鼠标、键盘、窗口截图作为内部模块使用，不进入 MCP 工具列表。

## Beta.1 关键改进（纯视觉迁移）

- **去掉 UIA 和 OCR**，全面采用截图 + 视觉模型理解。只需 Pillow 处理截图。
- 全链路使用 Per-Monitor DPI Awareness V2 和物理像素坐标。
- `computer_state` 返回应用记忆或当前窗口截图；Agent 基于截图识别元素并决定操作。
- 文本注入要求目标窗口处于前台，并使用跨进程哈希熔断阻止旧任务重复键入。
- 粘贴回退会保存并恢复完整 OLE 剪贴板，不覆盖用户原有文本、图片或富文本。
- 同一状态连续失败或观察无进展时触发熔断，禁止转用 Shell 绕过 GUI 执行器。
- Git 安装首次使用时可由 Agent 启动后台依赖安装，并通过 `setup_status` 返回精简进度。
- Reasonix 原生路由 Hook 在明确 GUI 任务中阻止 Bash/Python 抢跑。
- 应用进程使用 Windows Job breakaway 与 Shell 回退启动，退出 Reasonix 不再连带关闭应用。
- 应用窗口复用要求进程路径、启动 PID、可执行文件或完整标题一致。
- Excel 与 WPS 表格提供 `select_cell` 与 `select_range` 领域动作，拒绝用 F5 或猜测网格坐标定位。
- 原始坐标点击必须产生像素变化；无变化不再返回虚假的"动作成功"。
- 快捷键严格校验修饰键，`CRTL` 等拼写错误会在注入前被拒绝。
- `open_file` 通过绝对路径和可选应用打开现有文件。
- Excel/WPS 的 `save_as` 直接接收 Known Folder 下的绝对路径，并以目标文件实际出现作为成功凭据。
- 表格定位必须从名称框或 Office COM 选中项验证真实选区。

## 仓库结构

```text
reasonix-computer-use/
├─ reasonix-plugin.json       Reasonix 原生插件清单
├─ reasonix_computer_use/     MCP 服务与 Windows 执行核心
├─ skills/app-control/        Agent 使用规则
├─ skills/spreadsheet-control/ Excel/WPS 表格快捷操作与按需参考
├─ hooks/                    路由守卫 Hook
├─ commands/                 诊断命令
├─ scripts/                   构建脚本
├─ tests/                     单元测试与 MCP 契约测试
└─ .github/workflows/         Windows CI
```

`tests/` 是发布仓库的一部分，用于防止 revision、视觉回退和四工具接口回归。
`memory/` 下的机器画像、应用索引、成功路径和截图均被 Git 忽略，不会上传用户环境信息。

## Reasonix 路由

- 桌面应用：`computer_app → computer_state → computer_action`
- 网页 DOM：使用 Reasonix 已有的 `chrome-devtools` MCP
- 浏览器窗口、文件选择器和跨应用切换：使用本插件
- `computer_state` 返回应用记忆或当前窗口截图；Agent 基于截图识别元素
- 同一 revision 禁止重复相同动作，相同图片不会再次返回
- `blocked=true` 时 Agent 必须停止，不得换 Shell、浏览器或新会话重复原流程
- 浏览器地址导航使用同一批次的 `Ctrl+L → 输入 URL → Enter`
- Edit/ComboBox 输入默认替换已有内容；只有 `replace:false` 才追加
- 表格使用 `select_cell`/`select_range`、名称框或定位功能，不猜测单元格像素位置
- Windows 应用搜索未命中时会立即增量查询 StartApps，支持"计算器/Calculator"等系统应用别名
- 用户指定应用作为处理步骤时不静默替换为 Python、公式或 CLI
- `UserPromptSubmit` 识别明确 GUI 流程，`PreToolUse` 在首个工具执行前阻止 Shell/Python 替代
- Excel/WPS 表格任务自动使用 `spreadsheet-control` Skill

`computer_action.actions[]` 固定使用 `type` 字段：

```json
{
  "window_id": "w1",
  "revision": "r2-ab12cd",
  "actions": [
    {"type":"click_point","x":200,"y":150,"coordinate_space":"window"},
    {"type":"type","text":"你好","replace":true},
    {"type":"press","keys":["ENTER"]}
  ],
  "expect":{"text_present":"你好"}
}
```

状态元素矩形和视觉返回坐标默认都是窗口内物理像素，`click_point` 使用
`coordinate_space: "window"`；屏幕绝对物理坐标必须显式使用
`coordinate_space: "screen"`。不要自行乘除 DPI，也不要在窗口坐标上再次加窗口原点。

## 系统画像

首次会话自动快速生成：

- `memory/system.md`：适合用户阅读的简短摘要
- `memory/system-index.json`：应用、硬件、显示器、DPI和 Known Folder 的权威索引
- `memory/apps/*.json`：按应用保存的已验证成功路径

桌面、文档和下载目录通过 Windows 注册表 Known Folder 读取，支持重定向到 D/E/F 等磁盘。

## 安装

### 环境要求

- **Windows 10/11 x64**
- **Python 3.10+**（推荐 3.12）
- **Pillow**（截图依赖，首次使用时自动安装）

纯视觉架构不再需要 UIA (comtypes) 和 OCR (rapidocr-onnxruntime)，只需 Pillow 处理截图。

### Git 安装（推荐）

**Reasonix Desktop：**

打开 **设置 → 插件 → Git 仓库**，输入：

```text
git:github.com/Plocr/Reasonix-computer-use
```

先点 **预检**，再点 **安装插件**。更新时勾选 **覆盖同名插件**。

**CLI：**

```powershell
reasonix plugin install git:github.com/Plocr/Reasonix-computer-use --replace --yes
reasonix plugin doctor computer-use
```

### 本地开发安装

```powershell
python -m pip install -e ".[dev]"
reasonix plugin install . --replace --yes
reasonix plugin doctor computer-use
```

### 首次使用

首次会话检测到 Pillow 未安装时：

1. `computer_app`/`computer_state`/`computer_action` 返回 `setup_required`
2. Agent 告知用户将下载依赖，用户确认
3. Agent 调用 `computer_system(operation="setup", params={"confirmed":true})`
4. 后台安装 Pillow 到 `%LOCALAPPDATA%\Reasonix\computer-use\site-packages`
5. Agent 调用 `setup_status` 等待完成

如果 `python` 不存在，从 [python.org](https://python.org/downloads/windows/) 安装并勾选 **Add Python to PATH**。
如果自动安装失败，手动执行：

```powershell
python -m pip install "Pillow>=10.0.0"
reasonix plugin doctor computer-use
```

安装或升级后，旧 MCP 可能跨任务常驻。Alpha.13 会检测核心文件变化，返回一次"插件已更新"并自行退出；重试当前工具调用即可按新版本重启服务。

## 诊断

安装后可让 Reasonix 调用：

```json
{"operation":"diagnose"}
```

`computer_system` 会返回 Windows 支持状态、DPI模式、显示器和四工具注册状态。

本地开发检查：

```powershell
python -m pytest -q
python -m reasonix_computer_use.session_start
```

Beta.1 当前包含 85 项自动测试，覆盖动作 schema、物理坐标、revision、首次依赖安装、
视觉回退、Unicode 输入、剪贴板恢复、熔断和 Shell 逃逸阻断。

## 安全边界

以下操作默认暂停并要求用户确认或接管：

- 密码、验证码和 UAC
- 支付、购买和协议确认
- 删除、卸载和结束非目标进程
- 系统修改、外部写入和不可逆命令

工具结果和应用记忆不记录输入正文、剪贴板内容或凭据。

## 当前范围

0.8.0-beta.1 主要支持 Windows 10/11 x64。纯视觉架构（无 UIA/OCR 依赖）使向 macOS 和 Linux 迁移成为可能。
跨平台支持（Windows/macOS/Linux）正在开发中，当前 Windows 功能最完善。
跨应用长链自治、主动巡检、语音无障碍和自动探索软件菜单不在本版本范围内。
