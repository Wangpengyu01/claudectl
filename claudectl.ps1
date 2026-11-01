<#
.SYNOPSIS
    claudectl —— Claude Desktop / Claude Code 本地管理工具（通用版）

.DESCRIPTION
    管理 Claude 的本地状态：查看、清日志、重置设备标识、备份恢复、配置、诊断。

    通用性设计：
      * 不硬编码任何用户名、盘符、包名
      * MSIX 包名运行时用 Get-AppxPackage 探测
      * userData 位置枚举全部可能（1p / 3p / MSIX 虚拟化 / 非打包）
      * Claude Code CLI 从 PATH、npm 全局、Desktop 内置三处找
      * 备份目录默认 %LOCALAPPDATA%\claudectl\backups，可用 -BackupRoot 改
      * 兼容 Windows PowerShell 5.1 与 PowerShell 7+
      * 未安装 Claude 时优雅退出，不报错

.PARAMETER Action
    menu(默认) / status / logs / ids / deep / backup / config / diag / list

.PARAMETER BackupRoot
    备份目录。默认 %LOCALAPPDATA%\claudectl\backups

.PARAMETER Yes
    跳过所有二次确认

.EXAMPLE
    .\claudectl.ps1
    .\claudectl.ps1 status
    .\claudectl.ps1 logs -Yes
    .\claudectl.ps1 backup -BackupRoot D:\mybackups
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('menu', 'status', 'logs', 'ids', 'deep', 'backup', 'config', 'diag', 'list')]
    [string]$Action = 'menu',

    [string]$BackupRoot,

    [switch]$Yes
)

$ErrorActionPreference = 'Continue'
$Script:Version = '2.0'

# ============================================================ 全局状态

$Script:Ctx = [ordered]@{
    Packages     = @()
    DesktopExe   = $null
    AppUserModel = $null
    UserDataAll  = @()
    UserData     = $null
    Cli          = $null
    BackupRoot   = $null
    ConfigFile   = $null
}

# ============================================================ 输出 helpers

function Write-Title($t) {
    Write-Host ''
    Write-Host ('─' * 62) -ForegroundColor DarkCyan
    Write-Host "  $t" -ForegroundColor Cyan
    Write-Host ('─' * 62) -ForegroundColor DarkCyan
}
function Write-Kv($k, $v, $color = 'Gray') {
    if ($null -eq $v) { $v = '(无)' }
    $label = [string]$k
    if ($label.Length -lt 24) { $label = $label + (' ' * (24 - $label.Length)) }
    Write-Host ('  ' + $label) -NoNewline -ForegroundColor DarkGray
    Write-Host ([string]$v) -ForegroundColor $color
}
function Write-Ok($m)   { Write-Host "  [OK]  $m" -ForegroundColor Green }
function Write-Warn($m) { Write-Host "  [!]   $m" -ForegroundColor Yellow }
function Write-Bad($m)  { Write-Host "  [X]   $m" -ForegroundColor Red }
function Write-Note($m) { Write-Host "        $m" -ForegroundColor DarkGray }
function Write-Section($m) { Write-Host ''; Write-Host "  $m" -ForegroundColor White }

function Format-Size($b) {
    if ($null -eq $b -or $b -eq 0) { return '0 B' }
    if ($b -ge 1GB) { return ('{0:N2} GB' -f ($b / 1GB)) }
    if ($b -ge 1MB) { return ('{0:N2} MB' -f ($b / 1MB)) }
    if ($b -ge 1KB) { return ('{0:N1} KB' -f ($b / 1KB)) }
    return "$b B"
}

function Confirm-Action($prompt) {
    if ($Yes) { return $true }
    Write-Host ''
    $a = Read-Host "  $prompt (y/N)"
    return ($a -match '^[yY]')
}

function Test-Admin {
    try {
        $id = [Security.Principal.WindowsIdentity]::GetCurrent()
        $pr = New-Object Security.Principal.WindowsPrincipal($id)
        return $pr.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    } catch { return $false }
}

# ============================================================ 探测

function Find-ClaudePackages {
    try {
        return @(Get-AppxPackage -ErrorAction SilentlyContinue |
                 Where-Object { $_.Name -like '*Claude*' -or $_.PublisherDisplayName -like '*Anthropic*' })
    } catch { return @() }
}

function Find-DesktopExe {
    foreach ($p in @($Script:Ctx.Packages)) {
        $cand = Join-Path $p.InstallLocation 'app\Claude.exe'
        if (Test-Path $cand) { return $cand }
    }
    $paths = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Claude\Claude.exe'),
        (Join-Path $env:LOCALAPPDATA 'AnthropicClaude\Claude.exe'),
        (Join-Path $env:ProgramFiles 'Claude\Claude.exe')
    )
    if (${env:ProgramFiles(x86)}) { $paths += (Join-Path ${env:ProgramFiles(x86)} 'Claude\Claude.exe') }
    foreach ($p in $paths) {
        if ($p -and (Test-Path $p)) { return $p }
    }
    $proc = Get-Process claude -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($proc -and $proc.Path) { return $proc.Path }
    return $null
}

function Find-AppUserModelId {
    foreach ($p in @($Script:Ctx.Packages)) {
        try {
            $apps = @(Get-StartApps -ErrorAction SilentlyContinue |
                      Where-Object { $_.AppID -like "$($p.PackageFamilyName)*" })
            if ($apps.Count -gt 0) { return $apps[0].AppID }
        } catch {}
    }
    try {
        $a = @(Get-StartApps -ErrorAction SilentlyContinue | Where-Object { $_.Name -like '*Claude*' })
        if ($a.Count -gt 0) { return $a[0].AppID }
    } catch {}
    return $null
}

