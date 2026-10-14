<#
  VN-MateAI Agent — cài đặt trên máy trạm Windows 10/11.

  Việc script làm:
    1. Tìm Python 3.10+ (py launcher hoặc python trong PATH).
    2. Tạo môi trường ảo .venv ngay trong thư mục Agent, cài thư viện (requirements.txt).
    3. Đăng ký Task Scheduler "VN-MateAI Agent": chạy ngầm (pythonw) mỗi khi người
       dùng hiện tại đăng nhập, tự khởi động lại nếu Agent dừng bất thường.
       Chạy trong phiên người dùng (không phải dịch vụ SYSTEM) vì Agent cần hiện
       popup nhắc việc / overlay và chụp màn hình phiên đang làm việc.
    4. Khởi động Agent ngay.

  Dùng:  chuột phải install_agent.bat -> Run   (hoặc)
         powershell -ExecutionPolicy Bypass -File install_agent.ps1 [-NoStart]
  Gỡ:    uninstall_agent.ps1
  Log:   logs\agent.log
#>
param(
    [switch]$NoStart
)

$ErrorActionPreference = 'Stop'
$AgentDir = $PSScriptRoot
$TaskName = 'VN-MateAI Agent'

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }

# 1. Python 3.10+
Write-Step 'Tìm Python 3.10+'
$python = $null
foreach ($cand in @(@('py', '-3'), @('python'))) {
    try {
        $exe = $cand[0]
        $extra = @($cand | Select-Object -Skip 1)
        $ver = & $exe @extra -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>$null
        if ($LASTEXITCODE -eq 0 -and $ver) {
            $parts = $ver.Trim().Split('.')
            if ([int]$parts[0] -eq 3 -and [int]$parts[1] -ge 10) { $python = $cand; break }
        }
    } catch { }
}
if (-not $python) {
    Write-Host 'Không tìm thấy Python 3.10 trở lên.' -ForegroundColor Red
    Write-Host 'Cài từ https://www.python.org/downloads/windows/ (tick "Add python.exe to PATH") rồi chạy lại.'
    exit 1
}
Write-Host "   dùng: $($python -join ' ') ($ver)"

# 2. Môi trường ảo + thư viện
$venv = Join-Path $AgentDir '.venv'
$venvPy = Join-Path $venv 'Scripts\python.exe'
$venvPyw = Join-Path $venv 'Scripts\pythonw.exe'
if (-not (Test-Path $venvPy)) {
    Write-Step 'Tạo môi trường ảo .venv'
    $exe = $python[0]
    $extra = @($python | Select-Object -Skip 1)
    & $exe @extra -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw 'Tạo .venv thất bại.' }
}
Write-Step 'Cài thư viện (requirements.txt)'
& $venvPy -m pip install --disable-pip-version-check -q --upgrade pip
& $venvPy -m pip install --disable-pip-version-check -q -r (Join-Path $AgentDir 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Cài thư viện thất bại — xem lỗi phía trên (mạng / proxy?).' }

# 3. Kiểm tra cấu hình tải kèm
$cfgPath = Join-Path $AgentDir 'config.json'
if (-not (Test-Path $cfgPath)) {
    Write-Host 'Thiếu config.json — hãy tải lại gói Agent từ Portal (nút "Tải Agent").' -ForegroundColor Red
    exit 1
}
$cfg = Get-Content $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json
$enrolled = Test-Path (Join-Path $AgentDir 'device.json')
if (-not $enrolled -and -not $cfg.enroll_code) {
    Write-Host 'config.json không có mã đăng ký (enroll_code) và máy chưa đăng ký — tải gói Agent MỚI từ Portal.' -ForegroundColor Red
    exit 1
}
Write-Host "   máy chủ: $($cfg.master_ip):$($cfg.master_port)"

# 4. Task Scheduler: chạy ngầm khi đăng nhập, tự khởi động lại khi dừng
Write-Step "Đăng ký tác vụ tự chạy '$TaskName'"
$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute $venvPyw -Argument "`"$(Join-Path $AgentDir 'agent.py')`"" -WorkingDirectory $AgentDir
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Description 'VN-MateAI Client Agent (máy trạm) — kết nối về máy chủ VN-MateAI.' -Force | Out-Null

if (-not $NoStart) {
    Write-Step 'Khởi động Agent'
    Start-ScheduledTask -TaskName $TaskName
    Start-Sleep -Seconds 5
    $log = Join-Path $AgentDir 'logs\agent.log'
    if (Test-Path $log) { Get-Content $log -Tail 5 -Encoding UTF8 }
}

Write-Host ''
Write-Host 'Đã cài xong. Agent tự chạy mỗi khi đăng nhập Windows.' -ForegroundColor Green
Write-Host "Log: $(Join-Path $AgentDir 'logs\agent.log')"
Write-Host 'Gỡ cài đặt: chạy uninstall_agent.ps1'
