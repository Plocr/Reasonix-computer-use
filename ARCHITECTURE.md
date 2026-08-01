# Reasonix Computer Use — 完整工程架构

> v0.8.0-beta.5 | 2026-07-31

---

## 1. 项目定位

**Reasonix Computer Use** 是 Reasonix 编码 Agent 的跨平台桌面自动化 MCP 插件。让 AI Agent 能像人一样操作电脑——通过归一化坐标协议和"精准层优先，视觉层兜底"的感知策略，在不引入本地视觉决策模型（VLM）的前提下，实现 Windows/macOS/Linux 桌面的完整控制。

**核心原则：** 插件不决策，只感知和执行。所有动作决策由宿主 Agent（VLM/LLM）完成。

---

## 2. 技术栈

| 维度 | 选择 | 理由 |
|---|---|---|
| **语言** | Python 3.10+ | Reasonix 插件标准，生态丰富 |
| **包管理** | hatchling / pip | pyproject.toml 声明依赖 |
| **进程通信** | MCP stdio JSON-RPC | Reasonix 插件协议标准 |
| **精准层 (Win)** | comtypes → UIAutomationCore | Win32 原生无障碍 API |
| **精准层 (Mac)** | PyObjC → AXAPI | macOS 原生 Accessibility |
| **精准层 (Linux)** | AT-SPI2 (D-Bus) | Linux 原生无障碍 |
| **视觉层 (文字)** | EasyOCR (GPU, CN+EN) | 中文 95%+ 准确率 |
| **视觉层 (UI)** | OpenCV 4.x | Canny 边缘 + 形态学 + 轮廓检测 |
| **截图** | Pillow (ImageGrab) | 跨平台屏幕捕获 |
| **输入模拟 (Win)** | ctypes → Win32 API | SetCursorPos / SendInput / keybd_event |
| **输入模拟 (Mac/Lin)** | 桩代码 → 待实现 | PyAutoGUI / xdotool |
| **坐标协议** | 归一化 4 空间 | CLAUDE_1024 / GEMINI_1000 / PIXEL / ELEMENT_REF |
| **系统画像** | Win32 API + PowerShell | CPU/GPU/存储/显示器/应用索引 |
| **应用发现** | Registry + Start Menu + UWP | PowerShell COM 解析 .lnk 真实路径 |
| **依赖安装** | pip subprocess | 分离进程 + target site-packages |

---

## 3. 工程架构 (分层设计)

```
┌──────────────────────────────────────────────────────────────┐
│                    AI Agent (Reasonix Host)                   │
│  screen_interactor / computer_system / computer_app / web_nav│
└────────────────────────┬─────────────────────────────────────┘
                         │ MCP stdio (JSON-RPC)
┌────────────────────────▼─────────────────────────────────────┐
│                    mcp_server.py                              │
│  ┌───────────────────────────────────────────────────────┐   │
│  │  main() loop: stdin→dispatch→handler→stdout            │   │
│  │  handle_initialize: serverInfo.version + capabilities  │   │
│  │  handle_tools_list: filter [HIDDEN] + annotations      │   │
│  │  handle_tools_call: _guard_visual_result + trace       │   │
│  │  _import_tools(): sync dep install + bg profile/OCR   │   │
│  └───────────────────────────────────────────────────────┘   │
└────────────────────────┬─────────────────────────────────────┘
                         │
    ┌────────────────────┼────────────────────┬─────────────────┐
    ▼                    ▼                    ▼                 ▼
┌───────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│ protocol/ │  │  platform/   │  │ perception/  │  │  services/   │
│           │  │              │  │              │  │              │
│ .坐标枚举  │  │ .抽象基类     │  │ .路由策略     │  │ .系统画像     │
│ .归一换算  │  │ .工厂方法     │  │ .UIA精准层   │  │ .应用发现     │
│ .快照格式  │  │ .Win32实现   │  │ .EasyOCR视觉 │  │ .硬件检测     │
│ .动作指令  │  │ .Mac/Lin桩   │  │ .OpenCV检测  │  │ .存储分析     │
└───────────┘  └──────────────┘  └──────────────┘  └──────────────┘
                         │
    ┌────────────────────┴───────────────────────┐
    ▼                                            ▼
┌──────────────┐                         ┌──────────────────┐
│   tools/     │                         │   environment_   │
│              │                         │   setup.py       │
│ .__init__    │← 10个 @register_tool    │                  │
│ .interactor  │  (6 public + 4 hidden) │ .pip依赖安装      │
│ .system      │                         │ .动态缺失检测    │
│ .navigator   │                         │ .分离进程执行    │
│ .hidden/     │                         └──────────────────┘
└──────────────┘
```