function Find-UserDataRoots {
    $roots = New-Object System.Collections.ArrayList

    # A) MSIX 虚拟化
    foreach ($p in @($Script:Ctx.Packages)) {
        $v = Join-Path $env:LOCALAPPDATA ("Packages\{0}\LocalCache\Roaming\Claude" -f $p.PackageFamilyName)
        if (Test-Path $v) { [void]$roots.Add($v) }
    }
    $pkgDir = Join-Path $env:LOCALAPPDATA 'Packages'
    if (Test-Path $pkgDir) {
        Get-ChildItem $pkgDir -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -like '*Claude*' } | ForEach-Object {
                $v = Join-Path $_.FullName 'LocalCache\Roaming\Claude'
                if (Test-Path $v) { [void]$roots.Add($v) }
            }
    }

    # B) Claude-Data 下的实例目录
    $cd = Join-Path $env:LOCALAPPDATA 'Claude-Data'
    if (Test-Path $cd) {
        $subs = @(Get-ChildItem $cd -Directory -ErrorAction SilentlyContinue)
        if ($subs.Count -gt 0) {
            foreach ($s in $subs) { [void]$roots.Add($s.FullName) }
        } else {
            [void]$roots.Add($cd)
        }
    }

    # C) 3p
    $p3 = Join-Path $env:LOCALAPPDATA 'Claude-3p'
    if (Test-Path $p3) { [void]$roots.Add($p3) }

    # D) 非打包
    $roaming = Join-Path $env:APPDATA 'Claude'
    if (Test-Path $roaming) { [void]$roots.Add($roaming) }

    $uniq = @()
    foreach ($r in $roots) {
        if ($uniq -contains $r) { continue }
        $looks = (Test-Path (Join-Path $r 'config.json')) -or
                 (Test-Path (Join-Path $r 'ant-did')) -or
                 (Test-Path (Join-Path $r 'Local Storage')) -or
                 (Test-Path (Join-Path $r 'Preferences'))
        if ($looks) { $uniq += $r }
    }
    return @($uniq)
}

function Resolve-ActiveUserData {
    $proc = Get-CimInstance Win32_Process -Filter "Name LIKE '%laude%'" -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -match '--user-data-dir=' } | Select-Object -First 1
    if ($proc) {
        $m = [regex]::Match($proc.CommandLine, '--user-data-dir="?([^"\s]+)"?')
        if ($m.Success) {
            $path = $m.Groups[1].Value.Trim('"')
            if (Test-Path $path) { return $path }
            if ($path -like '*\AppData\Roaming\Claude*') {
                foreach ($p in @($Script:Ctx.Packages)) {
                    $v = Join-Path $env:LOCALAPPDATA ("Packages\{0}\LocalCache\Roaming\Claude" -f $p.PackageFamilyName)
                    if (Test-Path $v) { return $v }
                }
                $pkgDir = Join-Path $env:LOCALAPPDATA 'Packages'
                if (Test-Path $pkgDir) {
                    $hit = Get-ChildItem $pkgDir -Directory -ErrorAction SilentlyContinue |
                           Where-Object { $_.Name -like '*Claude*' } | ForEach-Object {
                               $v = Join-Path $_.FullName 'LocalCache\Roaming\Claude'
                               if (Test-Path $v) { $v }
                           } | Select-Object -First 1
                    if ($hit) { return $hit }
                }
            }
        }
    }
    $all = @($Script:Ctx.UserDataAll)
    if ($all.Count -gt 0) { return [string]$all[0] }
    return $null
}

function Find-ClaudeCli {
    $cmd = Get-Command claude -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source) {
        $exe = $cmd.Source
        if ($exe -like '*.cmd' -or $exe -like '*.ps1') {
            $alt = Join-Path (Split-Path $exe -Parent) 'node_modules\@anthropic-ai\claude-code\bin\claude.exe'
            if (Test-Path $alt) { return $alt }
        } else { return $exe }
    }
    $cands = @(
        (Join-Path $env:APPDATA 'npm\node_modules\@anthropic-ai\claude-code\bin\claude.exe'),
        (Join-Path $env:ProgramFiles 'nodejs\node_modules\@anthropic-ai\claude-code\bin\claude.exe')
    )
    try {
        $prefix = (& npm config get prefix 2>$null | Select-Object -First 1)
        if ($prefix) { $cands += (Join-Path $prefix 'node_modules\@anthropic-ai\claude-code\bin\claude.exe') }
    } catch {}
    foreach ($c in $cands) { if ($c -and (Test-Path $c)) { return $c } }
    foreach ($root in @($Script:Ctx.UserDataAll)) {
        $cc = Join-Path $root 'claude-code'
        if (Test-Path $cc) {
            $exe = Get-ChildItem $cc -Recurse -Filter 'claude.exe' -ErrorAction SilentlyContinue |
                   Sort-Object LastWriteTime -Descending | Select-Object -First 1
            if ($exe) { return $exe.FullName }
        }
    }
    return $null
}

function Initialize-Context {
    $Script:Ctx.Packages    = @(Find-ClaudePackages)
    $Script:Ctx.DesktopExe  = Find-DesktopExe
    $Script:Ctx.AppUserModel = Find-AppUserModelId
    $Script:Ctx.UserDataAll = @(Find-UserDataRoots)
    $Script:Ctx.UserData    = Resolve-ActiveUserData
    $Script:Ctx.Cli         = Find-ClaudeCli
    $Script:Ctx.ConfigFile  = Join-Path $env:LOCALAPPDATA 'claudectl\config.json'

    if (-not $BackupRoot) {
        if (Test-Path $Script:Ctx.ConfigFile) {
            try {
                $c = Get-Content $Script:Ctx.ConfigFile -Raw | ConvertFrom-Json
                if ($c.BackupRoot) { $BackupRoot = $c.BackupRoot }
            } catch {}
        }
        if (-not $BackupRoot) { $BackupRoot = Join-Path $env:LOCALAPPDATA 'claudectl\backups' }
    }
    $Script:Ctx.BackupRoot = $BackupRoot
    if (-not (Test-Path $BackupRoot)) {
        try { New-Item -ItemType Directory -Force $BackupRoot | Out-Null } catch {}
    }
}

function Save-ContextConfig {
    try {
        $dir = Split-Path $Script:Ctx.ConfigFile -Parent
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force $dir | Out-Null }
        [ordered]@{ BackupRoot = $Script:Ctx.BackupRoot } |
            ConvertTo-Json | Set-Content $Script:Ctx.ConfigFile -Encoding UTF8
    } catch {}
}

