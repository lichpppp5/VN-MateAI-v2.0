// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
/**
 * VoiceAudioQueue — phát MP3 của câu trả lời thoại bằng Web Audio API, dùng
 * CHUNG cho portal (app.js) và HUD (hud.js).
 *
 * Realtime P5 (D5): trước đây mỗi trang một bản (StreamingAudioQueue /
 * HudAudioQueue). Bản HUD giải mã các đoạn SONG SONG nên đoạn sau giải mã xong
 * trước thì phát trước — câu bị đảo. Cả hai bản đều phát nốt đoạn đang giải mã
 * dở sau khi đã stop() (ngắt lời). Ở đây:
 *   - giải mã TUẦN TỰ theo thứ tự nhận, lập lịch nối tiếp không khe hở;
 *   - stop()/reset() tăng "thế hệ": đoạn đang giải mã dở của lượt cũ bị bỏ;
 *   - phần riêng của từng trang truyền qua options.
 *
 * options:
 *   getContext()          AudioContext dùng chung của trang (mặc định tự tạo)
 *   getDestination(ctx)   nút nhận âm thanh (HUD: AnalyserNode cho hiệu ứng)
 *   canPlay()             false -> bỏ qua đoạn (HUD tắt tiếng)
 *   onBeforeChunk(first)  trước mỗi đoạn; first = đoạn đầu của lượt
 *   onStart(durationSec)  bắt đầu phát (từ trạng thái im lặng)
 *   onChunkScheduled(durationSec, delaySec)  MỖI đoạn vừa được lập lịch: phát sau delaySec giây, dài durationSec
 *                         (HUD: hiện đúng câu đang đọc — mỗi câu một khung MP3)
 *   onFirstAudio()        đoạn đầu tiên của lượt đã được lập lịch
 *   onPlaybackEnd()       phát hết
 *   autoEnd               true: phát hết hàng đợi là kết thúc (HUD — không có
 *                         tín hiệu hết lượt); false: chờ markStreamEnded() (portal)
 *   notifyOnStop          gọi onPlaybackEnd khi stop() (mặc định true)
 *   keepChunks            giữ MP3 đã nhận cho getCombinedBlob() (mặc định true;
 *                         HUD không bao giờ reset nên tắt để RAM không tăng mãi)
 *   jitterLeadSec         đệm ban đầu trước đoạn đầu / sau khi cạn (mặc định 0.05 s)
 *   jitterStepSec         mỗi lần CẠN bộ đệm (đoạn kế đến trễ) đệm thêm (mặc định 0.04 s)
 *   jitterMaxSec          trần đệm (mặc định 0.25 s) — prompt cuối §26: thấp độ trễ, không đệm vài giây
 *
 * Jitter buffer (prompt cuối §26): đoạn tới khi hàng đợi đã phát hết = một lần cạn (underrun,
 * người nghe thấy khe lặng). Mỗi lần cạn tăng đệm một bước để lần sau đỡ ngắt quãng;
 * `stats()` trả số lần cạn + độ đệm hiện tại để đo.
 */
