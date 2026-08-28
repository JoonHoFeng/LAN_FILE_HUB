$ErrorActionPreference = 'Stop'

trap {
    Write-Host ''
    Write-Host ("配置出错：" + $_.Exception.Message) -ForegroundColor Red
    exit 1
}

function Read-PlainTextSecret([string]$Prompt) {
    $secure = Read-Host $Prompt -AsSecureString
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }
}

function Find-Python {
    $command = Get-Command python.exe -ErrorAction SilentlyContinue | Where-Object { $_.Source -notmatch 'WindowsApps' } | Select-Object -First 1
    if ($command) { return $command.Source }

    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($launcher) {
        $candidate = (& py.exe -3 -c "import sys; print(sys.executable)" 2>$null | Select-Object -First 1).Trim()
        if ($candidate -and (Test-Path $candidate)) { return $candidate }
    }
    throw '未找到可用的 Python 3。请先安装 Python，并勾选“Add Python to PATH”。'
}

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$server = Join-Path $root 'server.py'
if (-not (Test-Path $server)) { throw "未在当前目录找到 server.py：$root" }

Write-Host 'LAN File Hub 开机后台启动配置' -ForegroundColor Green
Write-Host "项目路径：$root"

$python = Find-Python
$pythonw = Join-Path (Split-Path $python -Parent) 'pythonw.exe'
if (-not (Test-Path $pythonw)) { $pythonw = $python }
Write-Host "Python 环境：$python"

$port = 0
$portInput = Read-Host '服务端口（直接回车使用 8080）'
if ([string]::IsNullOrWhiteSpace($portInput)) { $port = 8080 }
elseif (-not [int]::TryParse($portInput, [ref]$port) -or $port -lt 1 -or $port -gt 65535) { throw '端口必须是 1 到 65535 之间的整数。' }

$adminToken = Read-PlainTextSecret '管理员口令（输入时不显示）'
if ([string]::IsNullOrWhiteSpace($adminToken)) { throw '管理员口令不能为空。' }

$windowsPassword = Read-PlainTextSecret '当前 Windows 账户登录密码（用于开机运行任务）'
if ([string]::IsNullOrWhiteSpace($windowsPassword)) { throw 'Windows 账户登录密码不能为空。' }

$domain = if ($env:USERDOMAIN) { $env:USERDOMAIN } elseif ($env:COMPUTERNAME) { $env:COMPUTERNAME } else { '.' }
$account = "$domain\$env:USERNAME"
$dataDirectory = Join-Path $root 'data'
$launcher = Join-Path $dataDirectory 'lan-file-hub-autostart.ps1'
New-Item -ItemType Directory -Path $dataDirectory -Force | Out-Null

$safeToken = $adminToken.Replace("'", "''")
$safePython = $pythonw.Replace("'", "''")
$safeServer = $server.Replace("'", "''")
@"
`$env:LAN_FILE_HUB_ADMIN_TOKEN = '$safeToken'
& '$safePython' '$safeServer' --port $port
"@ | Set-Content -Path $launcher -Encoding UTF8 -Force

# 启动脚本中含管理员口令，只允许当前 Windows 账户读取。
$acl = Get-Acl $launcher
$acl.SetAccessRuleProtection($true, $false)
$rule = New-Object System.Security.AccessControl.FileSystemAccessRule($account, 'FullControl', 'Allow')
$acl.SetAccessRule($rule)
Set-Acl -Path $launcher -AclObject $acl

$arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$launcher`""
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId $account -LogonType Password -RunLevel Highest
$task = New-ScheduledTask -Action $action -Trigger $trigger -Principal $principal
Register-ScheduledTask -TaskName 'LAN File Hub' -InputObject $task -User $account -Password $windowsPassword -Force | Out-Null

Write-Host ''
Write-Host "配置完成：LAN File Hub 将以 $account 在开机后后台启动。" -ForegroundColor Green
Write-Host "服务地址：http://localhost:$port"
Write-Host '窗口将在 3 秒后自动关闭。'
Start-Sleep -Seconds 3
exit 0