# ============================================================ 进程

function Get-ClaudeProcs { return @(Get-Process claude -ErrorAction SilentlyContinue) }

function Stop-Claude {
    $p = Get-ClaudeProcs
    if ($p.Count -eq 0) { Write-Note '没有运行中的 Claude 进程'; return $true }
    Write-Note "停止 $($p.Count) 个进程…"
    $p | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 3
    if ((Get-ClaudeProcs).Count -gt 0) {
        Write-Bad '仍有进程未退出，请手动关闭'
        return $false
    }
    Write-Ok "已停止 $($p.Count) 个进程"
    return $true
}

function Start-Claude {
    $started = $false
    if ($Script:Ctx.AppUserModel) {
        try { Start-Process ('shell:AppsFolder\' + $Script:Ctx.AppUserModel); $started = $true } catch {}
    }
    if (-not $started -and $Script:Ctx.DesktopExe) {
        try { Start-Process $Script:Ctx.DesktopExe; $started = $true } catch {}
    }
    if (-not $started) { Write-Warn '找不到启动入口，请手动打开 Claude'; return }
    Start-Sleep -Seconds 8
    $n = (Get-ClaudeProcs).Count
    if ($n -gt 0) { Write-Ok "已启动（$n 个进程）" } else { Write-Warn '启动后未见进程' }
}

# ============================================================ 文件操作

function Remove-Safe([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return $true }
    try {
        Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction Stop
        return $true
    } catch {
        $empty = Join-Path $env:TEMP ('_e_' + [guid]::NewGuid().ToString('N'))
        try {
            New-Item -ItemType Directory -Force $empty | Out-Null
            robocopy $empty $Path /MIR /NFL /NDL /NJH /NJS /R:1 /W:1 | Out-Null
        } catch {}
        Remove-Item $empty -Force -ErrorAction SilentlyContinue
        try {
            Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction Stop
            return $true
        } catch { return $false }
    }
}

function Get-PathSize([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return 0 }
    $it = Get-Item -LiteralPath $Path -ErrorAction SilentlyContinue
    if (-not $it) { return 0 }
    if ($it.PSIsContainer) {
        $s = (Get-ChildItem -LiteralPath $Path -Recurse -File -ErrorAction SilentlyContinue |
              Measure-Object Length -Sum).Sum
        if ($null -eq $s) { return 0 }
        return $s
    }
    return $it.Length
}

# ============================================================ 备份

function New-Backup([string[]]$Items, [string]$Tag = 'manual') {
    $ts = Get-Date -Format 'yyyyMMdd_HHmmss'
    $dir = Join-Path $Script:Ctx.BackupRoot ("{0}_{1}" -f $Tag, $ts)
    New-Item -ItemType Directory -Force $dir | Out-Null
    $i = 0
    foreach ($it in $Items) {
        if (-not (Test-Path -LiteralPath $it)) { continue }
        $i++
        $name = '{0:D2}_{1}' -f $i, (Split-Path $it -Leaf)
        try {
            Copy-Item -LiteralPath $it -Destination (Join-Path $dir $name) -Recurse -Force -ErrorAction Stop
        } catch { Write-Warn "备份跳过: $(Split-Path $it -Leaf)" }
    }
    $ids = [ordered]@{}
    if ($Script:Ctx.UserData) {
        $ad = Join-Path $Script:Ctx.UserData 'ant-did'
        if (Test-Path $ad) { $ids['ant-did'] = (Get-Content $ad -Raw).Trim() }
    }
    $cj = Join-Path $env:USERPROFILE '.claude.json'
    if (Test-Path $cj) {
        try {
            $j = Get-Content $cj -Raw | ConvertFrom-Json
            $ids['machineID'] = $j.machineID
            $ids['userID'] = $j.userID
        } catch {}
    }
    $ids['MachineGuid'] = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Cryptography' -Name MachineGuid -ErrorAction SilentlyContinue).MachineGuid
    $ids['userDataPath'] = $Script:Ctx.UserData
    $ids['createdAt'] = (Get-Date).ToString('o')
    $ids | ConvertTo-Json | Set-Content (Join-Path $dir 'identifiers.json') -Encoding UTF8
    Write-Ok "备份: $dir"
    return $dir
}

function Get-Backups {
    if (-not (Test-Path $Script:Ctx.BackupRoot)) { return @() }
    $out = @()
    Get-ChildItem $Script:Ctx.BackupRoot -Directory -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | ForEach-Object {
            $sz = (Get-ChildItem $_.FullName -Recurse -File -ErrorAction SilentlyContinue |
                   Measure-Object Length -Sum).Sum
            $out += [pscustomobject]@{
                Name = $_.Name
                Time = $_.LastWriteTime
                Size = $sz
                Path = $_.FullName
            }
        }
    return $out
}

# ============================================================ 状态读取

function Get-Identifiers {
    $r = [ordered]@{}
    $ud = $Script:Ctx.UserData
    if ($ud) {
        $ad = Join-Path $ud 'ant-did'
        if (Test-Path $ad) {
            $raw = (Get-Content $ad -Raw).Trim()
            $r['ant-did (raw)'] = $raw
            try { $r['ant-did'] = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($raw)) }
            catch { $r['ant-did'] = '(非 base64)' }
        } else { $r['ant-did'] = '(未生成)' }
    }
    $cj = Join-Path $env:USERPROFILE '.claude.json'
    if (Test-Path $cj) {
        try {
            $j = Get-Content $cj -Raw | ConvertFrom-Json
            $r['machineID'] = $j.machineID
            $r['userID'] = $j.userID
        } catch { $r['machineID'] = '(.claude.json 解析失败)' }
    } else { $r['machineID'] = '(.claude.json 不存在)' }

    if ($ud) {
        $cp = Join-Path $ud 'config.json'
        if (Test-Path $cp) {
            try {
                $cfg = Get-Content $cp -Raw | ConvertFrom-Json
                if ($cfg.lastKnownAccountUuid) { $r['lastKnownAccountUuid'] = $cfg.lastKnownAccountUuid }
                $tc = $cfg.'oauth:tokenCacheV2'
                if ($tc) { $r['Desktop 登录态'] = '已登录' } else { $r['Desktop 登录态'] = '未登录' }
            } catch {}
        }
        $dr = Join-Path $ud 'ant-device-registry.json'
        if (Test-Path $dr) {
            try {
                $reg = Get-Content $dr -Raw | ConvertFrom-Json
                $r['device-registry'] = (@($reg.PSObject.Properties.Name) -join ', ')
            } catch {}
        }
    }
    return $r
}

function Get-LogTargets {
    return [ordered]@{
        'sentry 队列'   = 'sentry\queue'
        'sentry 作用域' = 'sentry\scope_v3.json'
        'Session 存储'  = 'Session Storage'
        'Local 存储'    = 'Local Storage'
        'IndexedDB'     = 'IndexedDB'
        'HTTP 缓存'     = 'Cache'
        'JS 代码缓存'   = 'Code Cache'
        'GPU 缓存'      = 'GPUCache'
        'Dawn 缓存'     = 'DawnGraphiteCache'
        'DawnWebGPU'    = 'DawnWebGPUCache'
        'GPU 持久缓存'  = 'GPUPersistentCache'
        '着色器缓存'    = 'ShaderCache'
        'Gr 着色器'     = 'GrShaderCache'
        'blob 存储'     = 'blob_storage'
        'Web 存储'      = 'WebStorage'
        'Crashpad'      = 'Crashpad'
        'Cookies'       = 'Network\Cookies'
        'Network 状态'  = 'Network\Network Persistent State'
    }
}

# ============================================================ 功能：状态

function Invoke-Status {
    Write-Title "Claude 环境状态  (claudectl v$Script:Version)"

    $procs = Get-ClaudeProcs
    if ($procs.Count -gt 0) {
        Write-Kv 'Desktop 进程' "$($procs.Count) 个运行中" 'Green'
    } else {
        Write-Kv 'Desktop 进程' '未运行' 'DarkGray'
    }
    Write-Kv 'Desktop 程序' $Script:Ctx.DesktopExe
    Write-Kv '启动入口' $Script:Ctx.AppUserModel
    if (@($Script:Ctx.Packages).Count -gt 0) {
        Write-Kv 'Appx 包' ((@($Script:Ctx.Packages | ForEach-Object { $_.Name })) -join ', ')
    }

    if (@($Script:Ctx.UserDataAll).Count -gt 1) {
        Write-Section '发现多个 userData：'
        foreach ($u in @($Script:Ctx.UserDataAll)) {
            if ($u -eq $Script:Ctx.UserData) { Write-Note "$u  ← 当前" } else { Write-Note $u }
        }
    }

    if (-not $Script:Ctx.UserData) {
        Write-Bad '未找到 Claude userData 目录（Claude 可能未安装）'
        return
    }

    Write-Section '设备标识'
    $ids = Get-Identifiers
    foreach ($k in $ids.Keys) {
        $col = 'Gray'
        if ($k -eq 'Desktop 登录态') {
            if ($ids[$k] -eq '已登录') { $col = 'Green' } else { $col = 'Yellow' }
        }
        Write-Kv $k $ids[$k] $col
    }

    Write-Section '日志与存储'
    $map = Get-LogTargets
    $total = 0
    $any = $false
    foreach ($k in $map.Keys) {
        $p = Join-Path $Script:Ctx.UserData $map[$k]
        if (-not (Test-Path -LiteralPath $p)) { continue }
        $any = $true
        $sz = Get-PathSize $p
        $total += $sz
        Write-Kv $k (Format-Size $sz)
    }
    if ($any) { Write-Kv '合计' (Format-Size $total) 'Cyan' } else { Write-Note '（无可统计项）' }

    Write-Section 'Claude Code 配置'
    $s = Join-Path (Join-Path $env:USERPROFILE '.claude') 'settings.json'
    if (Test-Path $s) {
        try {
            $j = Get-Content $s -Raw | ConvertFrom-Json
            $k1 = $j.env.ANTHROPIC_API_KEY
            if ($k1) {
                Write-Kv 'API key' ($k1.Substring(0, [Math]::Min(18, $k1.Length)) + '…') 'Green'
            } else {
                Write-Kv 'API key' '(未配置)' 'Yellow'
            }
            Write-Kv '模型' $j.env.ANTHROPIC_MODEL
            if ($j.env.ANTHROPIC_BASE_URL) {
                Write-Kv 'BASE_URL' $j.env.ANTHROPIC_BASE_URL
            } else {
                Write-Kv 'BASE_URL' '未设（官方）'
            }
            if ($j.timeZone) { Write-Kv 'timeZone' $j.timeZone } else { Write-Kv 'timeZone' '(跟随系统)' }
            if ($j.env.ANTHROPIC_AUTH_TOKEN) { Write-Warn '存在 ANTHROPIC_AUTH_TOKEN（可能覆盖 API key）' }
        } catch { Write-Bad 'settings.json 解析失败' }
    } else { Write-Warn 'settings.json 不存在' }

    Write-Kv 'CLI 路径' $Script:Ctx.Cli
    if ($Script:Ctx.Cli) {
        try {
            $v = (& $Script:Ctx.Cli --version 2>&1 | Select-Object -First 1)
            Write-Kv 'CLI 版本' $v
        } catch { Write-Kv 'CLI 版本' '(读取失败)' }
    }

    Write-Section '网络与系统'
    $px = Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' -ErrorAction SilentlyContinue
    if ($px.ProxyEnable -eq 1) {
        Write-Kv '系统代理' "开  $($px.ProxyServer)" 'Green'
    } else {
        Write-Kv '系统代理' '关' 'Yellow'
    }
    Write-Kv '系统时区' (Get-TimeZone).Id
    if (Test-Admin) { Write-Kv '管理员权限' '是' 'Green' } else { Write-Kv '管理员权限' '否' }
    Write-Kv '备份目录' $Script:Ctx.BackupRoot
}

# ============================================================ 功能：清日志

function Invoke-ClearLogs {
    Write-Title '清理日志与存储（保留登录态与设备标识）'

    if (-not $Script:Ctx.UserData) { Write-Bad '未找到 userData'; return }
    Write-Kv 'userData' $Script:Ctx.UserData

    $map = Get-LogTargets
    Write-Section '将清理：'
    $total = 0
    $list = @()
    foreach ($k in $map.Keys) {
        $p = Join-Path $Script:Ctx.UserData $map[$k]
        if (Test-Path -LiteralPath $p) {
            $sz = Get-PathSize $p
            $total += $sz
            $list += [pscustomobject]@{ Name = $k; Path = $p }
            Write-Note ("{0,-14} {1}" -f $k, (Format-Size $sz))
        }
    }
    if ($list.Count -eq 0) { Write-Note '没有可清理的项'; return }
    Write-Note ("合计约 {0}" -f (Format-Size $total))
    Write-Section '保留：ant-did / config.json 登录态 / settings.json'

    if (-not (Confirm-Action '确认清理？')) { Write-Note '已取消'; return }
    if (-not (Stop-Claude)) { return }

    $bakItems = @($list | ForEach-Object { $_.Path })
    New-Backup -Items $bakItems -Tag 'logs' | Out-Null

    Write-Section '执行'
    $ok = 0; $bad = 0
    foreach ($it in $list) {
        if (Remove-Safe -Path $it.Path) { Write-Ok "清除 $($it.Name)"; $ok++ }
        else { Write-Bad "失败 $($it.Name)"; $bad++ }
    }
    Write-Host ''
    Write-Ok "完成：$ok 项成功，$bad 项失败"
    Write-Note '登录态未受影响'
    Start-Claude
}

# ============================================================ 功能：重置标识

function Invoke-ClearIds {
    Write-Title '重置设备标识（需重新登录）'

    if (-not $Script:Ctx.UserData) { Write-Bad '未找到 userData'; return }

    $ids = Get-Identifiers
    Write-Section '当前：'
    foreach ($k in @('ant-did', 'machineID', 'userID', 'lastKnownAccountUuid')) {
        if ($ids.Contains($k)) { Write-Note ("{0,-22} {1}" -f $k, $ids[$k]) }
    }

    Write-Section '将删除：'
    Write-Note 'ant-did / ant-device-registry.json / ccd-ids.json / remote-control-state.json'
    Write-Note '.claude.json（machineID、userID 由 CLI 重建）'
    Write-Note 'config.json 中的账号字段'
    Write-Warn '登录态会失效'

    if (-not (Confirm-Action '确认重置？')) { Write-Note '已取消'; return }
    if (-not (Stop-Claude)) { return }

    $items = @()
    foreach ($f in @('ant-did', 'ant-device-registry.json', 'ccd-ids.json', 'remote-control-state.json', 'config.json')) {
        $p = Join-Path $Script:Ctx.UserData $f
        if (Test-Path -LiteralPath $p) { $items += $p }
    }
    $cj = Join-Path $env:USERPROFILE '.claude.json'
    if (Test-Path $cj) { $items += $cj }
    New-Backup -Items $items -Tag 'ids' | Out-Null

    Write-Section '执行'
    foreach ($f in @('ant-did', 'ant-device-registry.json', 'ccd-ids.json', 'remote-control-state.json')) {
        $p = Join-Path $Script:Ctx.UserData $f
        if (Test-Path -LiteralPath $p) {
            if (Remove-Safe -Path $p) { Write-Ok "删除 $f" }
        }
    }

    $cp = Join-Path $Script:Ctx.UserData 'config.json'
    if (Test-Path $cp) {
        try {
            $cfg = Get-Content $cp -Raw | ConvertFrom-Json
            $drop = @($cfg.PSObject.Properties.Name | Where-Object {
                $_ -match 'lastKnownAccountUuid|oauth:tokenCache|bootSideSessions|sshTransportFlagLastAnswer'
            })
            foreach ($d in $drop) { $cfg.PSObject.Properties.Remove($d) }
            $cfg | ConvertTo-Json -Depth 20 | Set-Content $cp -Encoding UTF8
            Write-Ok "清理 config.json 的 $($drop.Count) 个账号字段"
        } catch { Write-Bad 'config.json 处理失败' }
    }

    if (Test-Path $cj) {
        if (Remove-Safe -Path $cj) { Write-Ok '删除 .claude.json' }
    }
    $cjb = "$cj.backup"
    if (Test-Path $cjb) { Remove-Safe -Path $cjb | Out-Null }

    Write-Host ''
    Write-Ok '设备标识已重置'
    Write-Note '下次启动会生成新的 ant-did / machineID / userID'
    Start-Claude
}

# ============================================================ 功能：深度

function Invoke-Deep {
    Write-Title '深度清理（日志 + 设备标识）'
    Write-Warn '等同「全新设备」，登录态会丢'
    if (-not (Confirm-Action '确认继续？')) { Write-Note '已取消'; return }
    $old = $Yes
    $script:Yes = $true
    try {
        Invoke-ClearLogs
        Write-Host ''
        Invoke-ClearIds
    } finally {
        $script:Yes = $old
    }
}

# ============================================================ 功能：备份

function Invoke-BackupMenu {
    Write-Title '备份管理'
    Write-Kv '备份目录' $Script:Ctx.BackupRoot
    $list = @(Get-Backups)
    if ($list.Count -eq 0) {
        Write-Note '没有备份'
        Write-Note '提示：清日志/重置标识时会自动创建备份'
        return
    }
    Write-Host ''
    $i = 0
    foreach ($b in $list) {
        $i++
        Write-Host ('  [{0}] ' -f $i) -NoNewline -ForegroundColor Yellow
        Write-Host ('{0,-34}' -f $b.Name) -NoNewline -ForegroundColor Gray
        Write-Host ('{0,10}  {1}' -f (Format-Size $b.Size), $b.Time.ToString('MM-dd HH:mm')) -ForegroundColor DarkGray
    }
    Write-Host ''
    $a = Read-Host '  序号=详情  r=恢复  c=改备份目录  回车=返回'
    if ($a -eq 'c' -or $a -eq 'C') {
        $n = Read-Host '  新备份目录'
        if ($n) {
            $Script:Ctx.BackupRoot = $n
            if (-not (Test-Path $n)) { New-Item -ItemType Directory -Force $n | Out-Null }
            Save-ContextConfig
            Write-Ok "已设为 $n"
        }
        return
    }
    if ($a -eq 'r' -or $a -eq 'R') {
        $n = Read-Host '  恢复哪个序号？'
        if ($n -match '^\d+$' -and [int]$n -ge 1 -and [int]$n -le $list.Count) {
            Restore-Backup -Backup $list[[int]$n - 1]
        }
        return
    }
    if ($a -match '^\d+$' -and [int]$a -ge 1 -and [int]$a -le $list.Count) {
        $b = $list[[int]$a - 1]
        Write-Title "详情: $($b.Name)"
        Get-ChildItem $b.Path -Force -ErrorAction SilentlyContinue | ForEach-Object {
            $sz = '0 B'
            if ($_.PSIsContainer) { $sz = '<目录>' } else { $sz = Format-Size $_.Length }
            Write-Note ("{0,-12} {1}" -f $sz, $_.Name)
        }
        $idf = Join-Path $b.Path 'identifiers.json'
        if (Test-Path $idf) {
            Write-Section '记录的标识符：'
            Get-Content $idf -Raw
        }
    }
}

function Restore-Backup($Backup) {
    Write-Title "恢复: $($Backup.Name)"
    Write-Warn '会覆盖当前状态'
    if (-not (Confirm-Action '确认恢复？')) { Write-Note '已取消'; return }
    if (-not (Stop-Claude)) { return }

    $ud = $Script:Ctx.UserData
    if (-not $ud) { Write-Bad '找不到 userData，无法恢复'; return }

    $MAP = @{
        'Network_Cookies'                  = 'Network\Cookies'
        'Network_Cookies-journal'          = 'Network\Cookies-journal'
        'Network_Trust Tokens'             = 'Network\Trust Tokens'
        'Network_Trust Tokens-journal'     = 'Network\Trust Tokens-journal'
        'Network_TransportSecurity'        = 'Network\TransportSecurity'
        'Network_Network Persistent State' = 'Network\Network Persistent State'
        'sentry_scope_v3.json'             = 'sentry\scope_v3.json'
        'queue'                            = 'sentry\queue'
    }

    $n = 0
    Get-ChildItem $Backup.Path -File -ErrorAction SilentlyContinue | ForEach-Object {
        if ($_.Name -eq 'identifiers.json') { return }
        $real = $_.Name -replace '^\d{2}_', ''
        $dst = $MAP[$real]
        if (-not $dst) { $dst = $real }
        $target = Join-Path $ud $dst
        $dir = Split-Path $target -Parent
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force $dir | Out-Null }
        try {
            Copy-Item $_.FullName $target -Force -ErrorAction Stop
            Write-Ok "还原 $dst"
            $n++
        } catch { Write-Warn "跳过 $dst" }
    }
    Get-ChildItem $Backup.Path -Directory -ErrorAction SilentlyContinue | ForEach-Object {
        $real = $_.Name -replace '^\d{2}_', '' -replace '^dir_', ''
        if ($real -eq 'sentry_queue') { $real = 'sentry\queue' }
        $target = Join-Path $ud $real
        if (Test-Path $target) { Remove-Safe -Path $target | Out-Null }
        try {
            Copy-Item $_.FullName $target -Recurse -Force -ErrorAction Stop
            Write-Ok "还原目录 $real"
            $n++
        } catch { Write-Warn "跳过目录 $real" }
    }
    Write-Host ''
    Write-Ok "恢复完成：$n 项"
    Start-Claude
}