---

## 4. 核心数据流

### 4.1 observe (屏幕观察)

```
Agent: screen_interactor(mode="observe", window_id="...")
  │
  ├─ PerceptionRouter.observe(window_key)
  │   │
  │   ├─ 1. precision 层 (UIA/Windows)
  │   │      └─ comtypes→UIAutomationCore→BuildCache tree walk
  │   │      └─ 命中 → ElementRef(e1..eN, bbox=窗口物理像素)
  │   │      └─ 空 → 加入 _precision_blacklist(永久跳过)
  │   │
  │   ├─ 2. vision 层 (EasyOCR + OpenCV)
  │   │      └─ PlatformProvider.screenshot() → PIL Image → numpy BGR
  │   │      └─ detect_text(image_bgr) → EasyOCR → [{x,y,w,h,text,conf}]
  │   │      └─ detect_ui_components(image_bgr) → Canny+morph→contours
  │   │      └─ _draw_annotations() → 蓝框(文字) + 黄框(UI) → PNG
  │   │      └─ ElementRef(eocr_tN 文字, eocr_uN UI组件)
  │   │
  │   └─ 3. 都失败 → blocked=true
  │
  └─ ScreenSnapshot {revision, source, elements[N], width, height, scale_factor}
     └─ response += annotated_image 路径
```

### 4.2 execute (动作执行)

```
Agent: screen_interactor(mode="execute", actions=[{type, element_ref, fallback}])

1. ActionCommand.from_dict(raw) → 解析字段
   └─ key→keys 别名 (字符串自动包裹成列表)
   └─ press_key→press 类型别名

2. _resolve_target(snapshot, platform)
   └─ element_ref → snapshot.find_element(id) → bbox中心点
   └─ fallback → CoordinateConverter.to_physical(coord, window_rect)
      └─ scale_factor × 逻辑坐标 = 物理像素
      └─ 返回 (int(px), int(py))
   └─ 都无 → GetCursorPos (兜底)

3. _execute_one(cmd)
   ├─ click/double_click/right_click → mouse_click(x, y)
   ├─ type → keyboard_type(text) → SendInput(Unicode)
   ├─ press/press_key/key → keyboard_press(keys) → keybd_event
   ├─ scroll → mouse_scroll(x, y, amount)
   ├─ drag → mouse_drag(from, to)
   └─ wait → time.sleep(duration)

4. PlatformProvider.* → int(x), int(y) → Win32 API
   └─ SetCursorPos / SendInput / keybd_event — argtypes 已声明

5. Auto after: response["after"] = observe()  # 夹带轻量快照
```

### 4.3 应用启动 (tiered)

```
Agent: computer_app(launch, query="QQMusic")

1. 画像查找 (profiler.load_index → applications[])
   └─ 精确名匹配 → 模糊名匹配 → 找到完整 EXE 路径

2. os.startfile(resolved_path)  # Windows Shell 解析

3. 画像未命中 → computer_app(search, query)
   └─ profiler.profile("search rescan") → 重扫注册表+开始菜单+UWP
   └─ 返回匹配列表 → 更新画像 → 重试 launch

4. 仍未找到 → Agent 自主 Win+S 搜索 (兜底)
```

---

## 5. 归一化坐标协议

