#!/usr/bin/env node
// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
/**
 * Test hồi quy cho Smart Tool Router (Command Center).
 *
 * Mục đích:
 * - Chạy ngoài trình duyệt (Node) để CI/CD bắt lỗi định tuyến sớm.
 * - Cùng một logic với code thực trong `web/app.js` giữa 2 marker
 *   `ROUTER_START` … `ROUTER_END`.
 * - Nếu sửa bảng ROUTES / _deaccent / _kwRegex, CHẮC CHẮN phải chạy
 *   `node tests/test_command_center_router.mjs` trước khi commit.
 */

import { readFileSync } from 'fs';
import { fileURLToPath } from 'url';
import { dirname, resolve } from 'path';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const appJs = readFileSync(resolve(__dirname, '../web/app.js'), 'utf8').replace(/\r\n/g, '\n');

// Cắt phần router giữa hai marker
const m = appJs.match(/\/\* ROUTER_START[\s\S]*?ROUTER_END \*\//);
if (!m) throw new Error('Không tìm thấy marker ROUTER_START/ROUTER_END trong web/app.js');

// Chuyển sang mảng dòng, bỏ 2 dòng đầu (marker block comment) và 1 dòng cuối
let lines = m[0].split('\n');
lines = lines.slice(2, -1);   // bỏ header 2 dòng, footer 1 dòng
let routerCode = lines.join('\n');

// Bỏ tất cả comment còn lại trong code
routerCode = routerCode
  .replace(/\/\*[\s\S]*?\*\//g, '')   // /* ... */
  .replace(/\/\/.*/g, '');            // // ...

// Ghi ra file tạm rồi import — cách này tránh lỗi eval/const/module scope
import { writeFileSync, unlinkSync } from 'fs';

const tempPath = resolve(__dirname, '.router_temp.mjs');
writeFileSync(tempPath, `export { classify, _deaccent };\n${routerCode}`);

const { classify, _deaccent } = await import(`file://${tempPath}`);
unlinkSync(tempPath);

// Bộ test case: [câu hỏi, endpoint mong đợi]
// Thứ tự: cashflow > policy > people > task > finance > chart (mặc định)
const CASES = [
  // Cashflow (dòng tiền / runway / quỹ)
  ['Quỹ còn bao lâu nữa',                 'cashflow'],
  ['quy con bao lau nua',                'cashflow'],
  ['tiền hết bao giờ',                   'cashflow'],
  ['tiền còn bao nhiêu',                  'cashflow'],
  ['dòng tiền tháng này thế nào',        'cashflow'],
  ['runway còn bao lâu',                 'cashflow'],
  ['quỹ vốn của công ty còn bao nhiêu',   'cashflow'],

  // Policy (quy chế / chính sách / thai sản / bảo hiểm)
  ['Quy trình xin nghỉ phép năm',        'policy'],
  ['quy trinh xin nghi phep nam',        'policy'],
  ['thai sản nghỉ được mấy tháng',       'policy'],
  ['thai san nghi duoc may thang',        'policy'],
  ['quy chế làm việc quá giờ thế nào',    'policy'],
  ['chính sách nghỉ thai sản',           'policy'],
  ['bội thường bảo hiểm là bao nhiêu',    'policy'],
  ['nghỉ ốm cần báo trước không',        'policy'],
  ['nội quy cấm hút thuốc nói ra sao',   'policy'],
  ['phụ cấp xăng xe tính thế nào',       'policy'],
  ['quy dinh ve lam viec qua gio',       'policy'],

  // People (nhân sự / phòng ban)
  ['Số nhân viên theo phòng ban',        'chart'],
  ['nhan vien moi tuyen bao nhieu',       'chart'],
  ['headcount phòng kỹ thuật',           'chart'],

  // Task (công việc / tiến độ)
  ['tiến độ công việc tuần này',         'chart'],
  ['tiến bộ dự án ra sao',               'chart'],
  ['deadline task nào sắp tới',          'chart'],

  // Finance (chi phí / doanh thu / lãi)
  ['chi phí tháng này',                  'chart'],
  ['doanh thu quý 2',                    'chart'],
  ['doanh thu quý 4 năm ngoái',          'chart'],
  ['lợi nhuận ròng',                     'chart'],
  ['giá vàng hôm nay',                   'chart'],
  ['hóa đơn khách hàng tháng 5',         'chart'],

  // Không khớp gì → mặc định chart
  ['xyz abc 123',                        'chart'],
  ['',                                   'chart'],
];

function run() {
  let pass = 0, fail = 0;
  const fails = [];

  for (const [q, want] of CASES) {
    const got = classify(q).endpoint;
    const ok = got === want;
    if (ok) {
      pass++;
    } else {
      fail++;
      fails.push({ q: q || '(rỗng)', want, got });
    }
  }

  console.log(`\n=== Smart Tool Router Test ===`);
  console.log(`Tổng: ${CASES.length} | Pass: ${pass} | Fail: ${fail}`);

  if (fails.length) {
    console.log('\n❌ THẤT BẠI:');
    for (const f of fails) {
      console.log(`  "${f.q}" → got "${f.got}", want "${f.want}"`);
    }
    process.exitCode = 1;
  } else {
    console.log('\n✅ TẤT CẢ PASS');
  }

  // Test nhanh _deaccent
  console.log('\n=== _deaccent smoke test ===');
  const d = _deaccent('Quy trình thai sản nghỉ phép');
  console.log('Input:', 'Quy trình thai sản nghỉ phép');
  console.log('Output:', d);
  // _deaccent chỉ bỏ dấu, KHÔNG lowerCase (lowerCase do classify làm)
  if (d !== 'Quy trinh thai san nghi phep') {
    console.log('❌ _deaccent sai');
    process.exitCode = 1;
  } else {
    console.log('✅ _deaccent OK');
  }
}

run();