# ============================================================ 功能：配置

function Invoke-Config {
    Write-Title 'Claude Code 配置'
    $dir = Join-Path $env:USERPROFILE '.claude'
    $s = Join-Path $dir 'settings.json'

    if (Test-Path $s) {
        try { $cfg = Get-Content $s -Raw | ConvertFrom-Json }
        catch { Write-Bad 'settings.json 解析失败，请手动修复'; return }
    } else {
        Write-Warn 'settings.json 不存在，将创建'
        $cfg = [pscustomobject]@{ env = [pscustomobject]@{}; includeCoAuthoredBy = $false }
    }
    if (-not $cfg.env) {
        $cfg | Add-Member -NotePropertyName env -NotePropertyValue ([pscustomobject]@{}) -Force
    }

    Write-Section '当前：'
    $k = $cfg.env.ANTHROPIC_API_KEY
    if ($k) { Write-Note ("API key   : " + $k.Substring(0, [Math]::Min(18, $k.Length)) + '…') }
    else { Write-Note 'API key   : (未设)' }
    Write-Note ("模型      : " + $cfg.env.ANTHROPIC_MODEL)
    if ($cfg.env.ANTHROPIC_BASE_URL) { Write-Note ("BASE_URL  : " + $cfg.env.ANTHROPIC_BASE_URL) }
    else { Write-Note 'BASE_URL  : (官方)' }
    if ($cfg.timeZone) { Write-Note ("timeZone  : " + $cfg.timeZone) }
    else { Write-Note 'timeZone  : (跟随系统)' }
    Write-Host ''
    Write-Note '[1] API key  [2] 模型  [3] timeZone  [4] BASE_URL  [5] 备份目录  [0] 返回'
    $c = Read-Host '  选择'
    switch ($c) {
        '1' {
            $v = Read-Host '  API key'
            if ($v) { $cfg.env | Add-Member -NotePropertyName ANTHROPIC_API_KEY -NotePropertyValue $v.Trim() -Force }
        }
        '2' {
            $v = Read-Host '  模型（如 claude-opus-5-5）'
            if ($v) { $cfg.env | Add-Member -NotePropertyName ANTHROPIC_MODEL -NotePropertyValue $v.Trim() -Force }
        }
        '3' {
            $v = Read-Host '  timeZone（如 Asia/Shanghai）'
            if ($v) { $cfg | Add-Member -NotePropertyName timeZone -NotePropertyValue $v.Trim() -Force }
        }
        '4' {
            $v = Read-Host '  BASE_URL（留空=官方）'
            $cfg.env | Add-Member -NotePropertyName ANTHROPIC_BASE_URL -NotePropertyValue $v.Trim() -Force
        }
        '5' {
            $v = Read-Host '  备份目录'
            if ($v) {
                $Script:Ctx.BackupRoot = $v
                if (-not (Test-Path $v)) { New-Item -ItemType Directory -Force $v | Out-Null }
                Save-ContextConfig
                Write-Ok '已保存备份目录'
                return
            }
        }
        default { return }
    }
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force $dir | Out-Null }
    $cfg | ConvertTo-Json -Depth 10 | Set-Content $s -Encoding UTF8
    Write-Ok '已保存'
}

