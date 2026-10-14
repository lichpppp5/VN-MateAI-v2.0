<#
  VN-MateAI Agent — gỡ cài đặt trên máy trạm Windows.
  Dừng Agent, xoá tác vụ tự chạy. -RemoveVenv: xoá luôn .venv (thư viện đã cài).
  Không xoá config.json / logs / skills — xoá thư mục Agent bằng tay nếu muốn.
#>
param(
    [switch]$RemoveVenv
)

$TaskName = 'VN-MateAI Agent'
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Đã xoá tác vụ '$TaskName'."
} else {
    Write-Host "Không có tác vụ '$TaskName'."
}

# Dừng tiến trình Agent còn chạy từ thư mục này
$agentPy = Join-Path $PSScriptRoot 'agent.py'
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" |
    Where-Object { $_.CommandLine -and $_.CommandLine.Contains($agentPy) } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Host "Đã dừng Agent (PID $($_.ProcessId))." }

if ($RemoveVenv) {
    $venv = Join-Path $PSScriptRoot '.venv'
    if (Test-Path $venv) { Remove-Item -Recurse -Force $venv; Write-Host 'Đã xoá .venv.' }
}
Write-Host 'Gỡ xong.'
