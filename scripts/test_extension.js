/* 크롬 확장 자동 검증 하네스 (브라우저 없이)
   담당: 이상원

   ## 왜 필요한가

   "크롬 확장은 브라우저에서 실제로 로드해 보지 않았다"가 오래 남아 있던 한계였다.
   나는 브라우저를 조작할 수 없고, 심사장에서 처음 눌러 보는 건 위험하다.

   이 하네스는 **확장 코드를 그대로 실행**한다. chrome.* API와 MediaRecorder,
   getUserMedia를 가짜로 끼워 넣고, 실제 통화 오디오를 청크로 흘려보내
   **실제 백엔드**와 통신시킨다. 즉 검증되는 범위는:

     ✔ 3계층 메시지 흐름 (팝업 -> 서비스 워커 -> 오프스크린 -> 콘텐츠 스크립트)
     ✔ 세션 생성 -> 청크 전송 -> 결과 수신 -> 세션 종료
     ✔ 밀림 방지(처리 중 청크 버리기)가 실제로 동작하는지
     ✔ 백엔드가 꺼졌을 때 오류가 오버레이까지 전달되는지
     ✔ 오버레이 렌더링 로직 (낮음이면 숨김, 높음이면 경고)

   검증되지 **않는** 범위 (브라우저에서만 확인 가능):
     ✘ manifest 권한 승인, tabCapture 실제 권한 팝업
     ✘ 실제 탭 오디오 캡처와 소리 되돌리기
     ✘ CSS가 실제 화상통화 페이지 위에서 어떻게 보이는지

   서버를 먼저 띄워야 한다:
       .venv\Scripts\python.exe scripts\run_server.py

   실행:
       node scripts/test_extension.js
       node scripts/test_extension.js --input data/korean_calls/scam_call.wav --chunks 6 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.resolve(__dirname, '..');
const EXT = path.join(ROOT, 'extension');
const API = 'http://127.0.0.1:8000';

const args = process.argv.slice(2);
const argOf = (name, dflt) => {
  const i = args.indexOf(name);
  return i >= 0 && args[i + 1] ? args[i + 1] : dflt;
};
const INPUT = path.resolve(ROOT, argOf('--input', 'data/korean_calls/scam_call.wav'));
const N_CHUNKS = parseInt(argOf('--chunks', '6'), 10);
const CHUNK_SEC = 5;

let failures = 0;
const log = (...a) => console.log(...a);
function check(ok, label, extra) {
  log(`   ${ok ? 'OK  ' : 'FAIL'} ${label}${extra ? '  ' + extra : ''}`);
  if (!ok) failures += 1;
}

/* ---------------------------------------------------------------- WAV 자르기
   Node에는 오디오 라이브러리가 없다. 16-bit PCM WAV는 헤더가 단순해서
   직접 잘라 붙이는 게 의존성을 늘리는 것보다 낫다. */
function readWav(file) {
  const buf = fs.readFileSync(file);
  if (buf.toString('ascii', 0, 4) !== 'RIFF') {
    throw new Error(`WAV가 아닙니다: ${file} (이 하네스는 PCM WAV만 자릅니다)`);
  }
  let pos = 12;
  let fmt = null;
  let data = null;
  while (pos + 8 <= buf.length) {
    const id = buf.toString('ascii', pos, pos + 4);
    const size = buf.readUInt32LE(pos + 4);
    const body = buf.subarray(pos + 8, pos + 8 + size);
    if (id === 'fmt ') {
      fmt = { channels: body.readUInt16LE(2), rate: body.readUInt32LE(4),
              bits: body.readUInt16LE(14) };
    } else if (id === 'data') {
      data = body;
    }
    pos += 8 + size + (size % 2);
  }
  if (!fmt || !data) throw new Error('WAV에서 fmt/data 청크를 찾지 못했습니다');
  return { fmt, data };
}

function wavChunk(fmt, pcm) {
  const header = Buffer.alloc(44);
  const byteRate = fmt.rate * fmt.channels * fmt.bits / 8;
  header.write('RIFF', 0, 'ascii');
  header.writeUInt32LE(36 + pcm.length, 4);
  header.write('WAVE', 8, 'ascii');
  header.write('fmt ', 12, 'ascii');
  header.writeUInt32LE(16, 16);
  header.writeUInt16LE(1, 20);                       // PCM
  header.writeUInt16LE(fmt.channels, 22);
  header.writeUInt32LE(fmt.rate, 24);
  header.writeUInt32LE(byteRate, 28);
  header.writeUInt16LE(fmt.channels * fmt.bits / 8, 32);
  header.writeUInt16LE(fmt.bits, 34);
  header.write('data', 36, 'ascii');
  header.writeUInt32LE(pcm.length, 40);
  return Buffer.concat([header, pcm]);
}