# ============================================================ 功能：诊断

function Invoke-Diag {
    Write-Title '诊断'

    Write-Section '[1/5] 出口 IP 与地区'
    $ip = $null
    try {
        $ip = (Invoke-RestMethod 'https://api.ipify.org?format=json' -TimeoutSec 10).ip
        Write-Kv '出口 IP' $ip
    } catch {
        try {
            $ip = (Invoke-RestMethod 'https://ifconfig.me/ip' -TimeoutSec 10)
            Write-Kv '出口 IP' $ip
        } catch { Write-Warn '出口查询失败（无网络或 DNS 故障）' }
    }
    if ($ip) {
        try {
            $g = Invoke-RestMethod ("http://ip-api.com/json/{0}?fields=country,regionName,city,isp,as,timezone" -f $ip) -TimeoutSec 10
            Write-Kv '归属' "$($g.country) / $($g.regionName) / $($g.city)"
            Write-Kv 'ISP' "$($g.isp)  ($($g.as))"
            Write-Kv '出口时区' $g.timezone
            $sysTz = (Get-TimeZone).Id
            Write-Kv '系统时区' $sysTz
            if ($g.timezone) {
                try {
                    $o1 = [TimeZoneInfo]::FindSystemTimeZoneById($sysTz).BaseUtcOffset
                    $o2 = [TimeZoneInfo]::FindSystemTimeZoneById($g.timezone).BaseUtcOffset
                    $diff = ($o2 - $o1).TotalHours
                    if ([math]::Abs($diff) -ge 1) {
                        Write-Warn ("出口与本地时差 {0} 小时（服务端按出口地区显示时间）" -f $diff)
                    }
                } catch {}
            }
        } catch { Write-Warn '归属查询失败' }
    }

    Write-Section '[2/5] DNS'
    foreach ($h in @('api.anthropic.com', 'claude.ai')) {
        try {
            $r = Resolve-DnsName $h -DnsOnly -Type A -ErrorAction Stop
            $ips = @($r | Where-Object { $_.IPAddress } | ForEach-Object { $_.IPAddress }) -join ', '
            if ($ips) { Write-Kv $h $ips 'Green' } else { Write-Kv $h '(无 A 记录)' 'Yellow' }
        } catch { Write-Kv $h '解析失败' 'Red' }
    }

    Write-Section '[3/5] API 连通性'
    if ($Script:Ctx.Cli) {
        Write-Note '发送测试请求…'
        $model = 'claude-sonnet-4-5'
        $s = Join-Path (Join-Path $env:USERPROFILE '.claude') 'settings.json'
        if (Test-Path $s) {
            try {
                $j = Get-Content $s -Raw | ConvertFrom-Json
                if ($j.env.ANTHROPIC_MODEL) { $model = $j.env.ANTHROPIC_MODEL }
            } catch {}
        }
        $out = (& $Script:Ctx.Cli -p 'reply with: ok' --model $model 2>&1 | Out-String)
        $code = $LASTEXITCODE
        if ($code -eq 0) {
            Write-Ok "API 可用（模型 $model，输出: $($out.Trim())）"
        } else {
            Write-Bad "API 失败（exit=$code）"
            foreach ($line in ($out.Trim() -split "`n" | Select-Object -First 6)) { Write-Note $line }
            $CATS = [ordered]@{
                'organization_on_hold'               = '组织被冻结 → 申诉 console.anthropic.com/appeal'
                'organization has been disabled'     = '组织被停用 → 申诉'
                'verification_required'              = '需要身份验证 → 验证通过才能签发令牌'
                'oauth_not_allowed_for_organization' = '该 org 禁用 OAuth → 改用 API key'
                'credit balance is too low'          = '余额不足'
                'rate_limit'                         = '限流 → 退避重试'
                'invalid x-api-key'                  = 'API key 无效'
                'authentication_error'               = '鉴权失败'
            }
            foreach ($k in $CATS.Keys) {
                if ($out -match [regex]::Escape($k)) { Write-Warn "归因: $($CATS[$k])" }
            }
        }
    } else { Write-Warn 'CLI 未找到，跳过' }

    Write-Section '[4/5] Desktop 登录态'
    if ($Script:Ctx.UserData) {
        $cp = Join-Path $Script:Ctx.UserData 'config.json'
        if (Test-Path $cp) {
            try {
                $cfg = Get-Content $cp -Raw | ConvertFrom-Json
                if ($cfg.'oauth:tokenCacheV2') { Write-Kv '登录态' '已登录' 'Green' }
                else { Write-Kv '登录态' '未登录' 'Yellow' }
                if ($cfg.lastKnownAccountUuid) { Write-Kv '账号' $cfg.lastKnownAccountUuid }
            } catch { Write-Warn 'config.json 解析失败' }
        } else {
            Write-Kv '登录态' '无 config.json（未登录过或已重置）' 'Yellow'
        }
    } else { Write-Warn '未找到 userData' }

    Write-Section '[5/5] 最近 HTTP 状态'
    if ($Script:Ctx.UserData) {
        $scope = Join-Path $Script:Ctx.UserData 'sentry\scope_v3.json'
        if (Test-Path $scope) {
            try {
                $txt = Get-Content $scope -Raw
                $m = [regex]::Matches($txt, '"url":"([^"]+)","method":"[A-Z]+","status_code":(\d+)')
                if ($m.Count -gt 0) {
                    foreach ($x in @($m | Select-Object -Last 8)) {
                        $code = $x.Groups[2].Value
                        $u = $x.Groups[1].Value
                        if ($u.Length -gt 58) { $u = $u.Substring(0, 58) + '…' }
                        $col = 'Yellow'
                        if ($code -eq '200') { $col = 'Green' }
                        elseif ($code -match '^4') { $col = 'Red' }
                        Write-Host ("        {0,4}  {1}" -f $code, $u) -ForegroundColor $col
                    }
                } else { Write-Note '无记录' }
            } catch { Write-Note '读取失败' }
        } else { Write-Note '无 scope_v3.json' }
    }
}

