# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
#
# Cài VN-MateAI trên Windows có Docker Desktop. Chạy trong thư mục gói (PowerShell):
#   powershell -ExecutionPolicy Bypass -File .\install.ps1
# Giống install.sh: nạp image dựng sẵn, tạo .env + mật khẩu ngẫu nhiên, hỏi gateway AI, khởi động, chờ sẵn sàng.
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Say($m) { Write-Host "[VN-MateAI] $m" -ForegroundColor Cyan }
function Die($m) { Write-Host "[VN-MateAI] $m" -ForegroundColor Red; exit 1 }

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { Die "Chưa có Docker Desktop: https://www.docker.com/products/docker-desktop/" }
docker info *> $null; if ($LASTEXITCODE -ne 0) { Die "Docker Desktop chưa chạy — mở Docker Desktop rồi chạy lại." }

# 1. Image dựng sẵn
Get-ChildItem -Path images -Filter *.tar -ErrorAction SilentlyContinue | ForEach-Object {
  Say "Nạp image $($_.Name) ..."; docker load -i $_.FullName | Out-Null
}

# 2. .env
function Rnd([int]$n = 24) { -join ((48..57) + (65..90) + (97..122) | Get-Random -Count $n | ForEach-Object { [char]$_ }) }
if (-not (Test-Path .env)) { Copy-Item env.example .env; Say "Đã tạo .env" }
$envText = Get-Content .env -Encoding UTF8
function GetV($k) { ($envText | Where-Object { $_ -match "^$k=" } | Select-Object -First 1) -replace "^$k=", "" }
function SetV($k, $v) {
  if ($envText -match "^$k=") { $script:envText = $envText | ForEach-Object { if ($_ -match "^$k=") { "$k=$v" } else { $_ } } }
  else { $script:envText += "$k=$v" }
}
# Nâng cấp: .env của bản trước vẫn ghi VNMATE_VERSION cũ -> compose sẽ chạy image CŨ. Đồng bộ theo phiên bản của gói này.
$bundleVer = ((Get-Content (Join-Path $PSScriptRoot "env.example") -Encoding UTF8 | Where-Object { $_ -match "^VNMATE_VERSION=" } |
  Select-Object -First 1) -replace "^VNMATE_VERSION=", "").Trim()
if ($bundleVer -and (GetV "VNMATE_VERSION").Trim() -ne $bundleVer) { Say "Phiên bản: $((GetV "VNMATE_VERSION").Trim()) -> $bundleVer"; SetV "VNMATE_VERSION" $bundleVer }
foreach ($k in "VNMATE_ADMIN_PASSWORD","VNMATE_MANAGER_PASSWORD","VNMATE_VIEWER_PASSWORD","VNMATE_PG_PASSWORD","VNMATE_REDIS_PASSWORD") {
  if (-not (GetV $k).Trim()) { SetV $k (Rnd 24) }
}
if (-not (GetV "VNMATE_LLM_BASE_URL").Trim()) {
  Say "Gateway mô hình AI (OpenAI-compatible). Enter để bỏ qua — nhập sau ở Portal -> Quản Lý Trợ Lý AI."
  $u = Read-Host "  URL (vd http://10.0.0.5:20128/v1)"
  if ($u) {
    SetV "VNMATE_LLM_BASE_URL" $u
    $k = Read-Host "  API key"; if ($k) { SetV "VNMATE_LLM_API_KEY" $k }
    $m = Read-Host "  Model chính (vd gpt-4o-mini)"; if ($m) { SetV "VNMATE_LLM_MODEL" $m }
  }
}
if (-not (GetV "VNMATE_PUBLIC_HOST").Trim()) {   # địa chỉ LAN cho gói "Tải Agent" (container chỉ thấy IP 172.x)
  $lan = Get-NetIPConfiguration -ErrorAction SilentlyContinue | Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq "Up" } |
    ForEach-Object { $_.IPv4Address.IPAddress } | Select-Object -First 1
  if ($lan) { SetV "VNMATE_PUBLIC_HOST" $lan; Say "Địa chỉ LAN của máy chủ: $lan (VNMATE_PUBLIC_HOST trong .env)" }
}
[System.IO.File]::WriteAllLines((Join-Path $PSScriptRoot ".env"), $envText, (New-Object System.Text.UTF8Encoding($false)))

# 3. Khởi động
$ver = (GetV "VNMATE_VERSION").Trim(); if (-not $ver) { $ver = "2.0.1" }
docker image inspect "vnmateai/app:$ver" *> $null
if ($LASTEXITCODE -eq 0) { docker compose up -d } else { Say "Không thấy image dựng sẵn — build từ mã nguồn (cần Internet, 10-20 phút)..."; docker compose up -d --build }
if ($LASTEXITCODE -ne 0) { Die "docker compose lỗi — xem thông báo ở trên." }

# 4. Chờ sẵn sàng
$port = (GetV "VNMATE_HTTPS_PORT").Trim(); if (-not $port) { $port = "443" }
Say "Đang khởi động (lần đầu ~1-2 phút)..."
$ok = $false
for ($i = 0; $i -lt 90; $i++) {
  try { & curl.exe -fsk "https://127.0.0.1:$port/livez" *> $null; if ($LASTEXITCODE -eq 0) { $ok = $true; break } } catch {}
  Start-Sleep -Seconds 4
}
if (-not $ok) { docker compose logs --tail=60 app; Die "Chưa thấy ứng dụng trả lời — xem nhật ký ở trên." }
$suffix = if ($port -eq "443") { "" } else { ":$port" }
$hostShown = (GetV "VNMATE_PUBLIC_HOST").Trim(); if (-not $hostShown) { $hostShown = "localhost" }
Say "XONG. Mở Portal: https://$hostShown$suffix/   ·   HUD: https://$hostShown$suffix/hud"
Say "Đăng nhập: admin / $(GetV 'VNMATE_ADMIN_PASSWORD')   (mật khẩu nằm trong .env — đổi ngay sau lần đăng nhập đầu)"