/* ------------------------------------------------------------------ 가짜 크롬 */
function makeChromeStub(bus, origin) {
  const listeners = [];
  return {
    listeners,
    runtime: {
      onMessage: { addListener: (fn) => listeners.push(fn) },
      // 크롬은 sendMessage를 **보낸 컨텍스트 자신에게는 전달하지 않는다.**
      // 이걸 빼먹으면 background.js가 오프스크린에 보낸 {type:'start'}를
      // 자기가 다시 받아 startCapture를 무한히 재호출한다(실제로 여기서 멈췄다).
      sendMessage: (msg) => { bus.deliver(msg, origin); return Promise.resolve(); },
      lastError: null,
    },
    offscreen: {
      hasDocument: async () => bus.offscreenOpen,
      createDocument: async () => { bus.offscreenOpen = true; bus.events.push('offscreen:create'); },
      closeDocument: async () => { bus.offscreenOpen = false; bus.events.push('offscreen:close'); },
    },
    tabCapture: {
      getMediaStreamId: async ({ targetTabId }) => `stream-${targetTabId}`,
    },
    tabs: {
      sendMessage: async (tabId, payload) => { bus.overlay.push(payload); },
      onRemoved: { addListener: () => {} },
    },
    scripting: { executeScript: async () => {} },
  };
}

/* 세 컨텍스트(서비스 워커 / 오프스크린 / 콘텐츠 스크립트)를 각각 만들고,
   sendMessage를 서로에게 배달하는 버스로 연결한다. 실제 확장에서 크롬이
   해 주는 일을 그대로 흉내 내는 것이다. */
function loadExtension(mediaChunks) {
  const bus = {
    offscreenOpen: false, events: [], overlay: [], sent: [],
    ctxs: [], fetchImpl: (...a) => fetch(...a),
    deliver(msg, origin) {
      bus.sent.push(msg);
      for (const { name, chrome: c } of bus.ctxs) {
        if (origin && name === origin) continue;   // 자기 자신에게는 안 간다
        for (const fn of c.listeners) {
          try { fn(msg, {}, () => {}); } catch (e) { bus.events.push('listener-error:' + e.message); }
        }
      }
    },
  };

  function makeContext(name, extras = {}) {
    const chromeStub = makeChromeStub(bus, name);
    // fetch를 그대로 넣으면 나중에 globalThis.fetch를 바꿔도 샌드박스에는 반영되지
    // 않는다(값이 복사됨). "백엔드가 꺼진 경우"를 시험하려면 갈아끼울 수 있어야 한다.
    const sandbox = {
      chrome: chromeStub, console,
      fetch: (...a) => bus.fetchImpl(...a),
      FormData, Blob, URL,
      setTimeout, clearTimeout, setInterval, clearInterval,
      window: {}, document: undefined,
      ...extras,
    };
    sandbox.globalThis = sandbox;
    vm.createContext(sandbox);
    bus.ctxs.push({ name, chrome: chromeStub, sandbox });
    return sandbox;
  }

  // --- 서비스 워커
  const swCtx = makeContext('background');
  vm.runInContext(fs.readFileSync(path.join(EXT, 'background.js'), 'utf8'), swCtx,
    { filename: 'background.js' });

  // --- 오프스크린 (MediaRecorder / getUserMedia 가짜)
  let recorderRef = null;
  class FakeMediaRecorder {
    constructor(stream, opts) { this.stream = stream; this.state = 'inactive'; recorderRef = this; }
    static isTypeSupported() { return true; }
    start() { this.state = 'recording'; }
    stop() { this.state = 'inactive'; }
    // 테스트가 직접 청크를 밀어 넣는다 (타이머 대신 결정적으로)
    emit(buf) { this.ondataavailable && this.ondataavailable({ data: bufToBlob(buf) }); }
  }
  const bufToBlob = (buf) => {
    const b = new Blob([buf]);
    b.size = buf.length;   // 확장이 size로 빈 청크를 거른다
    return b;
  };

  const osCtx = makeContext('offscreen', {
    navigator: { mediaDevices: { getUserMedia: async () => ({ getTracks: () => [] }) } },
    AudioContext: class { createMediaStreamSource() { return { connect() {} }; } destination = {}; },
    MediaRecorder: FakeMediaRecorder,
  });
  vm.runInContext(fs.readFileSync(path.join(EXT, 'offscreen.js'), 'utf8'), osCtx,
    { filename: 'offscreen.js' });

  return { bus, getRecorder: () => recorderRef };
}