# ============================================================ 功能：环境清单

function Invoke-List {
    Write-Title "环境探测结果  (claudectl v$Script:Version)"

    Write-Section 'Claude Desktop'
    if (@($Script:Ctx.Packages).Count -gt 0) {
        $pk = @($Script:Ctx.Packages | ForEach-Object { "$($_.Name) $($_.Version)" }) -join '; '
        Write-Kv 'Appx 包' $pk
    } else {
        Write-Kv 'Appx 包' '(无，可能是非打包安装)'
    }
    Write-Kv '程序路径' $Script:Ctx.DesktopExe
    Write-Kv '启动入口' $Script:Ctx.AppUserModel
    if ((Get-ClaudeProcs).Count -gt 0) {
        Write-Kv '进程状态' "$((Get-ClaudeProcs).Count) 个"
    } else {
        Write-Kv '进程状态' '未运行'
    }

    Write-Section 'userData 目录'
    if (@($Script:Ctx.UserDataAll).Count -eq 0) {
        Write-Bad '未发现'
    } else {
        foreach ($u in @($Script:Ctx.UserDataAll)) {
            if ($u -eq $Script:Ctx.UserData) { Write-Note "$u  ← 当前使用" } else { Write-Note $u }
        }
    }

    Write-Section 'Claude Code CLI'
    Write-Kv '路径' $Script:Ctx.Cli
    if ($Script:Ctx.Cli) {
        try { Write-Kv '版本' (& $Script:Ctx.Cli --version 2>&1 | Select-Object -First 1) } catch {}
    }

    Write-Section '其他'
    Write-Kv '备份目录' $Script:Ctx.BackupRoot
    Write-Kv '配置文件' $Script:Ctx.ConfigFile
    if (Test-Admin) { Write-Kv '管理员权限' '是' } else { Write-Kv '管理员权限' '否' }
}

