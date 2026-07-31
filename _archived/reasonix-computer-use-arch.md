# Reasonix Computer Use — 项目概览

> 版本：v0.8.0-beta.3 | 路径：`E:\Agent\reasonix-computer-use`

## 项目本质

**Reasonix Computer Use** 是 Reasonix 编码 Agent 的**跨平台桌面自动化插件**。让 AI Agent 能像人一样操作电脑——启动应用、点击按钮、输入文字、截屏录屏——通过归一化坐标协议和"精准层优先，视觉层兜底"的感知策略，在不用本地 VLM 的前提下实现对 Windows/macOS/Linux 桌面的控制。

---

## 技术架构

```
┌─────────────────────────────────────────────────────────────┐
│                     AI Agent (Reasonix Host)                 │
│      调用 screen_interactor / computer_system / web_navigator│
└────────────────┬────────────────────────────────────────────┘
                 │ MCP stdio JSON-RPC
┌────────────────▼────────────────────────────────────────────┐
│                     mcp_server.py                           │
│               工具注册/分发/视觉守卫                         │
│   _import_tools(): 同步装依赖 → 后台画像+OCR预热            │
└────────────────┬────────────────────────────────────────────┘
                 │
    ┌────────────┼────────────┬────────────────┐
    ▼            ▼            ▼                ▼
┌─────────┐ ┌─────────┐ ┌──────────┐ ┌──────────────┐
│protocol/│ │platform/│ │perception│ │ services/    │
│坐标协议  │ │OS抽象层  │ │感知层    │ │ 系统画像     │
│4空间    │ │17方法   │ │UIA→OCR  │ │ 511应用索引  │
│换算引擎 │ │工厂模式  │ │黑名单   │ │ HW/GPU/存储  │
└─────────┘ └─────────┘ └──────────┘ └──────────────┘
                 │
    ┌────────────┴──────┬────────────────┐
    ▼                   ▼                ▼
┌──────────────┐ ┌───────────┐ ┌────────────────┐
│tools/        │ │hooks/     │ │ environment_   │
│screen_inter. │ │route_guard│ │ setup.py       │
│computer_sys. │ │fail-closed│ │ pip依赖安装    │
│web_navigator │ │激活门禁   │ │ Pillow/paddle  │
│hidden/       │ └───────────┘ └────────────────┘
└──────────────┘
```

---

## 核心模块详解

### protocol/ — 坐标协议

| 空间 | 范围 | 用途 |
|---|---|---|
| `CLAUDE_1024` | 0–1023 → 1024×768 | Claude 模型 |
| `GEMINI_1000` | 0–999 → 1000×1000 | Gemini 模型 |
| `PIXEL` | 物理像素 | 直接映射 |
| `ELEMENT_REF` | 元素 ID | 最稳定，推荐纯文本 LLM |

`CoordinateConverter` 读 `system-index.json` 的 `scale_factor` 换算为物理像素。

### perception/ — 感知降级策略

```
observe(window)
  ├─ precision 层 (UIA / AXAPI / AT-SPI2)
  │   └─ 首次返回空 → 加入 _precision_blacklist
  ├─ vision 层 (PaddleOCR PP-OCRv4)
  │   └─ 仅输出结构化 bbox+text，不做决策
  └─ 都不可用 → blocked=true
```

### platform/ — 跨平台抽象

- `PlatformProvider` 基类定义 17 个方法（鼠标4 / 键盘4 / 屏幕2 / 窗口4 / 录屏2）
- `WindowsPlatformProvider` 完整实现（Win32 API + Per-Monitor DPI V2）
- macOS/Linux 为桩代码（`available=False`，fallback 到 vision）

### tools/ — MCP 工具

| 工具 | 类型 | 用途 |
|---|---|---|
| `screen_interactor` | 公开核心 | observe + execute，自动附带 after 快照 |
| `computer_system` | 公开 | profile/diagnose/setup/command/window |
| `web_navigator` | 公开只读 | [BROWSER ONLY] 路由到 Playwright/mcp-chrome |
| `computer_app` | 公开兼容 | 启动/聚焦/关闭应用 |
| `computer_state` | 公开只读兼容 | observe 别名 |
| `computer_action` | 公开兼容 | execute 别名 |
| mouse_action | 隐藏 | 归一化坐标强制，Agent 不可见 |
| keyboard_action | 隐藏 | 归一化坐标强制，Agent 不可见 |
| screenshot | 隐藏 | 截图 → 用户 Downloads |
| screen_recorder | 隐藏 | 录屏 → 用户 Downloads |

---

## 文件树