```
4 种坐标空间，插件内部统一换算为物理像素：

┌────────────────┬──────────┬────────────────────┐
│ 空间            │ 范围      │ 视口映射            │
├────────────────┼──────────┼────────────────────┤
│ ELEMENT_REF    │ N/A      │ 元素ID引用(首选)     │
│ PIXEL          │ 任意      │ 直接物理像素         │
│ CLAUDE_1024    │ 0–1023   │ 1024×768           │
│ GEMINI_1000    │ 0–999    │ 1000×1000          │
└────────────────┴──────────┴────────────────────┘

CoordinateConverter.from_system_index()
  → 读取 memory/system-index.json
  → scale_factor (from 显示器 DPI/96)
  → to_physical(coord) → int(px), int(py)
  → from_physical(px, py, space) → NormalizedCoord
```

---

## 6. 平台抽象 (跨平台)

```
PlatformProvider (base.py — 16 抽象方法)

┌───────────────────┬──────────────────────────────────────┐
│ 类别               │ 方法                                  │
├───────────────────┼──────────────────────────────────────┤
│ 鼠标 (4)          │ mouse_move/click/drag/scroll          │
│ 键盘 (4)          │ keyboard_type/press/key_down/key_up   │
│ 屏幕 (2)          │ screenshot/get_virtual_screen_rect    │
│ 窗口 (4)          │ list_windows/get_window_rect/         │
│                   │ activate_window/get_foreground_window │
│ 录屏 (2)          │ start_recording/stop_recording        │
└───────────────────┴──────────────────────────────────────┘

get_platform() 工厂 → WindowsPlatformProvider (完整)
                    → macOS AXAPI (PyObjC, 已实现)
                    → Linux 桩 (待 AT-SPI2 实现)
```

**Windows 实现细节：**
- Per-Monitor DPI V2 — SetThreadDpiAwarenessContext(-4)
- ctypes argtypes 声明 — SetCursorPos/mouse_event/SendInput 全部声明
- Lazy Pillow import — 仅 screenshot() 时 import ImageGrab
- 所有 int(x), int(y) — ctypes 边界强制转 int

---

## 7. 感知降级策略

```
PerceptionRouter.observe(window_key):

  precision(UIA) ──成功──→ ScreenSnapshot (元素 bbox=物理像素)
       │
       │ 空/异常 → 加入 _precision_blacklist
       │
  vision(EasyOCR+OpenCV) ──成功──→ ScreenSnapshot (标注PNG)
       │
       │ 失败
       │
  blocked=true → "Install EasyOCR or comtypes"
```

**黑名单机制：** `foreground` 自动解析为真实 HWND，确保同窗口不会重复尝试 precision。

---

## 8. MCP 工具清单

| # | 工具 | 类型 | 只读 | 用途 |
|---|------|------|------|------|
| 1 | `screen_interactor` | 核心 | - | observe + execute |
| 2 | `computer_system` | 管理 | - | profile/diagnose/setup/window |
| 3 | `web_navigator` | 路由 | ✅ | [BROWSER ONLY] Playwright/MCP |
| 4 | `computer_app` | 兼容 | - | launch/search/focus/close |
| 5 | `computer_state` | 兼容 | ✅ | observe 别名 |
| 6 | `computer_action` | 兼容 | - | execute 别名 |
| 7 | `mouse_action` | 隐藏 | - | 归一化坐标强制 |
| 8 | `keyboard_action` | 隐藏 | - | 归一化坐标强制 |
| 9 | `screenshot` | 隐藏 | - | → %Downloads% |
|10 | `screen_recorder` | 隐藏 | - | → %Downloads% |

---

## 9. 多端兼容状态