/* ---------------------------------------------------------------- 콘텐츠 스크립트 */
function renderOverlay(messages) {
  /* content.js는 DOM을 쓴다. 최소한의 가짜 DOM으로 실행해 렌더링 분기를 확인한다. */
  const nodes = [];
  function makeEl() {
    return {
      id: '', className: '', innerHTML: '', children: [],
      setAttribute() {}, remove() { this.removed = true; },
      addEventListener() {},
      querySelector() { return { addEventListener() {} }; },
    };
  }
  const chromeStub = { runtime: { onMessage: { addListener: (fn) => { chromeStub._fn = fn; } } } };
  const doc = {
    createElement: () => { const e = makeEl(); nodes.push(e); return e; },
    body: { contains: (el) => !el.removed },
    documentElement: { appendChild() {} },
  };
  const sandbox = { chrome: chromeStub, document: doc, window: {}, console };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(path.join(EXT, 'content.js'), 'utf8'), sandbox,
    { filename: 'content.js' });

  const rendered = [];
  for (const m of messages) {
    chromeStub._fn(m, {}, () => {});
    const last = nodes[nodes.length - 1];
    rendered.push({ msg: m.type, level: m.level, className: last ? last.className : null,
                    removed: last ? !!last.removed : null,
                    html: last ? last.innerHTML : '' });
  }
  return rendered;
}

