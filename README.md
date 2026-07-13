# Reasonix Computer Use 0.8.0-alpha.6

Reasonix 专用的 Windows Computer Use 插件。任务完成优先，低 token 为优化目标。

Alpha.6 已完成 QQ、QQ 音乐、Ollama Desktop 等真实应用验证，重点修复 Edge 启动、首次安装轮询和高 DPI
坐标偏移、WebView 输入失败、重复截图与无进展工具循环。

## 执行顺序

```text
system-index / 应用记忆 → UIA → 本地 OCR → 当前窗口视觉 → 用户介入
```

插件只向 Reasonix 公开四个 MCP 工具：

| 工具 | 用途 |
|---|---|
| `computer_app` | 搜索、启动、聚焦、列出和关闭应用 |
| `computer_state` | 返回目标相关的 UIA/OCR 状态，必要时附一张当前窗口图片 |
| `computer_action` | 在最新 revision 上批量执行并验证最多五个动作 |
| `computer_system` | 系统画像、刷新、诊断、文件和窗口管理、受限命令 |

鼠标、键盘、窗口截图、UIA和OCR作为内部模块使用，不进入 MCP 工具列表。

## Alpha.6 关键改进

- 全链路使用 Per-Monitor DPI Awareness V2 和物理像素坐标。
- UIA 控件优先调用 `Invoke`、`SetFocus`、`ValuePattern` 等内部 Pattern，不依赖坐标。
- OCR 坐标由窗口局部物理像素转换为屏幕物理像素，并绑定当前 revision。
- WebView/自绘输入依次使用 UIA ValuePattern、Unicode SendInput 和一次剪贴板粘贴回退。
- 文本注入要求目标窗口处于前台，并使用跨进程哈希熔断阻止旧任务重复键入。
- 粘贴回退会保存并恢复完整 OLE 剪贴板，不覆盖用户原有文本、图片或富文本。
- 输入后通过 UIA 或本地 OCR 验证；验证失败立即停止后续 Enter、提交等动作。
- UIA/OCR 通道切换不会生成虚假 revision。
- 同一状态连续失败或观察无进展时触发熔断，禁止转用 Shell 绕过 GUI 执行器。
- Git 安装首次使用时可由 Agent 启动后台依赖安装，并通过 `setup_status` 返回精简进度。
- 移除依赖 `cmd` 的外部 Hook，避免 PATH 不完整时每轮重复报错；安全检查全部位于 MCP 内部。
- 应用进程使用 Windows Job breakaway 与 Shell 回退启动，退出 Reasonix 不再连带关闭应用。

## 仓库结构

```text
reasonix-computer-use/
├─ reasonix-plugin.json       Reasonix 原生插件清单
├─ reasonix_computer_use/     MCP 服务与 Windows 执行核心
├─ skills/app-control/        Agent 使用规则
├─ installer/                 Windows 一键安装器配置
├─ scripts/                   自包含 ZIP 与安装器构建脚本
├─ tests/                     单元测试与 MCP 契约测试
└─ .github/workflows/         Windows CI
```

`tests/` 是发布仓库的一部分，用于防止 UIA、revision、OCR回退和四工具接口回归。`memory/` 下的机器画像、应用索引、成功路径和截图均被 Git 忽略，不会上传用户环境信息。

## Reasonix 路由

- 桌面应用：`computer_app → computer_state → computer_action`
- 网页 DOM：使用 Reasonix 已有的 `chrome-devtools` MCP
- 浏览器窗口、文件选择器和跨应用切换：使用本插件
- UIA成功时不返回图片；OCR成功时不调用外部视觉模型
- 同一 revision 禁止重复相同动作，相同图片不会再次返回
- `blocked=true` 时 Agent 必须停止，不得换 Shell、浏览器或新会话重复原流程

`computer_action.actions[]` 固定使用 `type` 字段：

```json
{
  "window_id": "w1",
  "revision": "r2-ab12cd",
  "actions": [
    {"type":"click_ref","ref":"e1"},
    {"type":"type","text":"你好"},
    {"type":"press","keys":["ENTER"]}
  ],
  "expect":{"text_present":"你好"}
}
```

视觉返回的坐标默认是窗口内物理像素，`click_point` 使用
`coordinate_space: "window"`；屏幕绝对物理坐标必须显式使用
`coordinate_space: "screen"`。

