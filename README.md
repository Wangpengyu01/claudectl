# claudectl

> Claude Desktop / Claude Code 本地管理工具 —— 启动前检查、日志清理、设备标识重置、备份恢复、诊断。

不硬编码任何用户名、盘符、包名、版本号。拷到任何 Windows 机器上都能跑。

## 两种界面

| 文件 | 界面 | 依赖 |
|---|---|---|
| `claudectl_gui.py` | 图形界面（tkinter） | Python 3.8+，零第三方包 |
| `claudectl.ps1` | 命令行 TUI | Windows PowerShell 5.1+ |

---

## 快速开始

```powershell
# 图形界面（推荐）
python claudectl_gui.py
pythonw claudectl_gui.py       # 无控制台窗口

# 或双击
启动图形界面.bat
```

```powershell
# 命令行
.\claudectl.ps1                 # 交互菜单
.\claudectl.ps1 status          # 查看状态
.\claudectl.ps1 logs            # 清日志
.\claudectl.ps1 ids             # 重置设备标识
.\claudectl.ps1 diag            # 诊断
```

执行策略受限时：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\claudectl.ps1
```

---

## 功能

### 启动前检查（9 项）

打开图形界面即自动执行，检查通过后才启用「启动 Claude」按钮。

| 检查项 | 内容 |
|---|---|
| Claude Desktop | 是否安装、版本、路径 |
| Claude 进程 | 当前是否在运行 |
| **出口 IP** | IPv4/IPv6 归属、是否大陆、IPv6 是否泄露 |
| **系统时区** | 系统时区 vs 出口时区是否一致 |
| **界面语言** | 是否中文 |
| **设备标识** | `ant-did` / `machineID` / `userID` / 账号 / 登录态 |
| DNS | `api.anthropic.com`、`claude.ai` 解析 |
| **服务连通性** | TLS 握手测试，**不发送任何凭据** |
| 登录态 | Desktop 是否已登录 |

图标语义：`✓` 通过、`!` 警告、`✕` 异常、`i` 信息（正常但需知晓）。

### 使用手册

程序内点顶部「使用手册」按钮，弹窗含 5 个章节：

- **封号了怎么办** —— 按错误码分级处置
- **IP 要求（最关键）** —— 独立干净 IP vs 机场共享 IP，为什么共享 IP 会被判「蒸馏」
- **检查项说明** —— 9 项检查各自含义
- **功能用法** —— 各功能操作说明
- **常见问题** —— 改客户端能否绕过、重置能否解封、时差、数据位置

内容同步保存在 [`docs/使用手册.md`](docs/使用手册.md)。

### 日志清理

清 18 类存储，**保留登录态与设备标识**，清理后可直接使用：

```
sentry 队列 / sentry 作用域 / Session 存储 / Local 存储 / IndexedDB
HTTP 缓存 / JS 代码缓存 / GPU 缓存 / Dawn 缓存 ×2 / GPU 持久缓存
着色器缓存 ×2 / blob 存储 / Web 存储 / Crashpad / Cookies / Network 状态
```

### 重置设备标识 / 清空数据

勾选式对话框，分四类：

| 类别 | 内容 | 默认 |
|---|---|---|
| 设备标识与账号 | `ant-did` / 设备注册表 / salt / `oauth:tokenCache*` | ☑ |
| 配置与对话记录 | `~/.claude.json`、`~/.claude` 下 projects / sessions / settings | ☑ |
| 日志与缓存 | sentry / Cache / 各类存储（全部 userData 位置） | ☑ |
| 可选清理 | CC Switch 配置 / CLI 包 / 注册表协议处理器 | ☐ |

执行前自动备份到 `%LOCALAPPDATA%\claudectl\backups\wipe_<时间戳>\`。

### 备份管理

列出恢复点、查看详情、恢复、改目录。每个备份含 `identifiers.json`，记录当时的设备标识便于回滚。

### 高级配置

默认折叠。可设置 `ANTHROPIC_API_KEY` / 模型 / `BASE_URL` / `timeZone`，写入 `~/.claude/settings.json`。

---

## 通用性设计

### 全动态探测

| 项 | 探测方式 |
|---|---|
| Appx 包名 | `Get-AppxPackage` 匹配 `*Claude*` 或 Publisher 含 `Anthropic` |
| 程序路径 | 包 InstallLocation → 常见安装目录 → 运行中进程 Path |
| 启动入口 | `Get-StartApps` 匹配包族名取 AppID |
| userData | 四类位置全枚举 |
| CLI 路径 | PATH → npm 全局 → `npm config get prefix` → Desktop 内置 |
| 备份目录 | `%LOCALAPPDATA%\claudectl\backups`，可改 |

### userData 四类位置

| 位置 | 出现条件 |
|---|---|
| `%LOCALAPPDATA%\Packages\<包族名>\LocalCache\Roaming\Claude` | **MSIX 虚拟化（最常见）** |
| `%LOCALAPPDATA%\Claude-Data\<实例>` | 1p 模式经典位置 |
| `%LOCALAPPDATA%\Claude-3p` | 3p 模式 |
| `%APPDATA%\Claude` | 非打包安装 |

**关键坑**：MSIX 应用的进程命令行显示 `--user-data-dir=...\AppData\Roaming\Claude`，但磁盘上那个目录不存在 —— 实际写入被文件系统虚拟化重定向到包容器。工具会自动做这个映射。

---

## 兼容性

| 环境 | 状态 |
|---|---|
| Windows PowerShell 5.1 | ✅ |
| PowerShell 7+ | ✅ |
| Python 3.8+ | ✅ 图形界面 |
| Claude Desktop（Store/MSIX） | ✅ |
| Claude Desktop（非打包） | ✅ |
| Claude Code CLI | ✅ |
| 未安装 Claude | ✅ 优雅提示，不报错 |
| 中文路径 / 长路径 | ✅ `\\?\` + robocopy 兜底 |

---

## 安全设计

1. **先备份再动手** —— 所有破坏性操作自动备份，附 `identifiers.json`
2. **二次确认** —— 删除前列出具体影响与大小
3. **日志与标识分离** —— 清日志不动登录态
4. **长路径兜底** —— `Remove-Item` 失败自动 `robocopy /MIR`
5. **数组安全** —— 单元素结果统一 `@()` 包装
6. **跨线程安全** —— 后台线程回调窗口已销毁时静默忽略

---

## 明确不做

- **不修改** `MachineGuid`、磁盘序列号、SMBIOS UUID。Claude 只读 `MachineGuid` 且仅用于 OpenTelemetry 遥测，改硬件层收益为零、风险高。
- **不影响**服务端对账号的判定。`organization_on_hold` / `verification_required` 是账号层的，与本地设备无关。
- **不提供**任何绕过风控的方法。本工具只做本地数据管理。

---

## 排错

**工具找不到 Claude？**

```powershell
python claudectl_gui.py    # 看底部日志面板
```

若 userData 为空：

- 确认 Claude Desktop 至少启动过一次
- Store 版检查 `Get-AppxPackage *Claude*` 是否有输出
- 非打包版检查 `%APPDATA%\Claude` 是否存在

**中文乱码？**

- `claudectl.ps1` 必须保存为 **UTF-8 with BOM**
- 图形界面用 tkinter，依赖系统字体「Microsoft YaHei UI」

---

## 许可证

MIT