# ============================================================ 菜单

function Show-Menu {
    Clear-Host
    Write-Host ''
    Write-Host '  ╔════════════════════════════════════════════════════════╗' -ForegroundColor Cyan
    Write-Host ('  ║  claudectl v{0,-42}║' -f $Script:Version) -ForegroundColor Cyan
    Write-Host '  ║  Claude Desktop / Claude Code 本地管理                  ║' -ForegroundColor Cyan
    Write-Host '  ╚════════════════════════════════════════════════════════╝' -ForegroundColor Cyan
    Write-Host ''
    if ($Script:Ctx.UserData) {
        Write-Host '   userData: ' -NoNewline -ForegroundColor DarkGray
        Write-Host $Script:Ctx.UserData -ForegroundColor DarkGray
    } else {
        Write-Host '   [!] 未找到 Claude userData' -ForegroundColor Yellow
    }
    Write-Host ''
    Write-Host '   [1] 查看状态          标识、日志量、配置、网络' -ForegroundColor Gray
    Write-Host '   [2] 清理日志          清 sentry/缓存/存储，保留登录态' -ForegroundColor Gray
    Write-Host '   [3] 重置设备标识      ant-did/machineID/userID（需重登）' -ForegroundColor Gray
    Write-Host '   [4] 深度清理          日志 + 设备标识' -ForegroundColor Gray
    Write-Host '   [5] 备份管理          查看 / 恢复 / 改目录' -ForegroundColor Gray
    Write-Host '   [6] 配置              API key / 模型 / 时区' -ForegroundColor Gray
    Write-Host '   [7] 诊断              出口 / DNS / API / 登录态' -ForegroundColor Gray
    Write-Host '   [8] 环境清单          探测到的所有路径与版本' -ForegroundColor Gray
    Write-Host ''
    Write-Host '   [0] 退出' -ForegroundColor DarkGray
    Write-Host ''
}

