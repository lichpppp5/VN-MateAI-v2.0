#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
#
# Cài VN-MateAI trên máy chủ Linux hoặc macOS có Docker. Chạy trong thư mục gói:   ./install.sh  (hoặc: bash install.sh)
#   - nạp image dựng sẵn (images/*.tar) nếu có → không cần Internet để cài;
#   - tạo .env, sinh mật khẩu ngẫu nhiên cho admin / CSDL / Redis;
#   - hỏi gateway mô hình AI (Enter để bỏ qua, nhập sau trên Portal);
#   - khởi động và chờ hệ thống sẵn sàng.
set -euo pipefail
cd "$(dirname "$0")"

say() { printf '\033[1;36m[VN-MateAI]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[VN-MateAI] %s\033[0m\n' "$*" >&2; exit 1; }

command -v docker >/dev/null || die "Chưa có Docker. Cài: https://docs.docker.com/engine/install/"
docker compose version >/dev/null 2>&1 || die "Chưa có Docker Compose v2 (lệnh 'docker compose')."
docker info >/dev/null 2>&1 || die "Không nói chuyện được với Docker (chạy bằng sudo hoặc thêm user vào nhóm docker)."

# 0. Gói nguyên vẹn + chữ ký nhà cung cấp (SHA256SUMS.txt.sig, Ed25519). So dấu vân tay in ra với dấu vân tay nhà cung cấp
#    công bố qua kênh riêng — khoá trong gói chỉ chứng minh gói không bị sửa SO VỚI khoá đó.
if [ -f SHA256SUMS.txt.sig ] && [ -f vendor_signing.pub ]; then
  if openssl version 2>/dev/null | grep -q '^OpenSSL 3'; then
    openssl pkeyutl -verify -pubin -inkey vendor_signing.pub -rawin -in SHA256SUMS.txt -sigfile SHA256SUMS.txt.sig >/dev/null 2>&1       || die "CHỮ KÝ GÓI KHÔNG HỢP LỆ — không cài. Tải lại gói từ nhà cung cấp."
    fp="$(openssl pkey -pubin -in vendor_signing.pub -outform DER 2>/dev/null | openssl dgst -sha256 -r | cut -d' ' -f1)"
    say "Chữ ký gói hợp lệ. Dấu vân tay khoá (DER): ${fp}"
  else
    say "Bỏ qua kiểm chữ ký (cần OpenSSL 3). Kiểm tay: python scripts/verify_release.py <thư mục gói>"
  fi
  sha_cmd="sha256sum"; command -v sha256sum >/dev/null || sha_cmd="shasum -a 256"
  while read -r sum file; do
    [ -f "$file" ] || continue
    [ "$($sha_cmd "$file" | cut -d' ' -f1)" = "$sum" ] || die "Tệp $file bị hỏng / bị sửa (băm không khớp) — không cài."
  done < SHA256SUMS.txt
  say "Băm các tệp trong gói: khớp."
fi