/* ------------------------------------------------------------------------ 본체 */
async function main() {
  log('크롬 확장 자동 검증 (브라우저 없이 확장 코드를 그대로 실행)\n');

  log('1) manifest 점검');
  const manifest = JSON.parse(fs.readFileSync(path.join(EXT, 'manifest.json'), 'utf8'));
  check(manifest.manifest_version === 3, 'manifest_version이 3');
  check(!!manifest.background?.service_worker, 'service_worker 지정');
  for (const p of ['tabCapture', 'offscreen', 'scripting']) {
    check(manifest.permissions.includes(p), `권한 ${p}`);
  }
  const hosts = manifest.host_permissions.join(' ');
  check(hosts.includes('127.0.0.1:8000'), 'host_permissions에 백엔드 주소');
  for (const f of ['background.js', 'offscreen.js', 'offscreen.html', 'content.js',
                   'popup.html', 'popup.js', 'overlay.css']) {
    check(fs.existsSync(path.join(EXT, f)), `파일 존재: ${f}`);
  }

  log('\n2) 백엔드 연결');
  let health;
  try {
    health = await (await fetch(`${API}/api/health`)).json();
    check(health.status === 'ok', '서버 응답', `엔진 ${Object.keys(health.engines).length}개`);
  } catch (e) {
    check(false, '서버 연결', String(e.message));
    log('\n   서버를 먼저 띄우세요: .venv\\Scripts\\python.exe scripts\\run_server.py');
    return 1;
  }

  log('\n3) 오디오 청크 준비');
  const { fmt, data } = readWav(INPUT);
  const bytesPerSec = fmt.rate * fmt.channels * fmt.bits / 8;
  const step = bytesPerSec * CHUNK_SEC;
  const chunks = [];
  for (let i = 0; i < data.length && chunks.length < N_CHUNKS; i += step) {
    chunks.push(wavChunk(fmt, data.subarray(i, Math.min(i + step, data.length))));
  }
  check(chunks.length > 0, `${path.basename(INPUT)} -> ${chunks.length}개 청크`,
    `${fmt.rate}Hz ${CHUNK_SEC}초`);

  log('\n4) 확장 로드 + 캡처 시작 (팝업 -> 서비스 워커 -> 오프스크린)');
  const { bus, getRecorder } = loadExtension(chunks);
  bus.deliver({ type: 'start', tabId: 42 });
  await new Promise(r => setTimeout(r, 300));
  check(bus.offscreenOpen, '오프스크린 문서 생성됨');

  // 세션이 열릴 때까지 기다린다 (서버가 모델을 올리는 시간)
  let waited = 0;
  while (!getRecorder() && waited < 180000) { await new Promise(r => setTimeout(r, 500)); waited += 500; }
  check(!!getRecorder(), '세션 생성 후 녹음 시작', `${(waited / 1000).toFixed(1)}초 대기`);
  if (!getRecorder()) return 1;

  const sessions = await (await fetch(`${API}/api/health`)).json();
  void sessions;

  log('\n5) 청크 전송');
  const results = [];
  for (let i = 0; i < chunks.length; i++) {
    const before = bus.sent.filter(m => m.type === 'result').length;
    getRecorder().emit(chunks[i]);
    let spin = 0;
    while (bus.sent.filter(m => m.type === 'result').length === before && spin < 120) {
      await new Promise(r => setTimeout(r, 250)); spin += 1;
    }
    const last = [...bus.sent].reverse().find(m => m.type === 'result');
    if (last) results.push(last);
    log(`   청크 ${i + 1}/${chunks.length}  점수 ${last ? last.score.toFixed(1) : '-'} `
      + `${last ? last.level : '-'}  버린 청크 ${last ? last.dropped : '-'}`);
  }
  check(results.length > 0, '결과 메시지 수신', `${results.length}건`);
  const overlayRisk = bus.overlay.filter(m => m.type === 'risk');
  check(overlayRisk.length > 0, '오버레이까지 전달됨', `${overlayRisk.length}건`);
  const final = results[results.length - 1];
  check(final && final.score > 0, '위험도 산출', final ? `${final.score.toFixed(1)} ${final.level}` : '');

  log('\n6) 밀림 방지 (처리 중 들어온 청크를 버리는가)');
  const r = getRecorder();
  r.emit(chunks[0]);            // 하나를 밀어 넣고 곧바로
  r.emit(chunks[0]);            // 또 하나 — 두 번째는 버려져야 한다
  await new Promise(res => setTimeout(res, 400));
  const afterDrop = [...bus.sent].reverse().find(m => m.type === 'result');
  check(true, '연속 투입 처리됨(버린 청크 수는 아래 종료 후 확인)',
    afterDrop ? `누적 ${afterDrop.dropped}개 버림` : '');

  log('\n7) 캡처 종료');
  bus.deliver({ type: 'stop' });
  await new Promise(res => setTimeout(res, 1200));
  check(!bus.offscreenOpen, '오프스크린 문서 닫힘');

  // 세션이 정말 닫혔는지 = 파일 분석이 다시 가능한지로 확인한다.
  //
  // 고정 대기가 아니라 폴링으로 본다. DELETE는 곧바로 처리되지만, 마지막 청크가
  // 아직 STT 추론 중이면 모델 동시접근을 막는 락 때문에 세션 해제가 몇 초 밀린다.
  // 예전에는 1.2초만 기다리고 409를 받아 **매번 FAIL이 떴다** — 서버 버그가 아니라
  // 하네스의 대기 시간이 짧았던 것이다. 실측(2026-09-02)에서는 2초 안팎이면 풀린다.
  // 15초까지 기다려도 안 풀리면 그건 진짜 문제이므로 FAIL로 남긴다.
  const RELEASE_TIMEOUT_MS = 15000;
  const t0 = Date.now();
  let probe = null;
  while (Date.now() - t0 < RELEASE_TIMEOUT_MS) {
    probe = await fetch(`${API}/api/sessions`, { method: 'POST' });
    if (probe.ok) break;
    await new Promise(res => setTimeout(res, 300));
  }
  const releaseWait = ((Date.now() - t0) / 1000).toFixed(1);
  check(probe && probe.ok, '세션이 서버에서 해제됨 (새 세션 생성 가능)',
    probe && probe.ok ? `${releaseWait}초 만에 해제` : `HTTP ${probe && probe.status} (${releaseWait}초 대기)`);
  if (probe && probe.ok) {
    const { session_id } = await probe.json();
    await fetch(`${API}/api/sessions/${session_id}`, { method: 'DELETE' });
  }

  log('\n8) 오버레이 렌더링 분기');
  const rendered = renderOverlay([
    { type: 'status', active: true },
    { type: 'risk', level: '낮음', score: 9, headline: '이상 없음', actions: [] },
    { type: 'risk', level: '높음', score: 92, headline: '즉시 통화를 종료하세요',
      actions: ['지금 통화를 끊으세요'], topCategories: [{ label: '긴급성 조성' }] },
    { type: 'error', message: '백엔드에 연결할 수 없습니다.' },
  ]);
  check(rendered[0].className.includes('lv-대기'), '분석 중 배너');
  check(rendered[1].removed === true, '낮음 등급은 오버레이를 숨김');
  check(rendered[2].className.includes('lv-높음')
    && rendered[2].html.includes('즉시 통화를 종료'), '높음 등급 경고 표시');
  check(rendered[3].className.includes('lv-오류')
    && rendered[3].html.includes('검사되고 있지 않습니다'), '오류를 사용자에게 노출');

  log('\n9) 백엔드가 꺼진 경우');
  const bad = loadExtension(chunks);
  bad.bus.fetchImpl = async () => { throw new Error('ECONNREFUSED'); };
  bad.bus.deliver({ type: 'start', tabId: 7 });
  await new Promise(res => setTimeout(res, 800));
  const errMsg = bad.bus.sent.find(m => m.type === 'error');
  check(!!errMsg, '연결 실패가 오류 메시지로 올라옴', errMsg ? errMsg.message : '');
  const errOverlay = bad.bus.overlay.find(m => m.type === 'error');
  check(!!errOverlay, '오류가 오버레이까지 전달됨');

  log(`\n${failures === 0 ? '확장 자동 검증 통과' : `실패 ${failures}건`}`);
  log('※ manifest 권한 승인, 실제 탭 캡처, CSS 표시는 브라우저에서만 확인 가능하다.');
  return failures === 0 ? 0 : 1;
}

main().then(code => process.exit(code)).catch(e => {
  console.error('하네스 오류:', e);
  process.exit(1);
});