## 系统画像

首次会话自动快速生成：

- `memory/system.md`：适合用户阅读的简短摘要
- `memory/system-index.json`：应用、硬件、显示器、DPI和 Known Folder 的权威索引
- `memory/apps/*.json`：按应用保存的已验证成功路径

桌面、文档和下载目录通过 Windows 注册表 Known Folder 读取，支持重定向到 D/E/F 等磁盘。应用搜索优先读取 App Paths、开始菜单、桌面快捷方式、卸载项和运行中窗口，保存精确 exe 目标。

## 安装

### 应该推荐哪一种

| 使用者 | 推荐方式 | 需要自行准备的环境 |
|---|---|---|
| 普通 Windows 用户 | GitHub Release 安装器 EXE | 无需 Python、Git 或 pip |
| 已有 Python 的开发者 | Reasonix Desktop Git 安装 | 只需 Python 3.10+，依赖可在首次使用时安装 |
| 插件开发者 | 克隆仓库后本地安装 | Python 3.10+、运行依赖和测试依赖 |

如果是推荐给完全不了解 Python 的人，请使用安装器 EXE。Git 安装现在只要求系统
存在 Python 3.10+，插件依赖可由首次使用引导器完成。

### Windows 一键安装器（推荐）

普通 Windows 10/11 x64 用户从 GitHub Releases 下载：

```text
reasonix-computer-use-<版本>-windows-x64-setup.exe
```

安装器包含 Python、UIA、RapidOCR、ONNX Runtime 和 OCR 模型，不需要安装 Python、
Git 或 pip。它会安装到当前用户的 `%LOCALAPPDATA%\ReasonixPlugins\computer-use`：

- 检测到 Reasonix CLI 时自动注册并运行 doctor；
- 未检测到 CLI 时显示安装目录，用户只需在 Reasonix Desktop 的
  **设置 → 插件 → 本地目录** 中选择该目录；
- 安装器只写入当前用户目录，不需要管理员权限。

### Windows 自包含 ZIP

普通 Windows 10/11 x64 用户优先从 GitHub Releases 下载：

```text
reasonix-computer-use-<版本>-windows-x64.zip
```

压缩包已经包含 Python、UIA、RapidOCR、ONNX Runtime 和 OCR 模型，无需安装 Python
或运行 `pip`。解压后有两种安装方式：

- Reasonix Desktop：打开 **设置 → 插件 → 本地目录**。Reasonix 1.17.10 的桌面端会以
  link 模式安装，因此目录必须位于界面提示的允许根目录内，例如
  `C:\Users\<用户名>\ReasonixPlugins\computer-use`；其他磁盘会被安全检查拒绝。
- CLI：执行 `reasonix plugin install <解压目录> --replace --yes`。

安装完成后运行 `reasonix plugin doctor computer-use`，并开启一个新会话。

### Reasonix Desktop Git 安装

Git 安装只下载插件源码。用户需要先准备 64 位 Python 3.10 或更高版本，推荐
Python 3.12：

```powershell
winget install --exact --id Python.Python.3.12
```

安装 Python 后关闭并重新打开终端，确认：

```powershell
python --version
```

然后打开 Reasonix Desktop 的 **设置 → 插件 → Git 仓库**，输入：

```text
git:github.com/Plocr/Reasonix-computer-use
```

先点 **预检**，再点 **安装插件**。更新现有版本时勾选 **覆盖同名插件**。
安装完成后新建会话。首次会话检测到依赖缺失时：

1. 首次 `computer_app`、`computer_state` 或 `computer_action` 返回 `setup_required` 和缺失模块。
2. Agent 告知用户将下载依赖，用户确认一次。
3. Agent 调用 `computer_system(operation="setup", params={"confirmed":true})`。
4. 安装在后台运行，Agent 调用 `setup_status` 并传入 `wait_seconds: 20`，由插件内部等待进度变化；禁止使用 Shell sleep 轮询。
5. 依赖写入 `%LOCALAPPDATA%\Reasonix\computer-use\site-packages`，以后更新 Git 插件无需重复安装。

