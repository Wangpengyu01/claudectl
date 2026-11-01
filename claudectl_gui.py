#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
claudectl GUI — Claude 启动前检查与管理工具

启动即自动诊断：出口 IP、时区、语言、DNS、API、登录态。
发现问题可一键修复，检查通过后再启动 Claude。

纯标准库（tkinter），零第三方依赖。
"""

from __future__ import annotations

import base64
import ctypes
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import urllib.request

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

APP_NAME = "claudectl"
APP_VERSION = "4.1"

# ============================================================ 常量

BG = "#1e1e1e"
BG2 = "#252526"
BG3 = "#2d2d30"
FG = "#d4d4d4"
FG_DIM = "#8a8a8a"
ACCENT = "#4ec9b0"
OK = "#6a9955"
WARN = "#dcdcaa"
BAD = "#f48771"
INFO = "#569cd6"

FONT_UI = ("Microsoft YaHei UI", 9)
FONT_MONO = ("Consolas", 9)
FONT_TITLE = ("Microsoft YaHei UI", 12, "bold")
FONT_H2 = ("Microsoft YaHei UI", 10, "bold")
FONT_BIG = ("Microsoft YaHei UI", 10)

LOG_TARGETS = [
    ("sentry 队列", os.path.join("sentry", "queue")),
    ("sentry 作用域", os.path.join("sentry", "scope_v3.json")),
    ("Session 存储", "Session Storage"),
    ("Local 存储", "Local Storage"),
    ("IndexedDB", "IndexedDB"),
    ("HTTP 缓存", "Cache"),
    ("JS 代码缓存", "Code Cache"),
    ("GPU 缓存", "GPUCache"),
    ("Dawn 缓存", "DawnGraphiteCache"),
    ("DawnWebGPU", "DawnWebGPUCache"),
    ("GPU 持久缓存", "GPUPersistentCache"),
    ("着色器缓存", "ShaderCache"),
    ("Gr 着色器", "GrShaderCache"),
    ("blob 存储", "blob_storage"),
    ("Web 存储", "WebStorage"),
    ("Crashpad", "Crashpad"),
    ("Cookies", os.path.join("Network", "Cookies")),
    ("Network 状态", os.path.join("Network", "Network Persistent State")),
]

# ============================================================ 使用手册

MANUAL_SECTIONS = [
    ("封号了怎么办", [
        ("先判断是哪种状态", [
            "登录 Claude 客户端，或点本工具「重新检查」，看报什么错：",
            "",
            "  organization_on_hold / organization has been disabled",
            "      → 组织被冻结。这是可申诉状态，服务端会给出申诉地址。",
            "",
            "  verification_required",
            "      → 需要身份验证。令牌还没签发，必须验完才能用。",
            "",
            "  oauth_not_allowed_for_organization",
            "      → 该组织禁用了 OAuth。改用 API key 即可，不是封号。",
            "",
            "  credit balance is too low / out_of_credits",
            "      → 额度或余额问题，不是封号。",
        ]),
        ("按状态处置", [
            "1. 组织被冻结（on_hold）",
            "   走申诉：https://console.anthropic.com/appeal",
            "   说明账号归属、使用场景、支付方式。这是唯一能恢复原账号的路。",
            "",
            "2. 需要身份验证",
            "   完成验证：真实证件、活体核验、支付主体核验。",
            "   信息必须与账号、支付方式、组织名一致，对不上会累积失败记录。",
            "",
            "3. OAuth 被禁",
            "   不用申诉。去 console.anthropic.com 建一个 API key，",
            "   在「配置」页填入即可，走的是另一套准入判断。",
            "",
            "4. 额度问题",
            "   充值、换模型、或等下个配额周期。",
        ]),
        ("不要做的事", [
            "× 不要在同一台机器、同一个出口 IP 上反复注册新账号。",
            "  这是最典型的关联信号，会让处置从「验证」升级到「冻结」。",
            "",
            "× 不要伪造身份材料去过验证。",
            "  这是整条链上唯一「做了就回不去」的动作。",
            "",
            "× 不要以为改客户端能绕过。",
            "  判定依据是令牌里的 account + org，不是客户端长什么样。",
            "  换客户端、改 UA、改指纹，拒绝结果一个字都不会变。",
        ]),
    ]),

    ("IP 要求（最关键）", [
        ("必须用自己做的独立干净 IP", [
            "Claude 的风控对出口 IP 极其敏感。要求：",
            "",
            "  ✓ 独享 IP —— 只有你在用",
            "  ✓ 干净 IP —— 没有被滥用记录",
            "  ✓ 稳定 IP —— 不要频繁更换",
            "  ✓ 非大陆 IP —— 大陆 IP 直接被拒",
            "",
            "推荐：自建 VPS + 独享出口，或住宅代理（residential proxy）。",
        ]),
        ("不要用机场 / 共享 IP", [
            "机场（公共代理服务）的 IP 有几个致命问题：",
            "",
            "  × 一个 IP 上同时有几十上百个用户",
            "  × 这些用户里必然有人在高频调用、批量注册、爬取",
            "  × 该 IP 在风控库里已被标记为「可疑来源」",
            "",
            "后果：你什么都没做，也会被连带判定。",
            "常见表现是「刚登录就要求验证」或「用了几天突然冻结」。",
        ]),
        ("为什么共享 IP 会被判「蒸馏」", [
            "「蒸馏」指用 Claude 的输出训练自己的模型。",
            "Anthropic 明令禁止，风控专门针对这个行为建模。",
            "",
            "共享 IP 为什么容易被误判：",
            "",
            "  1. 同一 IP 上大量结构化调用",
            "     别人在批量抓数据，特征和你正常的对话混在一起。",
            "",
            "  2. 请求模式高度相似",
            "     同一个 IP 的请求带着相似的 system prompt、相似的采样参数，",
            "     风控会认为这是同一个采集程序。",
            "",
            "  3. 输出被系统性留存",
            "     如果该 IP 的流量有全量落盘特征（比如中转站记录所有响应），",
            "     上游看到的是「这个来源在系统性收集输出」。",
            "",
            "  4. 并发与节奏异常",
            "     共享 IP 的总并发远超单人使用，触发批量行为阈值。",
            "",
            "结论：共享 IP 上，你无法控制别人的行为，但后果由你承担。",
        ]),
        ("本工具怎么帮你", [
            "「出口 IP」检查项会显示：",
            "  · IPv4 归属国家 / 城市 / ISP / 时区",
            "  · 是否大陆（大陆 = 红字异常）",
            "  · IPv6 是否泄露（IPv6 绕过代理会暴露真实地址）",
            "",
            "注意：工具只能告诉你「是不是大陆」，",
            "判断不了「是不是共享 IP」—— 那需要看 IP 的滥用记录，",
            "建议用 IP 信誉查询站点自查（如 ipqualityscore、 AbuseIPDB）。",
        ]),
    ]),

    ("检查项说明", [
        ("9 项检查各自含义", [
            "Claude Desktop",
            "    是否安装、版本、路径。未安装时启动按钮会禁用。",
            "",
            "Claude 进程",
            "    当前是否在运行。清理数据前会自动停止。",
            "",
            "出口 IP",
            "    IPv4/IPv6 归属。大陆 IP 标红，IPv6 泄露标红。",
            "",
            "系统时区",
            "    系统时区 vs 出口时区。不一致时 Claude 显示时间会有偏差，",
            "    只影响显示，不影响功能。可点「修改时区…」调整。",
            "",
            "界面语言",
            "    Windows 显示语言。非中文时提示可切换。",
            "",
            "设备标识",
            "    ant-did / machineID / userID / 账号 / 登录态。",
            "    异常（格式错、状态矛盾）时才标黄。",
            "",
            "DNS",
            "    api.anthropic.com 与 claude.ai 的解析结果。",
            "    解析失败说明 DNS 或代理有问题。",
            "",
            "服务连通性",
            "    对 claude.ai 和 api.anthropic.com 做 TLS 握手，",
            "    只测网络可达，不发送任何凭据。",
            "",
            "登录态",
            "    Desktop 是否已登录，以及账号 UUID。",
        ]),
    ]),

    ("功能用法", [
        ("重置设备标识 / 清空数据", [
            "点击后弹出勾选窗口，分四类：",
            "",
            "  设备标识与账号      —— 重置核心，默认勾选",
            "  配置与对话记录      —— ~/.claude 下的记录与配置",
            "  日志与缓存          —— sentry / Cache / 各类存储",
            "  可选清理            —— CC Switch / CLI / 注册表，默认不勾",
            "",
            "执行前会自动备份到：",
            "    %LOCALAPPDATA%\\claudectl\\backups\\wipe_<时间戳>\\",
            "",
            "提示：只想重置身份、保留聊天记录的话，",
            "取消勾选「整个 userData 目录」，只勾「日志与缓存」即可。",
        ]),
        ("清理日志", [
            "清理 18 类存储，保留登录态与设备标识。",
            "清理后可直接启动 Claude，不需要重新登录。",
            "适合：怀疑日志泄露了信息、或磁盘占用过大。",
        ]),
        ("备份管理", [
            "所有破坏性操作都会自动备份。",
            "在「备份」页可以查看、恢复、修改备份目录。",
            "每个备份里有 identifiers.json，记录当时的设备标识，便于回滚。",
        ]),
        ("高级配置", [
            "默认折叠，普通用户不需要动。",
            "",
            "  API key    —— 不登录 Claude 时使用；留空走客户端登录态",
            "  模型       —— 如 claude-opus-5-5",
            "  BASE_URL   —— 留空 = 官方 api.anthropic.com",
            "  timeZone   —— 如 Asia/Shanghai；留空跟随系统",
        ]),
    ]),

    ("常见问题", [
        ("改了客户端能绕过封号吗", [
            "不能。",
            "判定依据是请求头里的 Bearer token，token 里写着 account 和 org。",
            "服务端不需要认识你的客户端，只需要读令牌。",
            "换客户端、改 UA、改指纹、改 metadata，拒绝结果完全一致。",
        ]),
        ("重置设备标识能解封吗", [
            "不能解封，但能让本机变干净。",
            "封号是账号层的，与设备无关。",
            "重置的价值在于：换新账号时不继承旧账号的本地痕迹。",
            "如果新账号仍要求验证，那是账号层判定，与本机无关。",
        ]),
        ("为什么时间快 1 小时", [
            "Claude 网页端按出口 IP 的地区显示时间。",
            "你的出口在日本（UTC+9），本地在中国（UTC+8），所以快 1 小时。",
            "只是显示差异，请求里的时间戳由服务端按 UTC 计算，不受影响。",
            "想消除：把出口换成香港或国内节点（同为 UTC+8）。",
        ]),
        ("数据存在哪", [
            "Desktop 数据（含登录态、缓存、对话）：",
            "    %LOCALAPPDATA%\\Packages\\<包名>\\LocalCache\\Roaming\\Claude",
            "",
            "Claude Code 配置：",
            "    %USERPROFILE%\\.claude\\ 与 %USERPROFILE%\\.claude.json",
            "",
            "注意：MSIX 打包应用的进程命令行显示 %APPDATA%\\Claude，",
            "但实际被文件系统虚拟化重定向到上面的 Packages 路径。",
        ]),
    ]),
]


# ============================================================ 工具

def run_ps(cmd: str, timeout: int = 30) -> str:
    try:
        p = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return (p.stdout or "").strip()
    except Exception:
        return ""


def run_hidden(args, timeout=120, text=True):
    try:
        return subprocess.run(
            args, capture_output=True, text=text, timeout=timeout,
            encoding="utf-8" if text else None, errors="replace" if text else None,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        return None


def fmt_size(n) -> str:
    if not n:
        return "0 B"
    n = float(n)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return f"{int(n)} B" if u == "B" else f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} TB"


def long_path(p: str) -> str:
    p = os.path.abspath(p)
    if p.startswith("\\\\?\\"):
        return p
    if p.startswith("\\\\"):
        return "\\\\?\\UNC\\" + p[2:]
    return "\\\\?\\" + p


def remove_safe(path: str) -> bool:
    if not os.path.exists(long_path(path)):
        return True
    try:
        if os.path.isdir(long_path(path)):
            shutil.rmtree(long_path(path))
        else:
            os.remove(long_path(path))
        return True
    except Exception:
        try:
            empty = os.path.join(os.environ.get("TEMP", "."), f"_e_{os.getpid()}_{int(time.time()*1000)}")
            os.makedirs(empty, exist_ok=True)
            subprocess.run(["robocopy", empty, path, "/MIR", "/NFL", "/NDL", "/NJH", "/NJS", "/R:1", "/W:1"],
                           capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            shutil.rmtree(empty, ignore_errors=True)
            if os.path.isdir(long_path(path)):
                shutil.rmtree(long_path(path), ignore_errors=True)
            elif os.path.exists(long_path(path)):
                os.remove(long_path(path))
            return not os.path.exists(long_path(path))
        except Exception:
            return False


def path_size(path: str) -> int:
    lp = long_path(path)
    if not os.path.exists(lp):
        return 0
    if os.path.isfile(lp):
        try:
            return os.path.getsize(lp)
        except Exception:
            return 0
    t = 0
    for root, _d, files in os.walk(lp):
        for f in files:
            try:
                t += os.path.getsize(os.path.join(root, f))
            except Exception:
                pass
    return t


def path_count(path: str) -> int:
    lp = long_path(path)
    if not os.path.exists(lp):
        return 0
    if os.path.isfile(lp):
        return 1
    n = 0
    for _r, _d, files in os.walk(lp):
        n += len(files)
    return n


# ============================================================ 环境探测

class ClaudeEnv:
    def __init__(self):
        self.packages = []
        self.desktop_exe = None
        self.app_id = None
        self.userdata_all = []
        self.userdata = None
        self.cli = None
        self.backup_root = None
        self.config_file = None
        self._discover()

    def _discover(self):
        self._pkgs()
        self._exe()
        self._appid()
        self._userdata()
        self._active()
        self._cli()
        self._backup()

    def _pkgs(self):
        out = run_ps("Get-AppxPackage | Where-Object { $_.Name -like '*Claude*' -or "
                     "$_.PublisherDisplayName -like '*Anthropic*' } | "
                     "ForEach-Object { $_.Name + '|' + $_.Version + '|' + $_.InstallLocation + '|' + $_.PackageFamilyName }")
        self.packages = []
        for line in out.splitlines():
            p = line.strip().split("|")
            if len(p) >= 4 and p[0]:
                self.packages.append({"name": p[0], "version": p[1], "install": p[2], "family": p[3]})

    def _exe(self):
        for p in self.packages:
            c = os.path.join(p["install"], "app", "Claude.exe")
            if os.path.exists(c):
                self.desktop_exe = c
                return
        la = os.environ.get("LOCALAPPDATA", "")
        for c in (os.path.join(la, "Programs", "Claude", "Claude.exe"),
                  os.path.join(la, "AnthropicClaude", "Claude.exe"),
                  os.path.join(os.environ.get("ProgramFiles", ""), "Claude", "Claude.exe")):
            if c and os.path.exists(c):
                self.desktop_exe = c
                return
        out = run_ps("(Get-Process claude -EA SilentlyContinue | Select-Object -First 1).Path")
        if out and os.path.exists(out):
            self.desktop_exe = out

    def _appid(self):
        for p in self.packages:
            out = run_ps(f"Get-StartApps | Where-Object {{ $_.AppID -like '{p['family']}*' }} | "
                         "Select-Object -First 1 -ExpandProperty AppID")
            if out:
                self.app_id = out.splitlines()[0].strip()
                return
        out = run_ps("Get-StartApps | Where-Object { $_.Name -like '*Claude*' } | "
                     "Select-Object -First 1 -ExpandProperty AppID")
        if out:
            self.app_id = out.splitlines()[0].strip()

    def _userdata(self):
        roots = []
        la = os.environ.get("LOCALAPPDATA", "")
        for p in self.packages:
            v = os.path.join(la, "Packages", p["family"], "LocalCache", "Roaming", "Claude")
            if os.path.exists(v):
                roots.append(v)
        pkg = os.path.join(la, "Packages")
        if os.path.isdir(pkg):
            try:
                for d in os.listdir(pkg):
                    if "claude" in d.lower():
                        v = os.path.join(pkg, d, "LocalCache", "Roaming", "Claude")
                        if os.path.exists(v):
                            roots.append(v)
            except Exception:
                pass
        cd = os.path.join(la, "Claude-Data")
        if os.path.isdir(cd):
            subs = [os.path.join(cd, d) for d in os.listdir(cd) if os.path.isdir(os.path.join(cd, d))]
            roots.extend(subs if subs else [cd])
        for extra in (os.path.join(la, "Claude-3p"), os.path.join(os.environ.get("APPDATA", ""), "Claude")):
            if os.path.exists(extra):
                roots.append(extra)
        uniq = []
        for r in roots:
            if r in uniq:
                continue
            if any(os.path.exists(os.path.join(r, f)) for f in
                   ("config.json", "ant-did", "Local Storage", "Preferences")):
                uniq.append(r)
        self.userdata_all = uniq

    def _active(self):
        out = run_ps("Get-CimInstance Win32_Process -Filter \"Name LIKE '%laude%'\" | "
                     "Where-Object { $_.CommandLine -match '--user-data-dir=' } | "
                     "Select-Object -First 1 -ExpandProperty CommandLine")
        if out:
            m = re.search(r'--user-data-dir="?([^"\s]+)"?', out)
            if m:
                path = m.group(1).strip('"')
                if os.path.exists(path):
                    self.userdata = path
                    return
                if "AppData\\Roaming\\Claude" in path:
                    la = os.environ.get("LOCALAPPDATA", "")
                    for p in self.packages:
                        v = os.path.join(la, "Packages", p["family"], "LocalCache", "Roaming", "Claude")
                        if os.path.exists(v):
                            self.userdata = v
                            return
        if self.userdata_all:
            self.userdata = self.userdata_all[0]

    def _cli(self):
        for d in os.environ.get("PATH", "").split(os.pathsep):
            for n in ("claude.exe", "claude.cmd"):
                c = os.path.join(d, n)
                if os.path.exists(c):
                    if c.endswith(".cmd"):
                        alt = os.path.join(d, "node_modules", "@anthropic-ai", "claude-code", "bin", "claude.exe")
                        if os.path.exists(alt):
                            self.cli = alt
                            return
                    else:
                        self.cli = c
                        return
        cands = [
            os.path.join(os.environ.get("APPDATA", ""), "npm", "node_modules",
                         "@anthropic-ai", "claude-code", "bin", "claude.exe"),
            os.path.join(os.environ.get("ProgramFiles", ""), "nodejs", "node_modules",
                         "@anthropic-ai", "claude-code", "bin", "claude.exe"),
        ]
        pre = run_ps("npm config get prefix 2>$null")
        if pre and os.path.isdir(pre):
            cands.append(os.path.join(pre, "node_modules", "@anthropic-ai", "claude-code", "bin", "claude.exe"))
        for c in cands:
            if c and os.path.exists(c):
                self.cli = c
                return
        for root in self.userdata_all:
            cc = os.path.join(root, "claude-code")
            if os.path.isdir(cc):
                best, bt = None, 0
                for r, _d, fs in os.walk(cc):
                    for f in fs:
                        if f.lower() == "claude.exe":
                            p = os.path.join(r, f)
                            try:
                                t = os.path.getmtime(p)
                            except Exception:
                                t = 0
                            if t > bt:
                                best, bt = p, t
                if best:
                    self.cli = best
                    return

    def _backup(self):
        la = os.environ.get("LOCALAPPDATA", "")
        self.config_file = os.path.join(la, "claudectl", "config.json")
        root = None
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, encoding="utf-8") as f:
                    root = json.load(f).get("BackupRoot")
            except Exception:
                pass
        if not root:
            root = os.path.join(la, "claudectl", "backups")
        self.backup_root = root
        try:
            os.makedirs(root, exist_ok=True)
        except Exception:
            pass

    def save_backup_root(self, root):
        self.backup_root = root
        try:
            os.makedirs(os.path.dirname(self.config_file), exist_ok=True)
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump({"BackupRoot": root}, f, ensure_ascii=False, indent=2)
        except Exception:
            pass


# ============================================================ 数据读取

def claude_pids():
    out = run_ps("(Get-Process claude -EA SilentlyContinue).Id")
    return [int(x) for x in out.splitlines() if x.strip().isdigit()]


def stop_claude():
    if not claude_pids():
        return True
    run_ps("Get-Process claude -EA SilentlyContinue | Stop-Process -Force", timeout=20)
    for _ in range(10):
        time.sleep(0.5)
        if not claude_pids():
            return True
    return not claude_pids()


def start_claude(env):
    if env.app_id:
        try:
            subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{env.app_id}"])
            return True
        except Exception:
            pass
    if env.desktop_exe and os.path.exists(env.desktop_exe):
        try:
            subprocess.Popen([env.desktop_exe])
            return True
        except Exception:
            pass
    return False


def read_ids(env):
    r = {}
    ud = env.userdata
    if ud:
        ad = os.path.join(ud, "ant-did")
        if os.path.exists(ad):
            try:
                raw = open(ad, encoding="utf-8").read().strip()
                r["ant-did"] = base64.b64decode(raw).decode("utf-8")
            except Exception:
                r["ant-did"] = "(解码失败)"
        else:
            r["ant-did"] = "(未生成)"
    cj = os.path.join(os.environ.get("USERPROFILE", ""), ".claude.json")
    if os.path.exists(cj):
        try:
            j = json.load(open(cj, encoding="utf-8"))
            r["machineID"] = j.get("machineID") or "(空)"
            r["userID"] = j.get("userID") or "(空)"
        except Exception:
            r["machineID"] = "(解析失败)"
    else:
        r["machineID"] = "(.claude.json 不存在)"
    if ud:
        cp = os.path.join(ud, "config.json")
        if os.path.exists(cp):
            try:
                cfg = json.load(open(cp, encoding="utf-8"))
                if cfg.get("lastKnownAccountUuid"):
                    r["账号"] = cfg["lastKnownAccountUuid"]
                r["登录态"] = "已登录" if cfg.get("oauth:tokenCacheV2") else "未登录"
            except Exception:
                r["登录态"] = "(解析失败)"
        else:
            r["登录态"] = "无 config.json"
    return r


def read_settings():
    p = os.path.join(os.environ.get("USERPROFILE", ""), ".claude", "settings.json")
    if not os.path.exists(p):
        return {}
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return {}


def write_settings(cfg):
    d = os.path.join(os.environ.get("USERPROFILE", ""), ".claude")
    try:
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "settings.json"), "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def list_backups(env):
    root = env.backup_root
    if not os.path.isdir(root):
        return []
    out = []
    for n in os.listdir(root):
        p = os.path.join(root, n)
        if os.path.isdir(p):
            out.append({"name": n, "path": p,
                        "time": datetime.datetime.fromtimestamp(os.path.getmtime(p)),
                        "size": path_size(p)})
    out.sort(key=lambda x: x["time"], reverse=True)
    return out


def collect_wipe_targets(env):
    """按类别汇总本机所有 Claude 相关数据。

    返回 [dict(cat, key, label, paths, size, note, danger)]
    """
    LA = os.environ.get("LOCALAPPDATA", "")
    RA = os.environ.get("APPDATA", "")
    UP = os.environ.get("USERPROFILE", "")
    groups = []

    def add(cat, key, label, paths, note="", danger=False):
        real = [p for p in paths if p and os.path.exists(long_path(p))]
        if not real:
            return
        groups.append({
            "cat": cat, "key": key, "label": label,
            "paths": real, "size": sum(path_size(p) for p in real),
            "note": note, "danger": danger,
        })

    # ---- 1. 设备标识（userData 内的标识文件）
    if env.userdata:
        add("ids", "devids", "设备标识文件",
            [os.path.join(env.userdata, f) for f in
             ("ant-did", "ant-device-registry.json",
              "ccd-ids.json", "remote-control-state.json")],
            "ant-did / 设备注册表 / salt")

    # ---- 2. 账号与登录态（config.json 中的字段，单独处理）
    if env.userdata:
        cp = os.path.join(env.userdata, "config.json")
        if os.path.exists(cp):
            groups.append({
                "cat": "ids", "key": "account", "label": "账号与登录态字段",
                "paths": [cp], "size": 0,
                "note": "lastKnownAccountUuid / oauth:tokenCache* / 会话标记",
                "danger": False, "fields_only": True,
            })

    # ---- 3. Claude Code 配置
    add("config", "ccjson", "Claude Code 配置",
        [os.path.join(UP, ".claude.json"), os.path.join(UP, ".claude.json.backup")],
        "machineID / userID / projects 记录")

    # ---- 4. ~/.claude 下的记录与配置
    cd = os.path.join(UP, ".claude")
    if os.path.isdir(cd):
        subs = []
        for name in ("projects", "sessions", "file-history", "shell-snapshots",
                     "session-env", "telemetry", "history.jsonl", "settings.json",
                     "settings.local.json", "backups", "spools", "spares",
                     "scheduled-tasks", "downloads", "plugins", "cache"):
            p = os.path.join(cd, name)
            if os.path.exists(p):
                subs.append(p)
        add("config", "claude_dir", "对话记录与本地配置", subs,
            "projects / sessions / file-history / settings / plugins")

    # ---- 5. 日志与缓存（所有 userData 位置）
    for i, ud in enumerate(env.userdata_all):
        targets = []
        for rel in ("sentry", "Cache", "Code Cache", "GPUCache",
                    "DawnGraphiteCache", "DawnWebGPUCache", "GPUPersistentCache",
                    "ShaderCache", "GrShaderCache", "blob_storage", "WebStorage",
                    "IndexedDB", "Local Storage", "Session Storage",
                    "Network", "Crashpad", "Shared Dictionary", "Dictionaries",
                    "logs", "fcache", "DIPS", "declarative_performance_observer.db"):
            p = os.path.join(ud, rel)
            if os.path.exists(p):
                targets.append(p)
        add("logs", f"ud{i}", f"日志与缓存 ({os.path.basename(ud)})", targets,
            "sentry / 各类缓存 / 存储 / Cookies")

    # ---- 6. 其余 userData 整目录（彻底模式）
    for i, ud in enumerate(env.userdata_all):
        add("logs", f"udfull{i}", f"整个 userData 目录 ({os.path.basename(ud)})",
            [ud], "删除后 Desktop 会重建全新数据目录", danger=True)

    # ---- 7. CC Switch（含第三方 provider 密钥）
    add("extra", "ccswitch", "CC Switch 配置",
        [os.path.join(UP, ".cc-switch")],
        "含第三方 provider 与 API key，删除后需重新配置", danger=True)

    # ---- 8. Claude Code CLI
    add("extra", "cli", "Claude Code CLI（npm 全局包）",
        [os.path.join(RA, "npm", "node_modules", "@anthropic-ai", "claude-code"),
         os.path.join(UP, ".local", "bin", "claude.exe")],
        "卸载终端 claude 命令，可用 npm 重装", danger=True)

    # ---- 9. 本工具自身配置
    add("extra", "self", "claudectl 自身配置",
        [os.path.join(LA, "claudectl")],
        "备份目录设置；备份文件本身不受影响")

    return groups


def wipe_registry_protocol():
    """删除 claude:// 协议处理器注册表项。返回 (ok, msg)"""
    key = r"HKCU\Software\Classes\claude"
    out = run_ps(f"if (Test-Path 'Registry::{key}') {{ "
                 f"Remove-Item 'Registry::{key}' -Recurse -Force -EA SilentlyContinue; "
                 f"if (Test-Path 'Registry::{key}') {{ 'FAIL' }} else {{ 'OK' }} }} "
                 f"else {{ 'ABSENT' }}")
    if out == "OK":
        return True, "已删除 claude:// 协议处理器"
    if out == "ABSENT":
        return True, "claude:// 协议处理器不存在"
    return False, "删除失败（可能需要权限）"