```
E:\Agent\reasonix-computer-use\  (3.1MB)
│
├── reasonix-plugin.json              # 插件 manifest (v0.8.0-beta.3)
├── pyproject.toml                    # Python 项目配置
├── README.md                         # 文档
├── CLAUDE.md / USER_GUIDE.md         # 辅助文档
│
├── hooks/
│   └── route_guard.py                # fail-closed 激活门禁
│
├── commands/                         # 斜杠命令
│   ├── benchmark.md                  # 能力评分
│   ├── doctor.md                     # 环境诊断
│   ├── run.md                        # 运行
│   ├── test.md                       # 测试
│   └── trace.md                      # 轨迹导出
│
├── skills/
│   ├── computer-use/SKILL.md         # 桌面自动化规则
│   └── web-navigator/SKILL.md        # Web 路由规则
│
├── reasonix_computer_use/            # ★ 核心包
│   │
│   ├── protocol/                     # 坐标协议
│   │   ├── coordinates.py            # CoordinateSpace 枚举 + 换算引擎
│   │   └── snapshot.py               # ScreenSnapshot + ActionCommand
│   │
│   ├── platform/                     # OS 抽象层
│   │   ├── base.py                   # PlatformProvider (17抽象方法)
│   │   └── windows.py                # Win32实现 (DPI V2 + ctypes argtypes)
│   │
│   ├── perception/                   # 感知层
│   │   ├── base.py                   # PerceptionProvider 基类
│   │   ├── router.py                 # 降级路由 + precision黑名单
│   │   ├── precision/                # 精准层
│   │   │   ├── windows_uia.py        # Win32 UIA (comtypes)
│   │   │   ├── macos_axapi.py        # 桩代码
│   │   │   └── linux_atspi.py        # 桩代码 (Wayland检测)
│   │   └── vision/                   # 视觉层
│   │       ├── paddle_ocr.py         # PaddleOCR PP-OCRv4 (动态可用性)
│   │       └── paddle_vl.py          # PaddleOCR-VL-1.6 (可选桩)
│   │
│   ├── services/                     # 系统画像
│   │   └── system_profiler.py        # 511应用/HW/GPU/存储/显示器索引
│   │
│   ├── tools/                        # MCP 工具
│   │   ├── __init__.py               # 10个 @register_tool 注册
│   │   ├── screen_interactor.py      # 核心: observe+execute+自动after
│   │   ├── computer_system.py        # profile/diagnose/setup/window
│   │   ├── web_navigator.py          # [BROWSER ONLY] 路由代理
│   │   └── hidden/actions.py         # 鼠标/键盘/截图/录屏
│   │
│   ├── mcp_server.py                 # MCP stdio JSON-RPC 服务
│   ├── environment_setup.py          # pip依赖安装
│   ├── text_vision.py                # 向后兼容 shim
│   ├── ui_tree.py                    # 向后兼容 shim
│   └── 辅助模块                      # trace/input_guard/replay/vision_router
│
└── tests/                            # 36 测试
    ├── test_protocol_smoke.py        # 坐标协议 (16)
    ├── test_platform.py              # 平台抽象 (6)
    ├── test_services.py              # 系统画像 (6)
    └── test_perception.py            # 感知层 (8)
```

---

## 数据流

```
1. Agent 调用 screen_interactor(observe)
   → PerceptionRouter: precision(UIA) → vision(OCR) → ScreenSnapshot
   → 返回元素ID + bbox + text

2. Agent 调用 screen_interactor(execute, click_ref/e6)
   → _resolve_target(): ELEMENT_REF → fallback坐标 → 光标位置
   → CoordinateConverter: normalized_coord × scale_factor = 物理像素
   → PlatformProvider.mouse_click(x, y)
   → 自动附带 after 快照 (response["after"])

3. Agent 调用 computer_system(profile|diagnose|setup|window)
   → SystemProfiler / environment_setup / PlatformProvider
```

---

## 关键设计决策

| 决策 | 选择 | 理由 |
|---|---|---|
| 感知策略 | 精准层优先 → 视觉层兜底 | UIA 更准，OCR 兜底，不引入本地 VLM |
| 坐标协议 | 归一化 + ELEMENT_REF | 多模型兼容，跨分辨率回放 |
| 平台抽象 | get_platform() 工厂 | 一处改，处处生效 |
| 启动流程 | 同步装依赖 → 后台画像+OCR | MCP 握手不超时，感知层不缓存不可用 |
| 隐藏工具 | [HIDDEN] 标记，tools/list 过滤 | Agent 不可见，screen_interactor 内部调用 |
| 版本 | 0.8.0-beta.3 | 功能完整，跨平台桩代码就绪 |