function Invoke-Menu {
    while ($true) {
        Show-Menu
        $c = Read-Host '  选择'
        switch ($c) {
            '1' { Invoke-Status;     Read-Host '  回车继续' | Out-Null }
            '2' { Invoke-ClearLogs;  Read-Host '  回车继续' | Out-Null }
            '3' { Invoke-ClearIds;   Read-Host '  回车继续' | Out-Null }
            '4' { Invoke-Deep;       Read-Host '  回车继续' | Out-Null }
            '5' { Invoke-BackupMenu; Read-Host '  回车继续' | Out-Null }
            '6' { Invoke-Config;     Read-Host '  回车继续' | Out-Null }
            '7' { Invoke-Diag;       Read-Host '  回车继续' | Out-Null }
            '8' { Invoke-List;       Read-Host '  回车继续' | Out-Null }
            '0' { return }
        }
    }
}

# ============================================================ 入口

Initialize-Context

switch ($Action) {
    'status' { Invoke-Status }
    'logs'   { Invoke-ClearLogs }
    'ids'    { Invoke-ClearIds }
    'deep'   { Invoke-Deep }
    'backup' { Invoke-BackupMenu }
    'config' { Invoke-Config }
    'diag'   { Invoke-Diag }
    'list'   { Invoke-List }
    default  { Invoke-Menu }
}