| 平台 | 精准层 | 视觉层 | 输入模拟 | 系统画像 | 状态 |
|------|--------|--------|----------|----------|------|
| **Windows 11** | UIA ✅ | EasyOCR+OpenCV ✅ | Win32 API ✅ | PowerShell ✅ | **生产可用** |
| **macOS** | AXAPI 桩 | EasyOCR+OpenCV ⚠️ | 桩 | Python fallback ⚠️ | 待实现 |
| **Linux (X11)** | AT-SPI2 桩 | EasyOCR+OpenCV ⚠️ | 桩 | Python fallback ⚠️ | 待实现 |
| **Linux (Wayland)** | 不可用 | EasyOCR+OpenCV ⚠️ | 不可用 | Python fallback ⚠️ | 待实现 |

**说明：**
- 🟢 生产可用 — 功能完整，测试通过
- 🟡 待实现 — 桩代码已就绪，缺平台特定实现
- 🔴 不可用 — Wayland 全局坐标 API 不可用，需视觉层兜底

---

## 10. 测试覆盖

```
tests/
├── test_protocol_smoke.py    (16)  坐标换算/枚举/快照
├── test_platform.py          (6)   PlatformProvider 接口/Windows 实例化
├── test_services.py          (6)   SystemProfiler/画像生成/渲染
├── test_perception.py        (8)   PerceptionRouter/降级/视觉层
└── test_integration_smoke.py (∞)  12模块导入+16方法+10工具+24动作类型
───────────────────────────────────
总计: 36 测试 + 集成烟雾测试
```

---

## 11. 文件树

```
E:\Agent\reasonix-computer-use\
│
├── reasonix-plugin.json              # 插件 manifest
├── pyproject.toml                    # Python 项目
├── README.md                         # 文档
├── reasonix-computer-use.bat         # MCP 启动脚本
│
├── hooks/route_guard.py              # fail-closed 激活门禁
├── commands/                         # 斜杠命令
├── skills/computer-use/SKILL.md      # 桌面操作规则+任务分解
├── skills/web-navigator/SKILL.md     # Web 路由规则
│
├── reasonix_computer_use/            # ★ 核心包
│   ├── protocol/                     # 坐标协议
│   │   ├── coordinates.py            # 4空间枚举+换算引擎
│   │   └── snapshot.py               # ScreenSnapshot+ActionCommand
│   ├── platform/                     # OS 抽象
│   │   ├── base.py                   # PlatformProvider (17方法)
│   │   ├── windows.py                # Win32 完整实现
│   │   └── __init__.py               # get_platform() 工厂
│   ├── perception/                   # 感知管道
│   │   ├── router.py                 # UIA→OCR 降级+黑名单
│   │   ├── precision/windows_uia.py  # comtypes UIA
│   │   └── vision/easy_ocr.py        # EasyOCR+OpenCV
│   ├── services/system_profiler.py   # 系统画像+应用发现
│   ├── tools/                        # MCP 工具
│   │   ├── __init__.py               # 10工具注册+单例
│   │   ├── screen_interactor.py      # observe+execute+after
│   │   ├── computer_system.py        # profile/diagnose/setup
│   │   ├── web_navigator.py          # [BROWSER ONLY]
│   │   └── hidden/actions.py         # 鼠标/键盘/截图/录屏
│   ├── mcp_server.py                 # MCP stdio JSON-RPC
│   ├── environment_setup.py          # pip 依赖安装
│   └── 辅助模块 (trace/input_guard)
│
└── tests/                            # 36 测试
```

---

## 12. 项目规模

| 指标 | 数值 |
|---|---|
| 总代码行数 | ~8,000 行 (Python) |
| 新架构模块 | 5 个 package (protocol/platform/perception/services/tools) |
| MCP 工具 | 10 个 (6 public + 4 hidden) |
| 测试数量 | 36 单元测试 + 集成烟雾 |
| 插件包大小 | 1.8 MB (不含 git) |
| 支持操作系统 | 3 (Windows/macOS/Linux) |
| 平台抽象方法 | 17 个 |
| 坐标空间 | 4 种 (ELEMENT_REF/CLAUDE_1024/GEMINI_1000/PIXEL) |
| 系统画像应用 | ~500 个 (注册表+开始菜单+UWP+运行中) |
| 动作类型 | 24 种 (含别名) |
