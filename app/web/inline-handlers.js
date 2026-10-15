// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
/*
 * Chạy hộ thuộc tính onclick="fn('x')" khi CSP chặn script nội tuyến (thẩm định §8: bỏ 'unsafe-inline').
 *
 * Máy chủ gửi `script-src 'self'` và chèn <meta name="vnmate-csp" content="strict"> vào trang. Khi đó trình duyệt bỏ qua
 * mọi on*="…" — tệp này bắt sự kiện ở `document`, đọc thuộc tính và chạy theo một NGỮ PHÁP NHỎ (không eval):
 *   lời gọi / chuỗi thuộc tính (`fn('a', 1, {k: [true]})`, `document.getElementById('x').classList.add('hidden')`),
 *   gán (`a.b = 'x'`), `return false`, nhiều lệnh nối bằng `;`. Tham số chỉ là hằng, `this…`, `event…`, hoặc lời gọi lồng.
 * Tên gốc phải nằm trong danh sách cho phép (sinh từ mã nguồn bởi scripts/inline_handlers.py) — HTML bị chèn vào trang
 * không gọi được fetch / apiFetch / localStorage…; vài thuộc tính nguy hiểm (constructor, defaultView, cookie, innerHTML…)
 * bị chặn trên mọi chuỗi. Không có meta (mở tệp trực tiếp, CSP cũ) → không làm gì, trình duyệt tự chạy như trước.
 */
