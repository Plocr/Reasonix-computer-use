# Reasonix Computer Use 0.8.0-alpha

Reasonix 专用的 Windows Computer Use 插件。任务完成优先，低 token 为优化目标。

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

## 仓库结构

```text
reasonix-computer-use/
├─ reasonix-plugin.json       Reasonix 原生插件清单
├─ reasonix_computer_use/     MCP 服务与 Windows 执行核心
├─ skills/app-control/        Agent 使用规则
├─ hooks/                     Windows SessionStart 与安全钩子
├─ scripts/                   自包含发行包构建脚本
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

## 系统画像

首次会话自动快速生成：

- `memory/system.md`：适合用户阅读的简短摘要
- `memory/system-index.json`：应用、硬件、显示器、DPI和 Known Folder 的权威索引
- `memory/apps/*.json`：按应用保存的已验证成功路径

桌面、文档和下载目录通过 Windows 注册表 Known Folder 读取，支持重定向到 D/E/F 等磁盘。应用搜索优先读取 App Paths、开始菜单、桌面快捷方式、卸载项和运行中窗口，保存精确 exe 目标。

## 安装

当前 Alpha 开发安装：

```powershell
python -m pip install -e .
reasonix plugin install . --link --replace --yes
reasonix plugin doctor computer-use
```

Reasonix Desktop 也可以从“插件 → 本地目录”选择本仓库。正式发行包会附带 Python 依赖和 RapidOCR/ONNX Runtime，普通用户不需要单独执行 pip。

维护者生成自包含发行包：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_release.ps1
```

脚本在构建机下载 Windows 嵌入式 Python，将所有依赖安装到包内 `runtime`，并生成可直接安装的 ZIP。最终用户只需解压或交给 Reasonix 安装，不运行 pip。

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

## 安全边界

以下操作默认暂停并要求用户确认或接管：

- 密码、验证码和 UAC
- 支付、购买和协议确认
- 删除、卸载和结束非目标进程
- 系统修改、外部写入和不可逆命令

工具日志只记录工具名和退出状态，不记录输入文本、剪贴板或凭据。

## 当前范围

0.8.0-alpha 只支持 Windows 10/11。macOS、Linux、跨应用长链自治、主动巡检、语音无障碍和自动探索软件菜单不在本版本范围内。