安装过程不会增加新的 MCP 工具，也不会将完整 pip 日志写入会话。安装命令和依赖列表
固定，Agent 不能传入任意包名或 pip 参数。旧会话不会自动加载新插件版本。

如果 `python` 命令不存在，可重新登录 Windows，或从
[python.org](https://www.python.org/downloads/windows/) 安装并勾选 **Add Python to PATH**。
如果自动安装失败，可以手动执行：

```powershell
python -m pip install "Pillow>=10.0.0" `
  "comtypes>=1.4.0" "rapidocr-onnxruntime>=1.4.4"
reasonix plugin doctor computer-use
```

### CLI Git 安装

要求 Python 3.10 或更高版本。先安装本项目直接使用的必要库：

```powershell
python -m pip install "Pillow>=10.0.0" `
  "comtypes>=1.4.0" "rapidocr-onnxruntime>=1.4.4"
reasonix plugin install git:github.com/Plocr/Reasonix-computer-use --replace --yes
reasonix plugin doctor computer-use
```

`rapidocr-onnxruntime` 会自动安装 NumPy、ONNX Runtime 等传递依赖，不需要逐项安装。

### 本地开发安装

克隆仓库后安装项目和测试依赖：

```powershell
python -m pip install -e ".[dev]"
reasonix plugin install . --replace --yes
reasonix plugin doctor computer-use
```

CLI 默认使用复制安装。`--link` 仅适合源码位于 Reasonix 允许的 skill roots 内（通常是
`C:\Users\<用户名>` 或 Reasonix workspace），且仓库不包含超过插件大小限制的
`dist/`、`runtime/` 等构建产物时使用。放在其他磁盘的开发仓库使用 `--link` 会触发
`link target escapes skill roots`，此时应去掉 `--link` 或安装 GitHub Release 包。

安装或升级任一来源后，需要结束旧任务，使旧的
`python -m reasonix_computer_use` MCP 进程退出。新的 Reasonix 工具调用会按当前
版本重新启动服务。

Reasonix 的 Git/本地安装不会执行仓库中的第三方安装脚本，这是平台安全边界。
不希望配置 Python 环境的用户应使用 Windows 安装器。

## Windows 发行包

维护者在 Windows x64 构建机执行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_release.ps1 -KeepStage
powershell -ExecutionPolicy Bypass -File scripts\build_installer.ps1
```

构建输出包括：

```text
dist/reasonix-computer-use-<版本>-windows-x64.zip
dist/reasonix-computer-use-<版本>-windows-x64.zip.sha256
dist/reasonix-computer-use-<版本>-windows-x64-setup.exe
dist/reasonix-computer-use-<版本>-windows-x64-setup.exe.sha256
```

脚本会读取 `reasonix-plugin.json` 中的版本，下载 Windows 嵌入式 Python，将全部运行
依赖安装到包内，生成第三方包清单，执行导入和 MCP 初始化检查，最后输出 ZIP、EXE
与 SHA-256。安装器构建需要 Inno Setup 6，GitHub Actions 会自动安装。推送
`v<版本>` 标签时，GitHub Actions 会构建并发布这些文件；也可以在
Actions 页面手动构建但不创建 Release。

## 诊断

安装后可让 Reasonix 调用：

```json
{"operation":"diagnose"}
```

`computer_system` 会返回 Windows 支持状态、DPI模式、显示器、UIA、OCR和四工具注册状态。

本地开发检查：

```powershell
python -m pytest -q
python -m reasonix_computer_use.session_start
```

Alpha.6 当前包含 54 项自动测试，覆盖动作 schema、物理坐标、revision、首次依赖安装、
UIA/OCR/视觉回退、Unicode 输入、WebView ComboBox、剪贴板恢复、熔断和
Shell 逃逸阻断。

## 安全边界

以下操作默认暂停并要求用户确认或接管：

- 密码、验证码和 UAC
- 支付、购买和协议确认
- 删除、卸载和结束非目标进程
- 系统修改、外部写入和不可逆命令

工具结果和应用记忆不记录输入正文、剪贴板内容或凭据。

## 当前范围

0.8.0-alpha.6 只正式打包 Windows 10/11 x64。macOS 和 Linux 将在 Windows 版本稳定
后适配；跨应用长链自治、主动巡检、语音无障碍和自动探索软件菜单不在本版本范围内。