def make_backup(env, items, tag):
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(env.backup_root, f"{tag}_{ts}")
    try:
        os.makedirs(dest, exist_ok=True)
        i = 0
        for it in items:
            if not os.path.exists(long_path(it)):
                continue
            i += 1
            name = f"{i:02d}_{os.path.basename(it)}"
            try:
                if os.path.isdir(long_path(it)):
                    shutil.copytree(long_path(it), long_path(os.path.join(dest, name)))
                else:
                    shutil.copy2(long_path(it), long_path(os.path.join(dest, name)))
            except Exception:
                pass
        ids = read_ids(env)
        ids["userDataPath"] = env.userdata
        ids["createdAt"] = datetime.datetime.now().isoformat()
        json.dump(ids, open(os.path.join(dest, "identifiers.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        return dest
    except Exception:
        return None


# ============================================================ 时区 / 语言

def get_system_tz():
    return run_ps("(Get-TimeZone).Id")


def get_ui_language():
    out = run_ps("(Get-UICulture).Name")
    return out or "?"


def list_timezones():
    """返回 [(Windows ID, DisplayName, 偏移)]

    DisplayName 含非 ASCII，走 Base64 传输避免 PowerShell 输出编码问题。
    """
    out = run_ps(
        "Get-TimeZone -ListAvailable | ForEach-Object { "
        "$b = [Text.Encoding]::UTF8.GetBytes($_.DisplayName); "
        "$_.Id + '|' + [Convert]::ToBase64String($b) + '|' + $_.BaseUtcOffset.ToString() "
        "}",
        timeout=60)
    tzs = []
    for line in out.splitlines():
        p = line.strip().split("|")
        if len(p) >= 3 and p[0]:
            disp = p[1]
            try:
                disp = base64.b64decode(p[1]).decode("utf-8")
            except Exception:
                pass
            tzs.append((p[0], disp, p[2]))
    tzs.sort(key=lambda x: x[2])       # 按 UTC 偏移排序，再按名称
    tzs.sort(key=lambda x: x[1])
    return tzs


def set_system_tz(tz_id):
    """需要管理员。返回 (ok, msg)"""
    r = run_ps(f"try {{ Set-TimeZone -Id '{tz_id}' -ErrorAction Stop; 'OK' }} catch {{ 'ERR:' + $_.Exception.Message }}", timeout=30)
    if r.startswith("OK"):
        return True, f"已设为 {tz_id}"
    if "ERR:" in r:
        return False, r.split("ERR:", 1)[1].strip()[:200]
    # 回退 tzutil
    r2 = run_ps(f"tzutil /s \"{tz_id}\" 2>&1")
    actual = get_system_tz()
    if actual.lower() == tz_id.lower():
        return True, f"已设为 {tz_id}"
    return False, (r2 or "需要管理员权限")[:200]


def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def relaunch_as_admin():
    """以管理员重新启动自身。"""
    try:
        script = os.path.abspath(sys.argv[0])
        params = " ".join(f'"{a}"' for a in sys.argv[1:])
        ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable,
                                            f'"{script}" {params}'.strip(), None, 1)
        return True
    except Exception:
        return False


# ============================================================ 诊断项

class CheckResult:
    def __init__(self, key, title, status, detail, fixable=False, fix_label=""):
        self.key = key
        self.title = title
        self.status = status      # ok / warn / bad / info
        self.detail = detail
        self.fixable = fixable
        self.fix_label = fix_label


# 大陆地区判定（ip-api 的 country / countryCode）
MAINLAND_CN = {"china", "cn"}
# 明确算「非大陆」的地区（即使属于中国）
NON_MAINLAND_CN = {"hong kong", "hk", "taiwan", "tw", "macao", "macau", "mo"}


def _http_get(url, timeout=8):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace").strip()
    except Exception:
        return None


def _ipv4():
    for url in ("https://api.ipify.org?format=json",
                "https://ipv4.icanhazip.com",
                "https://ifconfig.me/ip"):
        v = _http_get(url)
        if not v:
            continue
        try:
            if url.endswith("json"):
                ip = json.loads(v).get("ip")
            else:
                ip = v
            if ip and "." in ip:
                return ip.strip()
        except Exception:
            continue
    return None


def _ipv6():
    for url in ("https://api6.ipify.org?format=json",
                "https://ipv6.icanhazip.com",
                "https://v6.ident.me"):
        v = _http_get(url)
        if not v:
            continue
        try:
            if url.endswith("json"):
                ip = json.loads(v).get("ip")
            else:
                ip = v
            if ip and ":" in ip:
                return ip.strip()
        except Exception:
            continue
    return None


def _geo(ip):
    if not ip:
        return {}
    try:
        u = ("http://ip-api.com/json/" + ip +
             "?fields=status,country,countryCode,regionName,city,isp,as,timezone")
        with urllib.request.urlopen(u, timeout=10) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return {}


def _is_mainland(geo):
    """是否中国大陆。HK/TW/MO 不算。"""
    if not geo:
        return None
    c = (geo.get("country") or "").strip().lower()
    cc = (geo.get("countryCode") or "").strip().lower()
    if c in NON_MAINLAND_CN or cc in NON_MAINLAND_CN:
        return False
    return c in MAINLAND_CN or cc in MAINLAND_CN


def check_export_ip():
    """出口 IP：大陆 = 异常（Anthropic 不服务大陆），非大陆 = 正常。
    同时检查 IPv6，防止 IPv6 绕过代理泄露真实地址。"""
    ip4 = _ipv4()
    ip6 = _ipv6()

    lines = []
    bad = False
    warn = False

    # ---- IPv4
    if not ip4:
        lines.append("IPv4  查询失败（无网络或 DNS 故障）")
        bad = True
        g4 = {}
    else:
        g4 = _geo(ip4)
        mainland4 = _is_mainland(g4)
        tag = ""
        if mainland4 is True:
            tag = "  ✕ 大陆 IP"
            bad = True
        elif mainland4 is False:
            tag = "  ✓ 非大陆"
        lines.append(f"IPv4  {ip4}{tag}")
        if g4:
            lines.append(f"      {g4.get('country','?')} / {g4.get('regionName','?')} / {g4.get('city','?')}")
            lines.append(f"      {g4.get('isp','?')}")
            lines.append(f"      时区 {g4.get('timezone','?')}")

    # ---- IPv6
    if ip6:
        g6 = _geo(ip6)
        mainland6 = _is_mainland(g6)
        tag = ""
        if mainland6 is True:
            tag = "  ✕ 大陆 IP（会绕过代理泄露真实地址）"
            bad = True
        elif mainland6 is False:
            tag = "  ✓ 非大陆"
        lines.append("")
        lines.append(f"IPv6  {ip6}{tag}")
        if g6:
            lines.append(f"      {g6.get('country','?')} / {g6.get('regionName','?')} / {g6.get('city','?')}")
            lines.append(f"      {g6.get('isp','?')}")
    else:
        lines.append("")
        lines.append("IPv6  未启用（无 IPv6 出口）  ✓ 不会泄露")

    # ---- 结论
    if bad:
        status = "bad"
    elif warn:
        status = "warn"
    else:
        status = "ok"

    # 记录出口时区供时区检查复用
    CheckResult.last_export_tz = g4.get("timezone") if g4 else None

    return CheckResult("ip", "出口 IP", status, "\n".join(lines), fixable=False)


def check_timezone():
    sys_tz = get_system_tz()
    # 对比出口时区
    exp_tz = None
    try:
        ip = None
        with urllib.request.urlopen("https://api.ipify.org?format=json", timeout=8) as r:
            ip = json.loads(r.read().decode()).get("ip")
        if ip:
            with urllib.request.urlopen(f"http://ip-api.com/json/{ip}?fields=timezone", timeout=8) as r:
                exp_tz = json.loads(r.read().decode()).get("timezone")
    except Exception:
        pass

    detail = f"系统时区: {sys_tz}"
    if exp_tz:
        detail += f"\n出口时区: {exp_tz}"
        if exp_tz != sys_tz:
            detail += "\n\n两者不一致 —— 服务端会按出口地区显示时间"
            return CheckResult("tz", "系统时区", "warn", detail, fixable=True, fix_label="改为出口时区")
        detail += "\n一致 ✓"
        return CheckResult("tz", "系统时区", "ok", detail)
    return CheckResult("tz", "系统时区", "info", detail, fixable=True, fix_label="修改时区")


def check_language():
    lang = get_ui_language()
    detail = f"UI 语言: {lang}"
    if lang.lower().startswith("zh"):
        return CheckResult("lang", "界面语言", "ok", detail)
    return CheckResult("lang", "界面语言", "info", detail, fixable=True, fix_label="切换为中文")


def check_dns():
    lines = []
    bad = False
    for h in ("api.anthropic.com", "claude.ai"):
        out = run_ps(f"(Resolve-DnsName {h} -DnsOnly -Type A -EA SilentlyContinue).IPAddress")
        ips = ", ".join(x.strip() for x in out.splitlines() if x.strip())
        if ips:
            lines.append(f"{h} → {ips}")
        else:
            lines.append(f"{h} → 解析失败")
            bad = True
    return CheckResult("dns", "DNS", "bad" if bad else "ok", "\n".join(lines))


def check_service():
    """服务连通性：只做 HTTPS 握手，不发送任何凭据。

    普通用户只用客户端，不需要 API key，所以这里不测 API 请求。
    """
    import socket
    import ssl

    targets = [
        ("claude.ai", 443),
        ("api.anthropic.com", 443),
    ]
    lines = []
    fail = 0

    for host, port in targets:
        t0 = time.time()
        try:
            ctx = ssl.create_default_context()
            with socket.create_connection((host, port), timeout=8) as sock:
                with ctx.wrap_socket(sock, server_hostname=host) as ss:
                    proto = ss.version() or "?"
            ms = int((time.time() - t0) * 1000)
            lines.append(f"{host:<20} 可达  {ms} ms  ({proto})")
        except Exception as e:
            fail += 1
            name = type(e).__name__
            lines.append(f"{host:<20} 不可达  ({name})")

    if fail == 0:
        return CheckResult("svc", "服务连通性", "ok", "\n".join(lines))

    lines.append("")
    lines.append("客户端可能无法连接 Anthropic 服务。")
    lines.append("请检查网络、代理或防火墙设置。")
    return CheckResult("svc", "服务连通性", "bad", "\n".join(lines))


def check_login(env):
    ids = read_ids(env)
    state = ids.get("登录态", "(未知)")

    if not env.userdata:
        return CheckResult("login", "登录态", "info",
                           "尚未启动 Claude —— 启动后登录即可")

    detail = f"Desktop: {state}"
    if ids.get("账号"):
        detail += f"\n账号: {ids['账号']}"

    if state == "已登录":
        return CheckResult("login", "登录态", "ok", detail)

    # 未登录属于正常首次状态，不是故障
    detail += "\n\n首次使用请打开 Claude 登录账号。\n" \
              "如需跳过登录直接用 API key，可在「配置」页填写。"
    return CheckResult("login", "登录态", "info", detail)


def is_first_run(env):
    """判断是否首次使用（新用户）。"""
    if not env.packages and not env.desktop_exe:
        return True                      # 完全没装
    if not env.userdata:
        return True                      # 装了但没启动过
    ids = read_ids(env)
    if ids.get("登录态") in ("未登录", "无 config.json", "(未知)"):
        return True                      # 启动过但没登录
    return False


def check_install(env):
    """Claude Desktop 安装检查。"""
    if not env.packages and not env.desktop_exe:
        return CheckResult(
            "install", "Claude Desktop", "bad",
            "未检测到 Claude Desktop\n"
            "请先从 https://claude.ai/download 下载安装，\n"
            "或从 Microsoft Store 搜索 Claude。\n"
            "安装完成后点「重新检查」。",
            fixable=False)

    parts = []
    if env.packages:
        for p in env.packages:
            parts.append(f"{p['name']} {p['version']}")
    else:
        parts.append("(非打包安装)")
    if env.desktop_exe:
        parts.append(f"路径 {env.desktop_exe}")
    if env.app_id:
        parts.append(f"启动入口 {env.app_id}")

    # 有安装但无 userData = 从未启动
    if not env.userdata:
        return CheckResult(
            "install", "Claude Desktop", "info",
            "\n".join(parts) + "\n\n尚未启动过 —— 首次启动后会自动创建数据目录。",
            fixable=False)

    return CheckResult("install", "Claude Desktop", "ok", "\n".join(parts))


def check_device_ids(env):
    """设备标识检查。

    图标语义：
      i  正常（有标识，或未生成但属正常首次状态）
      !  可疑（有标识但格式异常 / 账号与登录态矛盾）
      ✕  异常（userData 都找不到）
    """
    if not env.userdata:
        # 未安装/未启动由 install 项负责报错，这里不重复标红
        if not env.packages and not env.desktop_exe:
            return CheckResult("ids", "设备标识", "info",
                               "跳过 —— 需先安装并启动 Claude", fixable=False)
        return CheckResult("ids", "设备标识", "info",
                           "尚未生成 —— 首次启动 Claude 后自动创建", fixable=False)

    ids = read_ids(env)
    lines = []
    suspicious = []

    ad = ids.get("ant-did", "(未生成)")
    mid = ids.get("machineID", "?")
    uid = ids.get("userID", "?")
    acct = ids.get("账号")
    login = ids.get("登录态", "?")

    # ant-did
    if ad == "(未生成)":
        lines.append("ant-did   未生成（首次启动 Claude 后自动创建，正常）")
    elif ad in ("(解码失败)", "(读取失败)", "(非 base64)"):
        lines.append(f"ant-did   {ad}")
        suspicious.append("ant-did 格式异常")
    elif len(ad) != 36 or ad.count("-") != 4:
        lines.append(f"ant-did   {ad}  ← 不是标准 UUID")
        suspicious.append("ant-did 非标准 UUID")
    else:
        lines.append(f"ant-did   {ad}")

    # machineID / userID
    def show(label, val):
        if not isinstance(val, str):
            return f"{label} {val}"
        if val.startswith("("):
            return f"{label} {val}"
        if len(val) == 64 and all(c in "0123456789abcdef" for c in val.lower()):
            return f"{label} {val[:16]}…  (sha256)"
        return f"{label} {val[:16]}…" if len(val) > 20 else f"{label} {val}"

    lines.append(show("machineID", mid))
    lines.append(show("userID   ", uid))

    if isinstance(mid, str) and mid.startswith("("):
        suspicious.append("machineID 缺失")
    if isinstance(uid, str) and uid.startswith("("):
        suspicious.append("userID 缺失")

    # 账号 / 登录态
    if acct:
        lines.append(f"账号      {acct}")
    lines.append(f"登录态    {login}")

    if login == "已登录" and not acct:
        suspicious.append("已登录但无账号记录")
    if login == "未登录" and acct:
        suspicious.append("有账号记录但未登录（可能刚重置过）")

    status = "warn" if suspicious else "info"
    if suspicious:
        lines.append("")
        lines.append("注意: " + "；".join(suspicious))

    return CheckResult("ids", "设备标识", status, "\n".join(lines),
                       fixable=True, fix_label="重置设备标识")


def check_process():
    pids = claude_pids()
    if pids:
        return CheckResult("proc", "Claude 进程", "ok", f"运行中（{len(pids)} 个进程）")
    return CheckResult("proc", "Claude 进程", "info", "未运行")


# ============================================================ 主窗口

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} v{APP_VERSION}")
        self.geometry("1020x760")
        self.minsize(900, 640)
        self.configure(bg=BG)

        self.env = ClaudeEnv()
        self.results = []
        self._busy = False
        self._checked = False

        self._style()
        self._build()
        self.welcome.pack_forget()      # 默认隐藏，检查完成后按需显示
        self.after(300, self.run_checks)

    # ---------- 样式
    def _style(self):
        st = ttk.Style(self)
        try:
            st.theme_use("clam")
        except Exception:
            pass
        st.configure(".", background=BG, foreground=FG, font=FONT_UI)
        st.configure("TFrame", background=BG)
        st.configure("Card.TFrame", background=BG2)
        st.configure("TLabel", background=BG, foreground=FG, font=FONT_UI)
        st.configure("Dim.TLabel", background=BG, foreground=FG_DIM, font=FONT_UI)
        st.configure("H2.TLabel", background=BG, foreground=ACCENT, font=FONT_H2)
        st.configure("Big.TLabel", background=BG, foreground=FG, font=FONT_BIG)
        st.configure("TButton", background="#333333", foreground=FG, borderwidth=0,
                     padding=(14, 7), font=FONT_UI)
        st.map("TButton", background=[("active", "#3f3f3f"), ("disabled", "#2a2a2a")],
               foreground=[("disabled", "#5a5a5a")])
        st.configure("Accent.TButton", background="#0e639c", foreground="#fff")
        st.map("Accent.TButton", background=[("active", "#1177bb"), ("disabled", "#2a3a45")])
        st.configure("Launch.TButton", background="#16825d", foreground="#fff",
                     font=("Microsoft YaHei UI", 11, "bold"), padding=(28, 12))
        st.map("Launch.TButton", background=[("active", "#1a9c6f"), ("disabled", "#2a3a33")])
        st.configure("Danger.TButton", background="#7a2f2f", foreground="#fff")
        st.map("Danger.TButton", background=[("active", "#9a3b3b")])
        st.configure("TNotebook", background=BG, borderwidth=0)
        st.configure("TNotebook.Tab", background=BG2, foreground=FG_DIM,
                     padding=(16, 7), font=FONT_UI, borderwidth=0)
        st.map("TNotebook.Tab", background=[("selected", BG)], foreground=[("selected", ACCENT)])
        st.configure("Treeview", background=BG2, fieldbackground=BG2, foreground=FG,
                     borderwidth=0, font=FONT_UI, rowheight=26)
        st.configure("Treeview.Heading", background="#333", foreground=FG_DIM,
                     borderwidth=0, font=FONT_UI)
        st.map("Treeview", background=[("selected", "#094771")])
        st.configure("TEntry", fieldbackground=BG3, foreground=FG, borderwidth=0, insertcolor=FG)
        st.configure("TCombobox", fieldbackground=BG3, foreground=FG, borderwidth=0)
        st.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=BG2, borderwidth=0)

    # ---------- 布局
    def _build(self):
        # 头部
        h = ttk.Frame(self, padding=(18, 14, 18, 6))
        h.pack(fill="x")
        self._header_frame = h
        ttk.Label(h, text=f"{APP_NAME}", font=FONT_TITLE, foreground=ACCENT).pack(side="left")
        ttk.Label(h, text=f"  v{APP_VERSION}   Claude 启动前检查", foreground=FG_DIM).pack(side="left")
        self.lbl_admin = ttk.Label(h, text="", foreground=FG_DIM, font=FONT_UI)
        self.lbl_admin.pack(side="right")
        ttk.Button(h, text="使用手册", command=self.do_manual).pack(
            side="right", padx=(0, 12))

        # 首次使用欢迎面板（默认隐藏）
        self.welcome = ttk.Frame(self, padding=(18, 6, 18, 0))
        self._build_welcome()

        # 检查区
        cf = ttk.Frame(self, padding=(18, 4, 18, 0))
        cf.pack(fill="both", expand=True)
        bar = ttk.Frame(cf)
        bar.pack(fill="x", pady=(0, 6))
        ttk.Label(bar, text="环境检查", style="H2.TLabel").pack(side="left")
        self.lbl_summary = ttk.Label(bar, text="", foreground=FG_DIM)
        self.lbl_summary.pack(side="left", padx=(12, 0))
        ttk.Button(bar, text="重新检查", command=self.run_checks).pack(side="right")

        # 检查结果列表
        cols = ("icon", "item", "detail")
        self.tree = ttk.Treeview(cf, columns=cols, show="headings", height=9)
        self.tree.heading("icon", text="")
        self.tree.heading("item", text="检查项")
        self.tree.heading("detail", text="结果")
        self.tree.column("icon", width=34, stretch=False, anchor="center")
        self.tree.column("item", width=130, stretch=False)
        self.tree.column("detail", width=760)
        self.tree.pack(fill="both", expand=True)
        self.tree.tag_configure("ok", foreground=OK)
        self.tree.tag_configure("warn", foreground=WARN)
        self.tree.tag_configure("bad", foreground=BAD)
        self.tree.tag_configure("info", foreground=INFO)

        # 修复按钮区
        fb = ttk.Frame(cf)
        fb.pack(fill="x", pady=(8, 0))
        self.fixbar = fb
        ttk.Label(fb, text="一键修复：", foreground=FG_DIM).pack(side="left")

        # 启动区
        lf = ttk.Frame(self, padding=(18, 10, 18, 4))
        lf.pack(fill="x")
        self.btn_launch = ttk.Button(lf, text="启动 Claude", style="Launch.TButton",
                                     command=self.do_launch)
        self.btn_launch.pack(side="left")
        self.lbl_launch = ttk.Label(lf, text="", foreground=FG_DIM)
        self.lbl_launch.pack(side="left", padx=(14, 0))
        ttk.Button(lf, text="关闭 Claude", command=self.do_stop).pack(side="right")

        # 标签页（管理功能）
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=18, pady=(6, 0))

        self.tab_ids = ttk.Frame(self.nb, padding=12)
        self.tab_logs = ttk.Frame(self.nb, padding=12)
        self.tab_backup = ttk.Frame(self.nb, padding=12)
        self.tab_config = ttk.Frame(self.nb, padding=12)
        self.nb.add(self.tab_ids, text="  设备标识  ")
        self.nb.add(self.tab_logs, text="  日志清理  ")
        self.nb.add(self.tab_backup, text="  备份  ")
        self.nb.add(self.tab_config, text="  配置  ")

        self._build_ids()
        self._build_logs()
        self._build_backup()
        self._build_config()

        # 状态栏
        sf = ttk.Frame(self, padding=(18, 6, 18, 10))
        sf.pack(fill="x")
        self.status = ttk.Label(sf, text="就绪", foreground=FG_DIM)
        self.status.pack(side="left")
        self.progress = ttk.Progressbar(sf, mode="indeterminate", length=150)
        self.progress.pack(side="right")

        # 日志面板
        lp = ttk.Frame(self, padding=(18, 0, 18, 10))
        lp.pack(fill="x")
        self.log = tk.Text(lp, height=4, bg=BG2, fg=FG_DIM, font=FONT_MONO,
                           borderwidth=0, wrap="none", state="disabled")
        sb = ttk.Scrollbar(lp, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        self.log.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        # 管理员提示
        if is_admin():
            self.lbl_admin.configure(text="管理员权限 ✓", foreground=OK)
        else:
            self.lbl_admin.configure(text="非管理员（改时区/语言需提权）", foreground=WARN)

    def _build_welcome(self):
        """首次使用引导面板。内容随缺失项变化。"""
        w = self.welcome
        self.welcome_title = tk.StringVar(value="")
        self.welcome_body = tk.StringVar(value="")
        self.welcome_steps = tk.StringVar(value="")

        box = tk.Frame(w, bg="#2a2d2e", highlightthickness=1,
                       highlightbackground="#3f3f46")
        box.pack(fill="x")
        inner = tk.Frame(box, bg="#2a2d2e")
        inner.pack(fill="x", padx=14, pady=12)

        self.lbl_welcome_title = tk.Label(
            inner, textvariable=self.welcome_title, bg="#2a2d2e", fg=ACCENT,
            font=("Microsoft YaHei UI", 11, "bold"), anchor="w", justify="left")
        self.lbl_welcome_title.pack(fill="x")

        self.lbl_welcome_body = tk.Label(
            inner, textvariable=self.welcome_body, bg="#2a2d2e", fg=FG,
            font=FONT_UI, anchor="w", justify="left")
        self.lbl_welcome_body.pack(fill="x", pady=(6, 0))

        self.lbl_welcome_steps = tk.Label(
            inner, textvariable=self.welcome_steps, bg="#2a2d2e", fg=FG_DIM,
            font=("Consolas", 9), anchor="w", justify="left")
        self.lbl_welcome_steps.pack(fill="x", pady=(8, 0))

        bar = tk.Frame(inner, bg="#2a2d2e")
        bar.pack(fill="x", pady=(10, 0))
        self.welcome_buttons = bar

    def _update_welcome(self):
        """根据环境更新欢迎面板。非首次使用则隐藏。"""
        env = self.env
        first = is_first_run(env)

        if not first:
            self.welcome.pack_forget()
            return

        for w in self.welcome_buttons.winfo_children():
            w.destroy()

        no_desktop = (not env.packages and not env.desktop_exe)
        no_userdata = (env.userdata is None)

        if no_desktop:
            self.welcome_title.set("欢迎使用 claudectl")
            self.welcome_body.set(
                "检测到本机尚未安装 Claude Desktop。\n"
                "本工具用于管理 Claude 的本地数据，需要先安装 Claude 才能使用完整功能。")
            self.welcome_steps.set(
                "1. 打开 https://claude.ai/download 下载安装\n"
                "2. 或从 Microsoft Store 搜索「Claude」\n"
                "3. 安装完成后点下方「重新检查」")
            ttk.Button(self.welcome_buttons, text="打开下载页",
                       style="Accent.TButton",
                       command=lambda: self._open_url("https://claude.ai/download")).pack(side="left")
        elif no_userdata:
            self.welcome_title.set("Claude 已安装，还没启动过")
            self.welcome_body.set(
                "首次启动 Claude 后会自动创建数据目录和设备标识，届时本工具即可完整管理。")
            self.welcome_steps.set(
                "1. 点下方「启动 Claude」\n"
                "2. 登录你的账号\n"
                "3. 回到本工具点「重新检查」")
        else:
            self.welcome_title.set("Claude 已安装，尚未登录")
            self.welcome_body.set(
                "登录后即可同步账号、订阅与用量信息。\n"
                "如果你只想用 API key（不登录账号），可直接在「配置」页填写。")
            self.welcome_steps.set(
                "方式 A：启动 Claude 并登录账号\n"
                "方式 B：切到「配置」页填入 ANTHROPIC_API_KEY")

        ttk.Button(self.welcome_buttons, text="重新检查",
                   command=self.run_checks).pack(side="left", padx=(8, 0))
        ttk.Button(self.welcome_buttons, text="跳过引导",
                   command=self._dismiss_welcome).pack(side="left")

        # 显示在 header 之后、检查区之前
        self.welcome.pack_forget()
        try:
            self.welcome.pack(fill="x", after=self._header_frame)
        except Exception:
            self.welcome.pack(fill="x")

    def _dismiss_welcome(self):
        self.welcome.pack_forget()

    def _open_url(self, url):
        try:
            os.startfile(url)
        except Exception:
            subprocess.Popen(["explorer.exe", url])

    # ---------- 设备标识页
    def _build_ids(self):
        f = self.tab_ids
        ttk.Label(f, text="设备标识", style="H2.TLabel").pack(anchor="w")
        ttk.Label(f, text="重置后需重新登录。", style="Dim.TLabel").pack(anchor="w", pady=(2, 8))
        self.tree_ids = ttk.Treeview(f, columns=("k", "v"), show="headings", height=8)
        self.tree_ids.heading("k", text="项")
        self.tree_ids.heading("v", text="值")
        self.tree_ids.column("k", width=180, stretch=False)
        self.tree_ids.column("v", width=700)
        self.tree_ids.pack(fill="both", expand=True)
        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=(8, 0))
        ttk.Button(bar, text="刷新", command=self.refresh_ids).pack(side="left")
        ttk.Button(bar, text="重置设备标识", style="Danger.TButton",
                   command=self.do_clear_ids).pack(side="right")

    # ---------- 日志页
    def _build_logs(self):
        f = self.tab_logs
        ttk.Label(f, text="日志清理", style="H2.TLabel").pack(anchor="w")
        ttk.Label(f, text="保留登录态与设备标识，清理后可立即使用。",
                  style="Dim.TLabel").pack(anchor="w", pady=(2, 8))
        self.tree_logs = ttk.Treeview(f, columns=("k", "v"), show="headings", height=12)
        self.tree_logs.heading("k", text="类别")
        self.tree_logs.heading("v", text="大小 / 文件数")
        self.tree_logs.column("k", width=220, stretch=False)
        self.tree_logs.column("v", width=280)
        self.tree_logs.pack(fill="both", expand=True)
        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=(8, 0))
        ttk.Button(bar, text="刷新", command=self.refresh_logs).pack(side="left")
        ttk.Button(bar, text="清理全部日志", style="Accent.TButton",
                   command=self.do_clear_logs).pack(side="right")

    # ---------- 备份页
    def _build_backup(self):
        f = self.tab_backup
        ttk.Label(f, text="备份管理", style="H2.TLabel").pack(anchor="w")
        top = ttk.Frame(f)
        top.pack(fill="x", pady=(4, 8))
        self.lbl_bak = ttk.Label(top, text="", style="Dim.TLabel")
        self.lbl_bak.pack(side="left")
        ttk.Button(top, text="改目录", command=self.do_change_backup).pack(side="right", padx=(6, 0))
        ttk.Button(top, text="刷新", command=self.refresh_backup).pack(side="right")
        self.tree_bak = ttk.Treeview(f, columns=("n", "t", "s"), show="headings", height=10)
        self.tree_bak.heading("n", text="名称")
        self.tree_bak.heading("t", text="时间")
        self.tree_bak.heading("s", text="大小")
        self.tree_bak.column("n", width=360, stretch=False)
        self.tree_bak.column("t", width=160, stretch=False)
        self.tree_bak.column("s", width=120)
        self.tree_bak.pack(fill="both", expand=True)
        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=(8, 0))
        ttk.Button(bar, text="查看详情", command=self.do_show_backup).pack(side="left")
        ttk.Button(bar, text="恢复选中", style="Accent.TButton",
                   command=self.do_restore).pack(side="right")

    # ---------- 配置页
    def _build_config(self):
        f = self.tab_config

        # ---------- 顶部说明（普通用户只需知道这些） ----------
        ttk.Label(f, text="高级设置", style="H2.TLabel").pack(anchor="w")
        ttk.Label(f,
                  text="普通用户不需要改这里 —— 直接在 Claude 客户端里登录即可。\n"
                       "以下选项仅用于：使用 API key 而不登录、或接入第三方网关。",
                  style="Dim.TLabel", justify="left").pack(anchor="w", pady=(2, 12))

        # ---------- 折叠开关 ----------
        self.var_advanced = tk.BooleanVar(value=False)
        cb = ttk.Checkbutton(f, text="显示 Claude Code 高级配置",
                             variable=self.var_advanced,
                             command=self._toggle_advanced)
        cb.pack(anchor="w")

        # ---------- 折叠区 ----------
        self.adv_frame = ttk.Frame(f)
        g = ttk.Frame(self.adv_frame)
        g.pack(fill="x", pady=(10, 0))
        g.columnconfigure(1, weight=1)

        self.var_key = tk.StringVar()
        self.var_model = tk.StringVar()
        self.var_base = tk.StringVar()
        self.var_tz = tk.StringVar()

        rows = [
            ("API key", self.var_key, True,
             "不登录 Claude 时使用；留空表示走客户端登录态"),
            ("模型", self.var_model, False,
             "如 claude-opus-5-5；留空用默认"),
            ("BASE_URL", self.var_base, False,
             "留空 = 官方 api.anthropic.com"),
            ("timeZone", self.var_tz, False,
             "如 Asia/Shanghai；留空跟随系统"),
        ]
        for i, (lb, var, sec, hint) in enumerate(rows):
            ttk.Label(g, text=lb, style="Dim.TLabel").grid(
                row=i * 2, column=0, sticky="w", padx=(0, 12))
            ttk.Entry(g, textvariable=var, font=FONT_MONO,
                      show="•" if sec else "").grid(
                row=i * 2, column=1, sticky="ew", pady=(5, 0))
            ttk.Label(g, text=hint, style="Dim.TLabel").grid(
                row=i * 2 + 1, column=1, sticky="w", pady=(0, 6))

        bar = ttk.Frame(self.adv_frame)
        bar.pack(fill="x", pady=(10, 0))
        ttk.Button(bar, text="重新载入",
                   command=self.refresh_config).pack(side="left")
        ttk.Button(bar, text="保存", style="Accent.TButton",
                   command=self.do_save_config).pack(side="right")

        # 默认折叠
        self.adv_frame.pack_forget()

    def _toggle_advanced(self):
        if self.var_advanced.get():
            self.adv_frame.pack(fill="x")
        else:
            self.adv_frame.pack_forget()


    # ======================================================== 通用

    def logmsg(self, m, color=FG_DIM):
        self.log.configure(state="normal")
        self.log.insert("end", f"[{datetime.datetime.now():%H:%M:%S}] {m}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def set_status(self, m):
        self.status.configure(text=m)

    def run_async(self, fn, done=None, label="处理中…"):
        if self._busy:
            return
        self._busy = True
        self.set_status(label)
        self.progress.start(12)

        def w():
            err = res = None
            try:
                res = fn()
            except Exception:
                err = traceback.format_exc()
            self._safe_after(lambda: self._fin(res, err, done))
        threading.Thread(target=w, daemon=True).start()

    def _safe_after(self, fn):
        """后台线程回调：窗口已销毁时静默忽略，避免 RuntimeError。"""
        try:
            if self.winfo_exists():
                self.after(0, fn)
        except Exception:
            pass

    def _fin(self, res, err, done):
        self._busy = False
        self.progress.stop()
        self.set_status("就绪")
        if err:
            self.logmsg("失败: " + err.splitlines()[-1], BAD)
            messagebox.showerror("失败", err.splitlines()[-1])
            return
        if done:
            done(res)

    # ======================================================== 检查流程

    def run_checks(self):
        if self._busy:
            return
        self.tree.delete(*self.tree.get_children())
        for w in self.fixbar.winfo_children():
            if not isinstance(w, ttk.Label):
                w.destroy()
        self.lbl_summary.configure(text="检查中…", foreground=FG_DIM)
        self.btn_launch.configure(state="disabled")
        self.lbl_launch.configure(text="检查完成前无法启动", foreground=FG_DIM)

        def job():
            results = []
            steps = [
                ("install", lambda: check_install(self.env)),
                ("proc", lambda: check_process()),
                ("ip", lambda: check_export_ip()),
                ("tz", lambda: check_timezone()),
                ("lang", lambda: check_language()),
                ("ids", lambda: check_device_ids(self.env)),
                ("dns", lambda: check_dns()),
                ("svc", lambda: check_service()),
                ("login", lambda: check_login(self.env)),
            ]
            for key, fn in steps:
                self._safe_after( lambda k=key: self._pending_row(k))
                try:
                    r = fn()
                except Exception as e:
                    r = CheckResult(key, key, "bad", str(e))
                results.append(r)
                self._safe_after( lambda rr=r: self._update_row(rr))
            return results

        def done(results):
            self.results = results
            self._checked = True
            self._render_fixes()
            ok = sum(1 for r in results if r.status == "ok")
            warn = sum(1 for r in results if r.status == "warn")
            bad = sum(1 for r in results if r.status == "bad")
            parts = []
            if ok: parts.append(f"{ok} 项通过")
            if warn: parts.append(f"{warn} 项警告")
            if bad: parts.append(f"{bad} 项异常")
            self.lbl_summary.configure(
                text=" · ".join(parts) if parts else "",
                foreground=(BAD if bad else (WARN if warn else OK)))
            # 未安装 Claude 时禁用启动按钮
            no_desktop = (not self.env.packages and not self.env.desktop_exe)
            if no_desktop:
                self.btn_launch.configure(state="disabled")
                self.lbl_launch.configure(text="请先安装 Claude Desktop", foreground=BAD)
            else:
                self.btn_launch.configure(state="normal")
                if bad:
                    self.lbl_launch.configure(text="有异常项，仍可启动", foreground=WARN)
                else:
                    self.lbl_launch.configure(text="检查通过，可以启动", foreground=OK)
            self.logmsg(f"检查完成：{self.lbl_summary.cget('text')}")
            self._update_welcome()
            self.refresh_ids()
            self.refresh_logs()
            self.refresh_backup()
            self.refresh_config()

        self.run_async(job, done, "检查环境中…")

    ICONS = {"ok": "✓", "warn": "!", "bad": "✕", "info": "i", "pending": "…"}

    def _pending_row(self, key):
        if self.tree.exists(key):
            return
        self.tree.insert("", "end", iid=key, values=("…", key, "检查中…"), tags=("info",))

    def _update_row(self, r):
        icon = self.ICONS.get(r.status, "?")
        detail = r.detail.replace("\n", "  |  ")
        vals = (icon, r.title, detail)
        if self.tree.exists(r.key):
            self.tree.item(r.key, values=vals, tags=(r.status,))
        else:
            self.tree.insert("", "end", iid=r.key, values=vals, tags=(r.status,))

    def _render_fixes(self):
        for w in self.fixbar.winfo_children():
            if not isinstance(w, ttk.Label):
                w.destroy()
        any_fix = False
        for r in self.results:
            if not r.fixable:
                continue
            any_fix = True
            if r.key == "tz":
                b = ttk.Button(self.fixbar, text="修改时区…",
                               command=self.do_fix_timezone)
            elif r.key == "lang":
                b = ttk.Button(self.fixbar, text="切换语言…",
                               command=self.do_fix_language)
            elif r.key == "ids":
                b = ttk.Button(self.fixbar, text="重置设备标识",
                               style="Danger.TButton", command=self.do_clear_ids)
            else:
                continue
            b.pack(side="left", padx=(0, 8))
        # 常用维护按钮始终显示
        ttk.Button(self.fixbar, text="清理日志",
                   command=self.do_clear_logs).pack(side="left", padx=(8, 0))
        if not any_fix:
            ttk.Label(self.fixbar, text="无需修复", foreground=OK).pack(side="left", padx=(8, 0))

    # ---------- 修复：时区
    def do_fix_timezone(self):
        win = tk.Toplevel(self)
        win.title("修改时区")
        win.geometry("520x420")
        win.configure(bg=BG)
        win.transient(self)

        ttk.Label(win, text="选择时区", style="H2.TLabel").pack(anchor="w", padx=14, pady=(14, 6))
        cur = get_system_tz()
        ttk.Label(win, text=f"当前：{cur}", style="Dim.TLabel").pack(anchor="w", padx=14)

        frame = ttk.Frame(win)
        frame.pack(fill="both", expand=True, padx=14, pady=10)
        lb = tk.Listbox(frame, bg=BG2, fg=FG, font=FONT_MONO, borderwidth=0,
                        selectbackground="#094771", highlightthickness=0)
        sb = ttk.Scrollbar(frame, orient="vertical", command=lb.yview)
        lb.configure(yscrollcommand=sb.set)
        lb.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        self.logmsg("读取时区列表…")
        tzs = list_timezones()
        for tz_id, disp, off in tzs:
            lb.insert("end", f"{off:>9}  {tz_id:<32}  {disp}")
        self._tz_list = tzs
        self.logmsg(f"共 {len(tzs)} 个时区")

        # 常用项预选
        preferred = ["China Standard Time", "Tokyo Standard Time",
                     "Singapore Standard Time", "UTC"]
        for i, (tz_id, _d, _o) in enumerate(tzs):
            if tz_id in preferred:
                lb.itemconfig(i, foreground=ACCENT)
        for i, (tz_id, _d, _o) in enumerate(tzs):
            if tz_id == cur:
                lb.selection_set(i)
                lb.see(i)
                break

        bar = ttk.Frame(win)
        bar.pack(fill="x", padx=14, pady=(0, 14))
        ttk.Label(bar, text="提示：修改系统时区需要管理员权限",
                  style="Dim.TLabel").pack(side="left")

        def apply():
            sel = lb.curselection()
            if not sel:
                return
            tz_id = tzs[sel[0]][0]
            if not messagebox.askyesno("确认", f"把系统时区改为：\n\n{tz_id}\n\n继续？", parent=win):
                return
            ok, msg = set_system_tz(tz_id)
            if ok:
                self.logmsg(msg, OK)
                win.destroy()
                self.run_checks()
            else:
                self.logmsg("失败: " + msg, BAD)
                if not is_admin():
                    if messagebox.askyesno("需要提权",
                                           "修改时区需要管理员权限。\n\n以管理员身份重启本程序？"):
                        if relaunch_as_admin():
                            self.destroy()
                            return
                messagebox.showerror("失败", msg, parent=win)

        ttk.Button(bar, text="应用", style="Accent.TButton", command=apply).pack(side="right")

    # ---------- 修复：语言
    def do_fix_language(self):
        win = tk.Toplevel(self)
        win.title("界面语言")
        win.geometry("420x260")
        win.configure(bg=BG)
        win.transient(self)

        ttk.Label(win, text="Windows 显示语言", style="H2.TLabel").pack(anchor="w", padx=14, pady=(14, 6))
        cur = get_ui_language()
        ttk.Label(win, text=f"当前 UI 语言：{cur}", style="Dim.TLabel").pack(anchor="w", padx=14)

        ttk.Label(win, text="\n修改系统显示语言需要管理员权限，\n且通常需要重启资源管理器或注销才生效。\n"
                            "Claude 自身的界面语言跟随系统。",
                  style="Dim.TLabel", justify="left").pack(anchor="w", padx=14, pady=(10, 0))

        bar = ttk.Frame(win)
        bar.pack(fill="x", padx=14, pady=14, side="bottom")

        def open_settings():
            try:
                subprocess.Popen(["ms-settings:regionlanguage"])
            except Exception:
                os.startfile("ms-settings:regionlanguage")

        ttk.Button(bar, text="打开语言设置", style="Accent.TButton",
                   command=open_settings).pack(side="right")
        ttk.Button(bar, text="关闭", command=win.destroy).pack(side="right", padx=(0, 8))

    # ======================================================== 使用手册

    def do_manual(self):
        """使用手册弹窗：左侧目录 + 右侧正文。"""
        win = tk.Toplevel(self)
        win.title("使用手册 — claudectl")
        win.geometry("960x680")
        win.minsize(780, 520)
        win.configure(bg=BG)
        win.transient(self)

        # ---------- 顶部 ----------
        top = ttk.Frame(win, padding=(16, 12, 16, 6))
        top.pack(fill="x")
        ttk.Label(top, text="使用手册", style="H2.TLabel").pack(side="left")
        ttk.Label(top, text="  封号处置 · IP 要求 · 功能说明 · 常见问题",
                  foreground=FG_DIM).pack(side="left")
        ttk.Button(top, text="关闭", command=win.destroy).pack(side="right")

        # ---------- 主体：左目录 + 右正文 ----------
        body = ttk.Frame(win, padding=(16, 0, 16, 14))
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        # 左：目录
        left = tk.Frame(body, bg=BG2, width=210)
        left.grid(row=0, column=0, sticky="ns", padx=(0, 12))
        left.grid_propagate(False)

        # 右：正文
        right = tk.Frame(body, bg=BG)
        right.grid(row=0, column=1, sticky="nsew")

        txt = tk.Text(right, bg=BG2, fg=FG, font=FONT_UI, borderwidth=0,
                      wrap="word", padx=16, pady=12,
                      spacing1=2, spacing3=2)
        sb = ttk.Scrollbar(right, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        txt.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        txt.tag_configure("h1", font=("Microsoft YaHei UI", 13, "bold"),
                          foreground=ACCENT, spacing1=14, spacing3=8)
        txt.tag_configure("h2", font=("Microsoft YaHei UI", 10, "bold"),
                          foreground=INFO, spacing1=10, spacing3=4)
        txt.tag_configure("body", font=FONT_UI, foreground=FG, lmargin1=14,
                          lmargin2=14)
        txt.tag_configure("warn", font=FONT_UI, foreground=BAD, lmargin1=14,
                          lmargin2=14)
        txt.tag_configure("ok", font=FONT_UI, foreground=OK, lmargin1=14,
                          lmargin2=14)
        txt.configure(state="disabled")

        # 目录项
        nav_buttons = []

        def show_section(idx):
            title, subs = MANUAL_SECTIONS[idx]
            txt.configure(state="normal")
            txt.delete("1.0", "end")

            txt.insert("end", title + "\n", "h1")
            for st, lines in subs:
                txt.insert("end", st + "\n", "h2")
                for line in lines:
                    if line.startswith("×"):
                        tag = "warn"
                    elif line.strip().startswith("✓"):
                        tag = "ok"
                    else:
                        tag = "body"
                    txt.insert("end", line + "\n", tag)
                txt.insert("end", "\n")

            txt.configure(state="disabled")
            txt.yview_moveto(0)

            for i, b in enumerate(nav_buttons):
                if i == idx:
                    b.configure(bg=BG3, fg=ACCENT)
                else:
                    b.configure(bg=BG2, fg=FG_DIM)

        for i, (title, _subs) in enumerate(MANUAL_SECTIONS):
            b = tk.Button(left, text=title, bg=BG2, fg=FG_DIM,
                          activebackground=BG3, activeforeground=ACCENT,
                          font=FONT_UI, bd=0, anchor="w", padx=12, pady=8,
                          relief="flat", cursor="hand2",
                          command=lambda i=i: show_section(i))
            b.pack(fill="x")
            nav_buttons.append(b)

        # 底部提示
        tk.Label(left, text="\n提示：手册内容也保存在\n仓库 docs/使用手册.md",
                 bg=BG2, fg=FG_DIM, font=("Microsoft YaHei UI", 8),
                 justify="left", anchor="w", padx=12).pack(side="bottom", fill="x")

        show_section(0)

    # ======================================================== 启动 / 关闭

    def do_launch(self):
        if not self._checked:
            messagebox.showinfo("提示", "请先等待检查完成")
            return

        def job():
            was_running = bool(claude_pids())
            if was_running:
                self._safe_after(lambda: self.logmsg("Claude 已在运行，先关闭再重启…"))
                stop_claude()
            ok = start_claude(self.env)
            time.sleep(3)
            return ok, len(claude_pids())

        def done(res):
            ok, n = res
            if ok and n > 0:
                self.logmsg(f"Claude 已启动（{n} 个进程）", OK)
                self.lbl_launch.configure(text="已启动", foreground=OK)
            elif ok:
                self.logmsg("已发送启动请求，进程尚未就绪", WARN)
            else:
                self.logmsg("启动失败：找不到启动入口", BAD)
                messagebox.showerror("启动失败", "找不到 Claude 的启动入口")
            self.refresh_ids()

        self.run_async(job, done, "启动 Claude…")

    def do_stop(self):
        def job():
            return stop_claude()

        def done(ok):
            if ok:
                self.logmsg("Claude 已关闭", OK)
                self.lbl_launch.configure(text="", foreground=FG_DIM)
            else:
                self.logmsg("关闭失败", BAD)
            self.refresh_ids()

        self.run_async(job, done, "关闭 Claude…")

    # ======================================================== 刷新各页

    def refresh_ids(self):
        self.tree_ids.delete(*self.tree_ids.get_children())
        for k, v in read_ids(self.env).items():
            self.tree_ids.insert("", "end", values=(k, v))

    def refresh_logs(self):
        self.tree_logs.delete(*self.tree_logs.get_children())
        if not self.env.userdata:
            return
        total = 0
        for lb, rel in LOG_TARGETS:
            p = os.path.join(self.env.userdata, rel)
            if not os.path.exists(long_path(p)):
                continue
            sz = path_size(p)
            total += sz
            self.tree_logs.insert("", "end", values=(lb, f"{fmt_size(sz)} / {path_count(p)} 个"))
        self.tree_logs.insert("", "end", values=("合计", fmt_size(total)))

    def refresh_backup(self):
        self.lbl_bak.configure(text=self.env.backup_root or "")
        self.tree_bak.delete(*self.tree_bak.get_children())
        for b in list_backups(self.env):
            self.tree_bak.insert("", "end", values=(
                b["name"], b["time"].strftime("%Y-%m-%d %H:%M"), fmt_size(b["size"])))

    def refresh_config(self):
        cfg = read_settings()
        e = cfg.get("env", {}) if isinstance(cfg, dict) else {}
        self.var_key.set(e.get("ANTHROPIC_API_KEY", "") or "")
        self.var_model.set(e.get("ANTHROPIC_MODEL", "") or "")
        self.var_base.set(e.get("ANTHROPIC_BASE_URL", "") or "")
        self.var_tz.set(cfg.get("timeZone", "") or "")

    # ======================================================== 操作

    def do_clear_logs(self):
        if not self.env.userdata:
            return
        items = [(lb, os.path.join(self.env.userdata, rel)) for lb, rel in LOG_TARGETS
                 if os.path.exists(long_path(os.path.join(self.env.userdata, rel)))]
        if not items:
            messagebox.showinfo("提示", "没有可清理的项")
            return
        total = sum(path_size(p) for _l, p in items)
        if not messagebox.askyesno("确认", f"清理 {len(items)} 项，约 {fmt_size(total)}？\n\n"
                                          "保留登录态与设备标识。"):
            return

        def job():
            was = bool(claude_pids())
            if was and not stop_claude():
                return ("fail", "无法停止 Claude")
            bak = make_backup(self.env, [p for _l, p in items], "logs")
            ok = bad = 0
            for _l, p in items:
                if remove_safe(p):
                    ok += 1
                else:
                    bad += 1
            return ("done", ok, bad, bak, was)

        def done(res):
            if res[0] == "fail":
                messagebox.showerror("失败", res[1])
                return
            _t, ok, bad, bak, was = res
            self.logmsg(f"清理完成 {ok} 成功 / {bad} 失败", OK)
            if bak:
                self.logmsg(f"备份: {bak}")
            self.refresh_logs()
            if was and messagebox.askyesno("完成", "清理完成。重新启动 Claude？"):
                self.do_launch()

        self.run_async(job, done, "清理日志…")

    def do_clear_ids(self):
        """打开勾选式清空对话框。"""
        groups = collect_wipe_targets(self.env)
        if not groups:
            messagebox.showinfo("提示", "本机未发现 Claude 相关数据")
            return

        win = tk.Toplevel(self)
        win.title("重置设备标识 / 清空 Claude 数据")
        win.geometry("780x640")
        win.configure(bg=BG)
        win.transient(self)
        win.grab_set()

        # ---- 标题
        ttk.Label(win, text="选择要清除的数据", style="H2.TLabel").pack(
            anchor="w", padx=16, pady=(14, 2))
        ttk.Label(win,
                  text="勾选后点「执行清除」。会先自动备份，可随时恢复。\n"
                       "标红的项目影响较大，请确认后再勾。",
                  style="Dim.TLabel", justify="left").pack(anchor="w", padx=16)

        # ---- 列表容器
        body = ttk.Frame(win)
        body.pack(fill="both", expand=True, padx=16, pady=10)

        canvas = tk.Canvas(body, bg=BG, highlightthickness=0)
        sb = ttk.Scrollbar(body, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        def on_wheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", on_wheel)

        # ---- 按类别分组
        CAT_NAMES = {
            "ids": "设备标识与账号（必选，重置核心）",
            "config": "配置与对话记录",
            "logs": "日志与缓存",
            "extra": "可选清理（影响较大）",
        }
        CAT_DEFAULT = {"ids": True, "config": True, "logs": True, "extra": False}

        vars_map = {}
        last_cat = None
        for g in groups:
            cat = g["cat"]
            if cat != last_cat:
                ttk.Label(inner, text=CAT_NAMES.get(cat, cat),
                          font=("Microsoft YaHei UI", 10, "bold"),
                          foreground=ACCENT).pack(anchor="w", pady=(12, 4))
                last_cat = cat

            var = tk.BooleanVar(value=CAT_DEFAULT.get(cat, True))
            vars_map[g["key"]] = (var, g)

            row = ttk.Frame(inner)
            row.pack(fill="x", pady=1)

            cb = tk.Checkbutton(
                row, variable=var, bg=BG,
                activebackground=BG, selectcolor=BG3,
                highlightthickness=0, bd=0)
            cb.pack(side="left")

            color = BAD if g.get("danger") else FG
            lbl = tk.Label(row, text=g["label"], bg=BG, fg=color,
                           font=FONT_UI, anchor="w")
            lbl.pack(side="left")

            size_txt = fmt_size(g["size"]) if g["size"] > 0 else "字段级"
            tk.Label(row, text=size_txt, bg=BG, fg=FG_DIM,
                     font=FONT_MONO, anchor="e", width=10).pack(side="right")

            hint = tk.Label(inner, text="    " + g["note"], bg=BG, fg=FG_DIM,
                            font=("Microsoft YaHei UI", 8), anchor="w")
            hint.pack(fill="x", padx=(24, 0))

        # ---- 注册表协议处理器
        var_reg = tk.BooleanVar(value=False)
        ttk.Label(inner, text=CAT_NAMES["extra"],
                  font=("Microsoft YaHei UI", 10, "bold"),
                  foreground=ACCENT).pack(anchor="w", pady=(12, 4))
        row_reg = ttk.Frame(inner)
        row_reg.pack(fill="x", pady=1)
        tk.Checkbutton(row_reg, variable=var_reg, bg=BG, activebackground=BG,
                       selectcolor=BG3, highlightthickness=0, bd=0).pack(side="left")
        tk.Label(row_reg, text="claude:// 协议处理器（注册表）", bg=BG, fg=BAD,
                 font=FONT_UI, anchor="w").pack(side="left")
        tk.Label(inner, text="    删除后点击 claude:// 链接不再唤起客户端",
                 bg=BG, fg=FG_DIM, font=("Microsoft YaHei UI", 8),
                 anchor="w").pack(fill="x", padx=(24, 0))

        # ---- 底部
        foot = ttk.Frame(win)
        foot.pack(fill="x", padx=16, pady=(0, 14))

        lbl_total = ttk.Label(foot, text="", font=("Microsoft YaHei UI", 10),
                              foreground=FG)
        lbl_total.pack(side="left")

        def recalc(*_):
            tot = sum(g["size"] for v, g in vars_map.values() if v.get())
            n = sum(1 for v, _g in vars_map.values() if v.get())
            if var_reg.get():
                n += 1
            lbl_total.configure(
                text=f"已选 {n} 类 · 预计释放 {fmt_size(tot)}")

        for v, _g in vars_map.values():
            v.trace_add("write", recalc)
        var_reg.trace_add("write", recalc)
        recalc()

        def do_execute():
            selected = [g for v, g in vars_map.values() if v.get()]
            if not selected and not var_reg.get():
                messagebox.showinfo("提示", "没有勾选任何项目", parent=win)
                return

            tot = sum(g["size"] for g in selected)
            danger = [g["label"] for g in selected if g.get("danger")]
            msg = f"将清除 {len(selected)} 类数据，约 {fmt_size(tot)}。\n\n"
            msg += "会先自动备份到：\n" + self.env.backup_root + "\n\n"
            if danger:
                msg += "注意，以下项目影响较大：\n"
                for d in danger:
                    msg += f"  · {d}\n"
                msg += "\n"
            msg += "清除后 Claude 需要重新登录。继续？"

            if not messagebox.askyesno("最终确认", msg, parent=win):
                return

            win.destroy()
            self._execute_wipe(selected, var_reg.get())

        ttk.Button(foot, text="取消", command=win.destroy).pack(side="right")
        ttk.Button(foot, text="执行清除", style="Danger.TButton",
                   command=do_execute).pack(side="right", padx=(0, 8))

    def _execute_wipe(self, selected, do_registry):
        """真正执行清除。"""
        def job():
            if not stop_claude():
                return ("fail", "无法停止 Claude，请手动关闭后重试")

            # ---- 备份
            bak_items = []
            for g in selected:
                if g.get("fields_only"):
                    continue
                bak_items.extend(g["paths"])
            bak = make_backup(self.env, bak_items, "wipe")

            removed = []
            failed = []
            fields_dropped = 0

            for g in selected:
                if g.get("fields_only"):
                    # config.json 只删字段
                    cp = g["paths"][0]
                    try:
                        cfg = json.load(open(cp, encoding="utf-8"))
                        pat = re.compile(
                            r"lastKnownAccountUuid|oauth:tokenCache|bootSideSessions|"
                            r"sshTransportFlagLastAnswer|lastSeenRequire")
                        for k in [k for k in list(cfg) if pat.search(k)]:
                            cfg.pop(k, None)
                            fields_dropped += 1
                        json.dump(cfg, open(cp, "w", encoding="utf-8"),
                                  ensure_ascii=False, indent="\t")
                        removed.append(f"config.json ({fields_dropped} 字段)")
                    except Exception as e:
                        failed.append(f"config.json: {e}")
                    continue

                for p in g["paths"]:
                    if remove_safe(p):
                        removed.append(os.path.basename(p.rstrip("\\/")) or p)
                    else:
                        failed.append(os.path.basename(p.rstrip("\\/")) or p)

            reg_msg = None
            if do_registry:
                ok, m2 = wipe_registry_protocol()
                reg_msg = m2
                if ok:
                    removed.append("claude:// 注册表")
                else:
                    failed.append("claude:// 注册表")

            return ("done", removed, failed, bak, reg_msg)

        def done(res):
            if res[0] == "fail":
                messagebox.showerror("失败", res[1])
                return
            _t, removed, failed, bak, reg_msg = res
            self.logmsg("=" * 50, FG_DIM)
            self.logmsg(f"清除完成：{len(removed)} 项成功，{len(failed)} 项失败", OK)
            if bak:
                self.logmsg(f"备份: {bak}", FG_DIM)
            if reg_msg:
                self.logmsg(reg_msg, OK if "已删除" in reg_msg else WARN)
            if failed:
                self.logmsg("失败项: " + ", ".join(failed[:6]), BAD)

            self.refresh_ids()
            self.refresh_logs()
            self.refresh_backup()
            self.run_checks()

            tail = "\n\nClaude 需要重新登录。" if not failed else \
                   "\n\n部分项目清除失败，请查看日志面板。"
            messagebox.showinfo("完成", f"已清除 {len(removed)} 项数据。{tail}")

        self.run_async(job, done, "清除数据…")

    def do_change_backup(self):
        d = filedialog.askdirectory(title="选择备份目录", initialdir=self.env.backup_root)
        if d:
            self.env.save_backup_root(d)
            self.refresh_backup()
            self.logmsg(f"备份目录 → {d}", OK)

    def do_show_backup(self):
        sel = self.tree_bak.selection()
        if not sel:
            messagebox.showinfo("提示", "请先选中一个备份")
            return
        name = self.tree_bak.item(sel[0], "values")[0]
        b = next((x for x in list_backups(self.env) if x["name"] == name), None)
        if not b:
            return
        lines = []
        try:
            for n in sorted(os.listdir(b["path"])):
                p = os.path.join(b["path"], n)
                lines.append(f"{'<目录>' if os.path.isdir(p) else fmt_size(os.path.getsize(p)):>12}  {n}")
        except Exception as e:
            lines.append(str(e))
        idf = os.path.join(b["path"], "identifiers.json")
        if os.path.exists(idf):
            lines.append("\n--- identifiers.json ---")
            try:
                lines.append(open(idf, encoding="utf-8").read())
            except Exception:
                pass
        w = tk.Toplevel(self)
        w.title(f"备份详情 — {b['name']}")
        w.geometry("700x500")
        w.configure(bg=BG)
        t = tk.Text(w, bg=BG2, fg=FG, font=FONT_MONO, borderwidth=0, wrap="none")
        t.pack(fill="both", expand=True, padx=10, pady=10)
        t.insert("1.0", "\n".join(lines))
        t.configure(state="disabled")

    def do_restore(self):
        sel = self.tree_bak.selection()
        if not sel:
            messagebox.showinfo("提示", "请先选中一个备份")
            return
        name = self.tree_bak.item(sel[0], "values")[0]
        b = next((x for x in list_backups(self.env) if x["name"] == name), None)
        if not b or not self.env.userdata:
            return
        if not messagebox.askyesno("确认", f"恢复 {name}？会覆盖当前状态。"):
            return

        MAP = {
            "Network_Cookies": os.path.join("Network", "Cookies"),
            "Network_Cookies-journal": os.path.join("Network", "Cookies-journal"),
            "Network_Trust Tokens": os.path.join("Network", "Trust Tokens"),
            "Network_Trust Tokens-journal": os.path.join("Network", "Trust Tokens-journal"),
            "Network_TransportSecurity": os.path.join("Network", "TransportSecurity"),
            "Network_Network Persistent State": os.path.join("Network", "Network Persistent State"),
            "sentry_scope_v3.json": os.path.join("sentry", "scope_v3.json"),
            "queue": os.path.join("sentry", "queue"),
        }

        def job():
            if not stop_claude():
                return ("fail", "无法停止 Claude")
            ud = self.env.userdata
            n = 0
            for nm in os.listdir(b["path"]):
                if nm == "identifiers.json":
                    continue
                src = os.path.join(b["path"], nm)
                real = re.sub(r"^\d{2}_", "", nm)
                real = re.sub(r"^dir_", "", real)
                dst = os.path.join(ud, MAP.get(real, real))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                try:
                    if os.path.isdir(src):
                        if os.path.exists(long_path(dst)):
                            remove_safe(dst)
                        shutil.copytree(long_path(src), long_path(dst))
                    else:
                        shutil.copy2(long_path(src), long_path(dst))
                    n += 1
                except Exception:
                    pass
            return ("done", n)

        def done(res):
            if res[0] == "fail":
                messagebox.showerror("失败", res[1])
                return
            self.logmsg(f"恢复 {res[1]} 项", OK)
            self.refresh_backup()
            self.run_checks()

        self.run_async(job, done, "恢复中…")

    def do_save_config(self):
        cfg = read_settings()
        if not isinstance(cfg, dict):
            cfg = {}
        env = cfg.get("env")
        if not isinstance(env, dict):
            env = {}
        if self.var_key.get().strip():
            env["ANTHROPIC_API_KEY"] = self.var_key.get().strip()
        if self.var_model.get().strip():
            env["ANTHROPIC_MODEL"] = self.var_model.get().strip()
        b = self.var_base.get().strip()
        if b:
            env["ANTHROPIC_BASE_URL"] = b
        else:
            env.pop("ANTHROPIC_BASE_URL", None)
        cfg["env"] = env
        tz = self.var_tz.get().strip()
        if tz:
            cfg["timeZone"] = tz
        else:
            cfg.pop("timeZone", None)
        if write_settings(cfg):
            self.logmsg("配置已保存", OK)
            self.run_checks()
        else:
            messagebox.showerror("失败", "写入失败，检查文件权限")


def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    App().mainloop()


if __name__ == "__main__":
    main()