(function (root) {
  'use strict';

  class VoiceAudioQueue {
    constructor(options = {}) {
      this._opts = options;
      this._ctx = null;
      this._gen = 0;
      this._nextStartTime = 0;
      this._activeSources = [];
      this._pendingChunks = [];
      this._isDecoding = false;
      this._streamEnded = false;
      this._endTimer = null;

      this.isPlaying = false;
      this.hasFirstAudio = false;
      this.receivedChunks = [];
      this._chunkCount = 0;
      this._lead = options.jitterLeadSec ?? 0.05;
      this._leadStep = options.jitterStepSec ?? 0.04;
      this._leadMax = options.jitterMaxSec ?? 0.25;
      this._underruns = 0;
      // Gán sau khi tạo (portal) hoặc qua options.
      this.onFirstAudio = options.onFirstAudio || null;
      this.onPlaybackEnd = options.onPlaybackEnd || null;
    }

    _call(fn, ...args) {
      if (typeof fn === 'function') {
        try { return fn(...args); } catch (e) { console.warn('[VoiceAudioQueue] callback lỗi:', e); }
      }
      return undefined;
    }

    /** AudioContext đang dùng (tạo / đánh thức nếu cần). */
    ensureContext() {
      if (typeof this._opts.getContext === 'function') {
        this._ctx = this._opts.getContext() || null;
      } else if (!this._ctx) {
        const AC = root.AudioContext || root.webkitAudioContext;
        if (AC) {
          try { this._ctx = new AC(); } catch (e) { console.warn('[VoiceAudioQueue] AudioContext lỗi:', e); }
        }
      }
      if (this._ctx && this._ctx.state === 'suspended') {
        this._ctx.resume().catch(() => {});
      }
      return this._ctx;
    }

    async enqueueChunk(arrayBuffer) {
      if (!arrayBuffer || arrayBuffer.byteLength < 32) return;
      if (typeof this._opts.canPlay === 'function' && !this._opts.canPlay()) return;
      this._call(this._opts.onBeforeChunk, this._chunkCount === 0);
      this._chunkCount += 1;
      if (this._opts.keepChunks !== false) this.receivedChunks.push(arrayBuffer);
      this._pendingChunks.push(arrayBuffer);
      await this._processPendingQueue();
    }

    async _processPendingQueue() {
      if (this._isDecoding) return;
      this._isDecoding = true;
      const gen = this._gen;
      try {
        while (this._pendingChunks.length > 0 && gen === this._gen) {
          const chunk = this._pendingChunks.shift();
          const ctx = this.ensureContext();
          if (!ctx) {
            console.warn('[VoiceAudioQueue] Trình duyệt không hỗ trợ Web Audio API');
            break;
          }
          let audioBuf = null;
          try {
            audioBuf = await ctx.decodeAudioData(chunk.slice(0));
          } catch (err) {
            console.warn('[VoiceAudioQueue] Bỏ đoạn MP3 không giải mã được:', err);
            continue;
          }
          // Đã stop()/reset() trong lúc giải mã: đoạn của lượt cũ, không phát.
          if (gen !== this._gen) break;
          if (!audioBuf || audioBuf.duration <= 0) continue;
          this._schedule(ctx, audioBuf);
        }
      } finally {
        if (gen === this._gen) this._isDecoding = false;
      }
    }

    _schedule(ctx, audioBuf) {
      const source = ctx.createBufferSource();
      source.buffer = audioBuf;
      const dest = typeof this._opts.getDestination === 'function'
        ? (this._opts.getDestination(ctx) || ctx.destination) : ctx.destination;
      source.connect(dest);

      // Nối tiếp tuyệt đối: câu sau bắt đầu đúng lúc câu trước kết thúc. Hàng đợi đã cạn
      // (đầu lượt hoặc đoạn đến trễ) -> đệm một chút; cạn GIỮA lượt -> tăng đệm (jitter).
      const now = ctx.currentTime;
      let startAt;
      if (this._nextStartTime > now) {
        startAt = this._nextStartTime;
      } else {
        if (this.isPlaying) {
          this._underruns += 1;
          this._lead = Math.min(this._leadMax, this._lead + this._leadStep);
        }
        startAt = now + this._lead;
      }
      source.start(startAt);
      this._nextStartTime = startAt + audioBuf.duration;
      this._call(this._opts.onChunkScheduled, audioBuf.duration, Math.max(0, startAt - now));
      this._activeSources.push(source);
      source.onended = () => {
        const idx = this._activeSources.indexOf(source);
        if (idx !== -1) this._activeSources.splice(idx, 1);
        this._checkFinished();
      };

      if (!this.isPlaying) {
        this.isPlaying = true;
        this._call(this._opts.onStart, audioBuf.duration);
      }
      if (!this.hasFirstAudio) {
        this.hasFirstAudio = true;
        this._call(this.onFirstAudio);
      }
      this._scheduleEndTimer();
    }

    _scheduleEndTimer() {
      if (this._endTimer) clearTimeout(this._endTimer);
      if (!this._ctx) return;
      const remainingSec = Math.max(0, this._nextStartTime - this._ctx.currentTime);
      this._endTimer = setTimeout(() => this._checkFinished(), Math.ceil((remainingSec + 0.1) * 1000));
    }

    _checkFinished() {
      if (!this._streamEnded && !this._opts.autoEnd) return;
      if (this._pendingChunks.length > 0 || this._isDecoding) return;
      if (this._ctx && this._ctx.currentTime < this._nextStartTime - 0.05) return;
      if (this.isPlaying) {
        this.isPlaying = false;
        this._call(this.onPlaybackEnd);
      }
    }

    /** Đã nhận hết các câu của lượt (portal). */
    markStreamEnded() {
      this._streamEnded = true;
      this._scheduleEndTimer();
      this._checkFinished();
    }

    /** Dừng ngay (ngắt lời / lượt mới). */
    stop() {
      this._gen += 1;
      for (const src of this._activeSources) {
        try { src.stop(); src.disconnect(); } catch (e) { /* đã dừng */ }
      }
      this._activeSources = [];
      this._pendingChunks = [];
      this._isDecoding = false;
      this._nextStartTime = 0;
      this.isPlaying = false;
      if (this._endTimer) {
        clearTimeout(this._endTimer);
        this._endTimer = null;
      }
      if (this._opts.notifyOnStop !== false) this._call(this.onPlaybackEnd);
    }

    /** Số đo jitter buffer: số lần cạn bộ đệm và độ đệm hiện tại (giây). */
    stats() {
      return { underruns: this._underruns, leadSec: this._lead, chunks: this._chunkCount };
    }

    /** Toàn bộ MP3 đã nhận (phát lại / tải xuống). */
    getCombinedBlob() {
      if (!this.receivedChunks.length) return null;
      return new Blob(this.receivedChunks, { type: 'audio/mp3' });
    }

    reset() {
      this.stop();
      this.hasFirstAudio = false;
      this.receivedChunks = [];
      this._chunkCount = 0;
      this._streamEnded = false;
    }
  }

  root.VoiceAudioQueue = VoiceAudioQueue;
})(typeof window !== 'undefined' ? window : globalThis);