# 1. Image dựng sẵn đúng kiến trúc máy: *-arm64-images.tar cho máy ARM (Mac Apple Silicon…), tệp còn lại cho x86_64.
#    Không chạy image amd64 trên máy ARM (giả lập chậm, model giọng nói có thể lỗi tập lệnh) — thiếu thì build ở bước 3.
case "$(uname -m)" in arm64|aarch64) arm=1 ;; *) arm=0 ;; esac
for t in images/*.tar; do
  [ -f "$t" ] || continue
  case "$t" in *-arm64-images.tar) [ "$arm" = 1 ] || continue ;; *) [ "$arm" = 0 ] || continue ;; esac
  say "Nạp image $t …"; docker load -i "$t" >/dev/null
done

# 2. .env   (sed / tr viết sao cho chạy được cả GNU (Linux) lẫn BSD (macOS))
rnd() { LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom 2>/dev/null | head -c "${1:-24}" || true; }
setv() { # setv KEY VALUE — ghi / thay trong .env
  if grep -q "^$1=" .env; then
    v="$(printf '%s' "$2" | sed 's/[\\|&]/\\&/g')"                                      # key có thể chứa & | \
    sed "s|^$1=.*|$1=$v|" .env > .env.tmp && cat .env.tmp > .env && rm -f .env.tmp   # giữ quyền 600 của .env
  else echo "$1=$2" >> .env; fi
}
getv() { grep -E "^$1=" .env | head -1 | cut -d= -f2- | sed 's/[[:space:]]*$//'; }
[ -f .env ] || { cp env.example .env; chmod 600 .env; say "Đã tạo .env"; }
# Nâng cấp: .env của bản trước vẫn ghi VNMATE_VERSION cũ -> compose sẽ chạy image CŨ. Đồng bộ theo phiên bản của gói này.
bundle_ver="$(grep -E '^VNMATE_VERSION=' env.example | head -1 | cut -d= -f2 | tr -d '[:space:]')"
if [ -n "$bundle_ver" ] && [ "$(getv VNMATE_VERSION)" != "$bundle_ver" ]; then
  say "Phiên bản: $(getv VNMATE_VERSION) -> $bundle_ver"; setv VNMATE_VERSION "$bundle_ver"
fi
for k in VNMATE_ADMIN_PASSWORD VNMATE_MANAGER_PASSWORD VNMATE_VIEWER_PASSWORD VNMATE_PG_PASSWORD VNMATE_REDIS_PASSWORD; do
  [ -n "$(getv $k)" ] || setv "$k" "$(rnd 24)"
done

if [ -z "$(getv VNMATE_LLM_BASE_URL)" ] && [ -t 0 ]; then
  say "Gateway mô hình AI (OpenAI-compatible). Enter để bỏ qua — nhập sau ở Portal → Quản Lý Trợ Lý AI."
  read -rp "  URL (vd http://10.0.0.5:20128/v1): " v; [ -z "$v" ] || setv VNMATE_LLM_BASE_URL "$v"
  if [ -n "$v" ]; then
    read -rsp "  API key: " k; echo; [ -z "$k" ] || setv VNMATE_LLM_API_KEY "$k"
    read -rp "  Model chính (vd gpt-4o-mini): " m; [ -z "$m" ] || setv VNMATE_LLM_MODEL "$m"
  fi
fi

# Địa chỉ LAN của máy chủ cho gói "Tải Agent" (trong container chỉ thấy IP nội bộ 172.x)
lan_ip() {
  local ip
  ip="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
  if [ -z "$ip" ] && [ "$(uname -s)" = Darwin ]; then                                # macOS
    ip="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)"
  fi
  printf '%s' "$ip"
}
if [ -z "$(getv VNMATE_PUBLIC_HOST)" ]; then
  ip="$(lan_ip)"
  if [ -n "$ip" ]; then setv VNMATE_PUBLIC_HOST "$ip"; say "Địa chỉ LAN của máy chủ: $ip (VNMATE_PUBLIC_HOST trong .env)"; fi
fi

# 3. Khởi động (có image thì không build; không có thì build từ app/ — cần Internet)
img="vnmateai/app:$(getv VNMATE_VERSION)"
if [ "$arm" = 1 ] && docker image inspect "$img-arm64" >/dev/null 2>&1; then docker tag "$img-arm64" "$img"; fi
img_arch="$(docker image inspect -f '{{.Architecture}}' "$img" 2>/dev/null || true)"
if [ -n "$img_arch" ] && { [ "$arm" = 0 ] || [ "$img_arch" = arm64 ]; }; then
  docker compose up -d
else
  say "Không thấy image dựng sẵn cho máy này — build từ mã nguồn (cần Internet, 15–30 phút)…"
  docker compose up -d --build
fi

# 4. Chờ sẵn sàng
port="$(getv VNMATE_HTTPS_PORT)"; port="${port:-443}"
say "Đang khởi động (lần đầu ~1–2 phút: tạo CSDL, sinh khoá, nạp model)…"
for i in $(seq 1 90); do
  if curl -fsk "https://127.0.0.1:${port}/livez" >/dev/null 2>&1; then ok=1; break; fi
  sleep 4
done
[ "${ok:-0}" = 1 ] || { docker compose logs --tail=60 app; die "Chưa thấy ứng dụng trả lời — xem nhật ký ở trên."; }

ip="$(getv VNMATE_PUBLIC_HOST)"; [ -n "$ip" ] || ip="$(lan_ip)"
say "XONG. Mở Portal: https://${ip:-<địa chỉ máy chủ>}$( [ "$port" = 443 ] || echo ":$port" )/   ·   HUD: …/hud"
say "Đăng nhập: admin / $(getv VNMATE_ADMIN_PASSWORD)    (mật khẩu nằm trong .env — đổi ngay sau lần đăng nhập đầu)"
say "Trình duyệt sẽ cảnh báo chứng chỉ tự ký: chọn 'Vẫn tiếp tục' hoặc thay chứng chỉ thật (xem README-CAI-DAT.md)."
