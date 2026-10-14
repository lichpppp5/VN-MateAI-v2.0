// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
// tests/test_voice_audio_queue.mjs
// web/voice-audio-queue.js — bộ phát MP3 dùng chung portal + HUD (realtime P5, D5).
// Chạy đúng file trong web/ với AudioContext giả — không dựng trình duyệt.
//   1. Đoạn sau giải mã NHANH hơn đoạn trước vẫn phát SAU (bản HUD cũ đảo câu).
//   2. stop() lúc đang giải mã: đoạn của lượt cũ không được phát (cả hai bản cũ đều phát).
//   3. autoEnd (HUD) / markStreamEnded (portal) gọi onPlaybackEnd đúng một lần.
//   4. keepChunks=false: không giữ MP3 đã phát (HUD không bao giờ reset).
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const src = readFileSync(join(ROOT, 'web', 'voice-audio-queue.js'), 'utf8');

let passed = 0;
let failed = 0;
function check(name, cond, detail = '') {
  if (cond) { passed += 1; console.log(`  ✓ ${name}`); } else { failed += 1; console.log(`  ✗ ${name} ${detail}`); }
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

class FakeCtx {
  constructor() { this.currentTime = 0; this.state = 'running'; this.destination = { name: 'dest' }; this.started = []; }
  async resume() {}
  // Byte đầu = số thứ tự đoạn; đoạn số nhỏ giải mã CHẬM hơn (đoạn 1: 60 ms, đoạn 3: 20 ms).
  decodeAudioData(buf) {
    const n = new Uint8Array(buf)[0];
    return new Promise((r) => setTimeout(() => r({ duration: 0.5, n }), 80 - n * 20));
  }
  createBufferSource() {
    const ctx = this;
    return {
      connect(dest) { this.dest = dest; },
      start(t) { ctx.started.push({ n: this.buffer.n, t, dest: this.dest }); },
      stop() {}, disconnect() {},
    };
  }
}
function chunk(n) { const b = new Uint8Array(64); b[0] = n; return b.buffer; }

const root = { };
new Function('window', 'globalThis', src)(root, root);
const { VoiceAudioQueue } = root;
check('module gắn VoiceAudioQueue vào window', typeof VoiceAudioQueue === 'function');

console.log('▸ Thứ tự phát theo thứ tự nhận');
{
  const ctx = new FakeCtx();
  const q = new VoiceAudioQueue({ getContext: () => ctx });
  // Ba đoạn tới gần như cùng lúc (như ba câu TTS xong liên tiếp).
  await Promise.all([q.enqueueChunk(chunk(1)), q.enqueueChunk(chunk(2)), q.enqueueChunk(chunk(3))]);
  await sleep(250);
  check('phát 1 → 2 → 3', ctx.started.map((s) => s.n).join() === '1,2,3', JSON.stringify(ctx.started));
  const near = (a, b) => Math.abs(a - b) < 1e-9;
  check('nối tiếp không khe hở', near(ctx.started[1].t, ctx.started[0].t + 0.5) && near(ctx.started[2].t, ctx.started[0].t + 1.0));
  check('đệm ban đầu nhỏ (jitter lead 50 ms)', near(ctx.started[0].t, 0.05));
}

console.log('\n▸ Ngắt lời trong lúc giải mã');
{
  const ctx = new FakeCtx();
  const q = new VoiceAudioQueue({ getContext: () => ctx, notifyOnStop: false });
  q.enqueueChunk(chunk(1));
  q.enqueueChunk(chunk(2));
  await sleep(10);
  q.stop();
  await sleep(200);
  check('không phát đoạn của lượt đã ngắt', ctx.started.length === 0, JSON.stringify(ctx.started));
  await q.enqueueChunk(chunk(3));
  await sleep(100);
  check('lượt mới vẫn phát được', ctx.started.map((s) => s.n).join() === '3');
}

console.log('\n▸ Kết thúc lượt');
{
  const ctx = new FakeCtx();
  let ends = 0;
  let starts = [];
  const q = new VoiceAudioQueue({ getContext: () => ctx, autoEnd: true, notifyOnStop: false,
    keepChunks: false, getDestination: () => 'analyser',
    onStart: (d) => starts.push(d), onPlaybackEnd: () => { ends += 1; } });
  await q.enqueueChunk(chunk(3));
  check('HUD: âm thanh đi qua AnalyserNode', ctx.started[0].dest === 'analyser');
  check('onStart kèm thời lượng', starts.length === 1 && starts[0] === 0.5);
  ctx.currentTime = 1;
  q._activeSources.forEach((s) => s.onended());
  check('autoEnd: phát hết → onPlaybackEnd một lần', ends === 1 && !q.isPlaying);
  check('keepChunks=false: không giữ MP3', q.receivedChunks.length === 0);

  const ctx2 = new FakeCtx();
  let ends2 = 0;
  const p = new VoiceAudioQueue({ getContext: () => ctx2 });
  p.onPlaybackEnd = () => { ends2 += 1; };
  await p.enqueueChunk(chunk(3));
  ctx2.currentTime = 1;
  p._activeSources.forEach((s) => s.onended());
  check('portal: chưa markStreamEnded thì chưa kết thúc', ends2 === 0);
  p.markStreamEnded();
  check('portal: markStreamEnded → kết thúc', ends2 === 1);
  check('portal: giữ MP3 để phát lại', p.receivedChunks.length === 1);
}

// 5. Jitter buffer (prompt cuối §26): cạn bộ đệm giữa lượt -> tăng đệm, có trần thấp.
{
  const ctx = new FakeCtx();
  const q = new VoiceAudioQueue({ getContext: () => ctx, notifyOnStop: false });
  await q.enqueueChunk(chunk(3));
  check('đoạn đầu không tính là cạn', q.stats().underruns === 0);
  ctx.currentTime = 2;                       // đoạn đầu phát xong từ lâu, đoạn kế mới tới
  await q.enqueueChunk(chunk(3));
  const s1 = q.stats();
  check('đoạn đến trễ = 1 lần cạn, đệm tăng 40 ms', s1.underruns === 1 && Math.abs(s1.leadSec - 0.09) < 1e-9);
  check('đoạn trễ phát sau đệm mới', Math.abs(ctx.started[1].t - 2.09) < 1e-9);
  for (let i = 0; i < 10; i += 1) { ctx.currentTime += 5; await q.enqueueChunk(chunk(3)); }
  check('đệm có trần 250 ms (không đệm vài giây)', q.stats().leadSec <= 0.25 + 1e-9);
}

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