(function () {
  'use strict';

  const ALLOWED = new Set([
  /* @allow-begin (scripts/inline_handlers.py --write) */
  'CommandCenter', 'HeaderInbox', 'addSecurityPolicyItem', 'analyzeSecuritySandbox', 'applyPersonaPreset',
  'approvePendingAction', 'askCommandCenter', 'batchToggleSkills', 'broadcastAudioAnnouncement',
  'checkPasswordStrength', 'clearEventLog', 'clearLogViewer', 'clearToolOutput', 'closeAddDepartmentModal',
  'closeAddSkillModal', 'closeCcDataSourceModal', 'closeChangePasswordModal', 'closeDeleteUserModal',
  'closeDispatchModal', 'closeDomainViewerModal', 'closeLiveMonitor', 'closeNewTemplateModal', 'closeSidebar',
  'closeSkillRunnerModal', 'closeSystemHUD', 'closeUserModal', 'confirmAddTemplate', 'console',
  'copyHudDisplayContent', 'copySkillResult', 'copyTemplateToClipboard', 'copyText', 'copyVisibleLogs',
  'copyVoiceResponseText', 'createAgentEnrollCode', 'decideVoiceApproval', 'deleteCurrentTemplate',
  'deleteDepartmentPrompt', 'detectTelegramChatId', 'document', 'downloadAgentPackage', 'downloadClientAgent',
  'downloadStudioVoiceMP3', 'downloadVoiceMP3', 'erpAdImportRun', 'exportAuditLogsJson', 'exportAuditReport',
  'exportDataSource', 'exportLogs', 'exportTasksCsv', 'exportUsersList', 'fetchLiveScreen', 'fetchUsers',
  'fillQuickLogin', 'filterAuditLogs', 'filterDevices', 'filterDomainTable', 'filterErpTree', 'filterLogDisplay',
  'filterProcessesList', 'filterSkillsByCategory', 'filterSkillsList', 'filterStudioVoices', 'filterTTSVoices',
  'filterTasksByClient', 'filterUsersList', 'goToPendingApprovals', 'gotoConnectorConfig', 'handleADToggleChange',
  'handleChangePasswordSubmit', 'handleCreateDepartment', 'handleErpBulkImport', 'handleLoginSubmit', 'handleLogout',
  'handleSendTask', 'handleTelegramToggleChange', 'handleToggleSkill', 'handleUserFormSubmit', 'hideHudDisplayCard',
  'hudConfirmSecurity', 'insertTemplateVariable', 'killClientProcess', 'loadAIModelStats', 'loadAIProxyModels',
  'loadAudioNodes', 'loadConfigHistory', 'loadConnectorConfigAll', 'loadDashboard', 'loadDeviceAccess',
  'loadDevices', 'loadEdgeTTSVoices', 'loadErpStructure', 'loadKpiLogs', 'loadPluginRegistryStats',
  'loadProxyModels', 'loadSampleStudioText', 'loadSandboxPreset', 'loadSecurityCenter', 'loadSkills',
  'loadSupervisorOverview', 'loadTaskBoard', 'loadVbeeVoices', 'loadVoiceHistory', 'loadVoiceStudioVoices',
  'onAIASREngineChange', 'onAITTSEngineChange', 'onAiModelPicked', 'onCfgModelPicked', 'onConfigModelChanged',
  'onEnterAddPolicy', 'onEnterCommandCenterAsk', 'onSelectReportTemplate', 'onTriBrainSelectChange',
  'openAddDataSourceModal', 'openAddDepartmentModal', 'openAddDeptModal', 'openAddSkillModal', 'openAddUserModal',
  'openChangePasswordModal', 'openDispatchModal', 'openDomainViewerModal', 'openEditUserModal', 'openLiveMonitor',
  'playCurrentVoiceSummary', 'playVoiceAudio', 'previewAITTS', 'previewStudioVoice', 'previewTemplateIfReady',
  'quickCmd', 'quickPlayTTS', 'quickRunSkill', 'refreshCurrentMonitorTab', 'refreshRecentLogs', 'refreshTasksAndErp',
  'rejectPendingAction', 'renderTaskBoard', 'resetAIPersona', 'resetDefaultTemplates', 'resetSkillRunnerArgs',
  'resetUserMfa', 'restoreConfigVersion', 'revokeDeviceApproval', 'robotAction', 'runAIPreview',
  'runCommandCenterAsk', 'runCommandCenterAudit', 'runConnectorHealth', 'runConnectorHealthAll',
  'runDataSourceAction', 'runDataSourceHealth', 'runIntegrationTool', 'runOpsDomainSync', 'runOpsSentinelScan',
  'saveAIConfig', 'saveAutonomyControls', 'saveCcDataSource', 'saveConnectorConfig', 'saveFullConfig',
  'saveReportTemplates', 'saveTelegramConfig', 'selectAllTaskTargets', 'selectCfgDirectProvider',
  'selectConfigQuickModel', 'selectDirectProvider', 'selectSecurityPolicyTab', 'sendVoiceCommand', 'setDeviceRole',
  'setLogFilter', 'setRoleFilter', 'setSupAttnFilter', 'showAISubTab', 'showConfigDiff', 'showNewTemplateModal',
  'showRangeValue', 'showToast', 'showVoiceSubTab', 'sortProcessesBy', 'stopActiveVoiceStream', 'stopVoiceAudio',
  'submitAddSkill', 'submitDeleteUser', 'submitDepartmentForm', 'submitDispatchCommand', 'submitEmergencyApproval',
  'submitExecuteSkill', 'submitHudCmd', 'submitRobotPairingCode', 'switchAdSubTab', 'switchCcSubTab',
  'switchCfgRoutingMode', 'switchDeptSubTab', 'switchLLMEngineMode', 'switchLogView', 'switchMonitorSubTab',
  'switchTab', 'syncDeptDataNow', 'syncDomainData', 'taskAction', 'testAILLMConnection', 'testAlertChannel',
  'testLLMConnection', 'testTelegramAlert', 'testVbeeVoice', 'toggleAIDirectKeyVisibility', 'toggleAIKeyVisibility',
  'toggleAISwitch', 'toggleAutoExecConfig', 'toggleBrowserSpeechRecognition', 'toggleCcDsAuthFields',
  'toggleCfgDirectKeyVisibility', 'toggleDeptAccordion', 'toggleDevicesGuide', 'toggleErpAdImport',
  'toggleFullscreen', 'toggleHudAudio', 'toggleHudMic', 'toggleHudThinking', 'toggleHudWake', 'toggleKeyVisibility',
  'toggleKillSwitch', 'toggleLocalWorkerNode', 'toggleLogAutoScroll', 'toggleLogPause', 'toggleMicHardware',
  'toggleMonitorAutoRefresh', 'toggleOpTask', 'togglePasswordVisibility', 'togglePinHudDisplay', 'toggleExpandHudDisplay', 'toggleSidebar',
  'toggleSupAttn', 'toggleTemplateView', 'toggleTheme', 'triggerEnterpriseCrossReport', 'triggerVisualOverlay',
  'updatePromptStats', 'updateSandboxPlaceholder', 'updateTaskTargetCount', 'updateVoiceTelemetry',
  'verifyAuditChain',
  /* @allow-end */
  ]);
  const DENY_PROPS = new Set(['constructor', '__proto__', 'prototype', 'defaultView', 'ownerDocument', 'parentWindow',
    'location', 'cookie', 'domain', 'write', 'writeln', 'open', 'eval', 'Function', 'innerHTML', 'outerHTML',
    'insertAdjacentHTML', 'srcdoc', 'setAttribute', 'setAttributeNS', 'createElement', 'execCommand', 'apply', 'call',
    'bind', 'fetch', 'XMLHttpRequest', 'sendBeacon', 'importNode', 'adoptNode', 'createRange', 'parseFromString']);

  // ── Tách từ ────────────────────────────────────────────────────────────────
  function tokenize(src) {
    const t = [];
    let i = 0;
    const n = src.length;
    while (i < n) {
      const c = src[i];
      if (/\s/.test(c)) { i += 1; continue; }
      if (c === '\'' || c === '"' || c === '`') {
        let j = i + 1, s = '';
        while (j < n && src[j] !== c) {
          if (src[j] === '\\' && j + 1 < n) {
            const e = src[j + 1];
            if (e === 'n') s += '\n';
            else if (e === 't') s += '\t';
            else if (e === 'u' && /^[0-9a-fA-F]{4}$/.test(src.slice(j + 2, j + 6))) { s += String.fromCharCode(parseInt(src.slice(j + 2, j + 6), 16)); j += 4; }
            else s += e;
            j += 2;
          } else {
            if (c === '`' && src[j] === '$' && src[j + 1] === '{') throw new SyntaxError('template ${} không hỗ trợ');
            s += src[j]; j += 1;
          }
        }
        if (j >= n) throw new SyntaxError('chuỗi chưa đóng');
        t.push({ k: 'str', v: s });
        i = j + 1;
        continue;
      }
      const num = /^\d+(?:\.\d+)?/.exec(src.slice(i));
      if (num) { t.push({ k: 'num', v: Number(num[0]) }); i += num[0].length; continue; }
      const id = /^[A-Za-z_$][\w$]*/.exec(src.slice(i));
      if (id) { t.push({ k: 'id', v: id[0] }); i += id[0].length; continue; }
      if ('()[]{},.;:=-!'.includes(c)) {
        if (c === '=' && src[i + 1] === '=') throw new SyntaxError('so sánh không hỗ trợ');
        t.push({ k: 'p', v: c }); i += 1; continue;
      }
      throw new SyntaxError(`ký tự không hỗ trợ: ${c}`);
    }
    return t;
  }

  // ── Phân tích → cây ────────────────────────────────────────────────────────
  function parse(src) {
    const t = tokenize(src);
    let p = 0;
    const peek = (v) => t[p] && t[p].k === 'p' && t[p].v === v;
    const eat = (v) => { if (!peek(v)) throw new SyntaxError(`thiếu "${v}"`); p += 1; };

    function primary() {
      const tok = t[p];
      if (!tok) throw new SyntaxError('hết chuỗi');
      if (tok.k === 'str' || tok.k === 'num') { p += 1; return { t: 'lit', v: tok.v }; }
      if (tok.k === 'id') {
        p += 1;
        if (tok.v === 'true') return { t: 'lit', v: true };
        if (tok.v === 'false') return { t: 'lit', v: false };
        if (tok.v === 'null') return { t: 'lit', v: null };
        if (tok.v === 'undefined') return { t: 'lit', v: undefined };
        return { t: 'id', v: tok.v };
      }
      if (peek('(')) { eat('('); const e = expr(); eat(')'); return e; }
      if (peek('[')) {
        eat('['); const items = [];
        while (!peek(']')) { items.push(expr()); if (!peek(']')) eat(','); }
        eat(']'); return { t: 'arr', items };
      }
      if (peek('{')) {
        eat('{'); const props = [];
        while (!peek('}')) {
          const k = t[p];
          if (!k || (k.k !== 'id' && k.k !== 'str')) throw new SyntaxError('khoá object');
          p += 1; eat(':'); props.push([k.v, expr()]);
          if (!peek('}')) eat(',');
        }
        eat('}'); return { t: 'obj', props };
      }
      throw new SyntaxError('biểu thức không hỗ trợ');
    }
    function postfix() {
      let e = primary();
      for (;;) {
        if (peek('.')) {
          eat('.'); const id = t[p];
          if (!id || id.k !== 'id') throw new SyntaxError('thiếu tên thuộc tính');
          p += 1; e = { t: 'mem', obj: e, prop: id.v };
        } else if (peek('(')) {
          eat('('); const args = [];
          while (!peek(')')) { args.push(expr()); if (!peek(')')) eat(','); }
          eat(')'); e = { t: 'call', callee: e, args };
        } else if (peek('[')) {
          eat('['); const k = expr(); eat(']'); e = { t: 'idx', obj: e, key: k };
        } else return e;
      }
    }
    function expr() {
      if (peek('-')) { eat('-'); const e = expr(); return { t: 'neg', e }; }
      if (peek('!')) { eat('!'); const e = expr(); return { t: 'not', e }; }
      return postfix();
    }
    const stmts = [];
    while (p < t.length) {
      if (peek(';')) { p += 1; continue; }
      if (t[p].k === 'id' && t[p].v === 'return') { p += 1; stmts.push({ t: 'ret', e: peek(';') || p >= t.length ? null : expr() }); }
      else {
        const left = expr();
        if (peek('=')) {
          if (left.t !== 'mem' && left.t !== 'idx') throw new SyntaxError('chỉ gán vào thuộc tính');
          eat('='); stmts.push({ t: 'set', left, right: expr() });
        } else stmts.push({ t: 'expr', e: left });
      }
      if (p < t.length) eat(';');
    }
    return stmts;
  }

  // ── Chạy ───────────────────────────────────────────────────────────────────
  function prop(name) {
    const s = String(name);
    if (DENY_PROPS.has(s)) throw new Error(`thuộc tính bị chặn: ${s}`);
    return s;
  }
  function root(name, ctx) {
    if (name === 'this') return ctx.self;
    if (name === 'event') return ctx.event;
    if (!ALLOWED.has(name)) throw new Error(`tên không nằm trong danh sách cho phép: ${name}`);
    const v = window[name];
    if (v === undefined) throw new ReferenceError(`${name} is not defined`);
    return v;
  }
  function ev(node, ctx) {
    switch (node.t) {
      case 'lit': return node.v;
      case 'id': return root(node.v, ctx);
      case 'neg': return -ev(node.e, ctx);
      case 'not': return !ev(node.e, ctx);
      case 'arr': return node.items.map((x) => ev(x, ctx));
      case 'obj': { const o = {}; node.props.forEach(([k, v]) => { o[prop(k)] = ev(v, ctx); }); return o; }
      case 'mem': { const o = ev(node.obj, ctx); return o == null ? undefined : o[prop(node.prop)]; }
      case 'idx': { const o = ev(node.obj, ctx); return o == null ? undefined : o[prop(ev(node.key, ctx))]; }
      case 'call': {
        let self, fn;
        if (node.callee.t === 'mem' || node.callee.t === 'idx') {
          self = ev(node.callee.obj, ctx);
          const k = node.callee.t === 'mem' ? node.callee.prop : ev(node.callee.key, ctx);
          fn = self == null ? undefined : self[prop(k)];
        } else {
          self = undefined;
          fn = ev(node.callee, ctx);
        }
        if (typeof fn !== 'function') throw new TypeError('không phải hàm');
        if (fn === Function || fn === window.eval || fn === window.setTimeout || fn === window.setInterval) throw new Error('hàm bị chặn');
        return fn.apply(self, node.args.map((a) => ev(a, ctx)));
      }
      default: throw new Error('nút lạ');
    }
  }
  function run(stmts, self, event) {
    const ctx = { self, event };
    for (const s of stmts) {
      if (s.t === 'ret') return s.e ? ev(s.e, ctx) : undefined;
      if (s.t === 'set') {
        const tgt = s.left;
        const o = ev(tgt.obj, ctx);
        const k = tgt.t === 'mem' ? tgt.prop : ev(tgt.key, ctx);
        o[prop(k)] = ev(s.right, ctx);
      } else ev(s.e, ctx);
    }
    return undefined;
  }

  const cache = new Map();
  function compile(code) {
    if (!cache.has(code)) {
      try { cache.set(code, parse(code)); } catch (e) { cache.set(code, e); }
    }
    return cache.get(code);
  }
  //: Thuộc tính gốc được chuyển sang data-vn-on* ngay khi phần tử vào trang (xem `adopt`) — nếu còn on* thì trình duyệt
  //: ghi một dòng "vi phạm CSP" mỗi lần bấm. Đọc cả hai: phần tử vừa chèn và bị bấm ngay trong cùng tác vụ vẫn chạy được.
  const attrOf = (el, type) => (el.hasAttribute(`data-vn-on${type}`) ? `data-vn-on${type}` : (el.hasAttribute(`on${type}`) ? `on${type}` : null));

  function fire(el, type, event) {
    const name = attrOf(el, type);
    const code = name && el.getAttribute(name);
    if (code == null || !code.trim()) return;
    const prog = compile(code);
    if (prog instanceof Error) { console.error(`[inline-handlers] không chạy được on${type}="${code}": ${prog.message}`); return; }
    try {
      if (run(prog, el, event) === false) event.preventDefault();
    } catch (e) {
      console.error(`[inline-handlers] on${type}="${code}":`, e);
    }
  }

  const api = { parse, run, compile, allowed: ALLOWED, active: false };
  window.VNInlineHandlers = api;

  const meta = document.querySelector('meta[name="vnmate-csp"]');
  if (!meta || meta.getAttribute('content') !== 'strict') return;
  api.active = true;

  const BUBBLING = ['click', 'dblclick', 'change', 'input', 'submit', 'reset', 'keyup', 'keydown', 'keypress',
    'mouseover', 'mouseout', 'contextmenu'];
  const DIRECT = ['focus', 'blur', 'error', 'load', 'mouseenter', 'mouseleave'];
  BUBBLING.forEach((type) => {
    document.addEventListener(type, (event) => {
      for (let el = event.target; el && el !== document; el = el.parentNode) {
        if (el.nodeType === 1 && attrOf(el, type)) {
          fire(el, type, event);
          if (event.cancelBubble) break;
        }
      }
    });
  });
  DIRECT.forEach((type) => {
    document.addEventListener(type, (event) => {
      const el = event.target;
      if (el && el.nodeType === 1 && attrOf(el, type)) fire(el, type, event);
    }, true);
  });

  // ── on* → data-vn-on* (tránh cảnh báo CSP mỗi lần bấm) ─────────────────────
  const ALL = BUBBLING.concat(DIRECT);
  const SELECTOR = ALL.map((t) => `[on${t}]`).join(',');
  function adoptOne(el) {
    for (const t of ALL) {
      const v = el.getAttribute(`on${t}`);
      if (v !== null) { el.setAttribute(`data-vn-on${t}`, v); el.removeAttribute(`on${t}`); }
    }
  }
  function adopt(node) {
    if (!node || node.nodeType !== 1) return;
    adoptOne(node);
    node.querySelectorAll(SELECTOR).forEach(adoptOne);
  }
  api.adopt = adopt;
  new MutationObserver((records) => {
    for (const r of records) {
      if (r.type === 'attributes') adoptOne(r.target);
      else r.addedNodes.forEach(adopt);
    }
  }).observe(document.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ALL.map((t) => `on${t}`) });
  document.addEventListener('DOMContentLoaded', () => adopt(document.documentElement));
})();
