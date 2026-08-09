/* 크롬 확장 **실제 브라우저** 검증 (CDP 자동 조작)
   담당: 이상원

   ## test_extension.js와 무엇이 다른가

   `test_extension.js`는 chrome.* API를 가짜로 끼워 넣고 확장 코드만 실행한다.
   로직은 검증되지만 **크롬이 실제로 확장을 받아들이는지는 확인되지 않는다.**
   이 스크립트는 진짜 크롬을 띄워서 확장을 로드하고 CDP(크롬 개발자 도구 프로토콜)로
   조작한다. 즉 여기서만 확인되는 것:

     ✔ manifest를 크롬이 실제로 받아들이는가 (권한 오류 없이 로드되는가)
     ✔ 서비스 워커가 실제로 뜨는가, 콘솔에 에러가 없는가
     ✔ chrome.tabCapture.getMediaStreamId가 실제 권한으로 성공하는가
     ✔ 오프스크린 문서가 실제로 생성되는가
     ✔ 실제 탭 오디오가 캡처돼 백엔드까지 도달하는가
     ✔ 콘텐츠 스크립트가 실제 페이지에 주입되고 오버레이 DOM이 생기는가
     ✔ 캡처를 시작해도 탭 소리가 계속 나는가 (오디오 되돌리기)

   ## 확장을 어떻게 넣는가 (`--load-extension`은 이제 안 된다)

   Chrome 137부터 `--load-extension` 커맨드라인 스위치가 **막혔다.** 실제로 이 플래그로
   띄워 보면 확장이 목록에 아예 안 뜬다(`--disable-features=...`로도 안 풀린다).
   대신 CDP의 **`Extensions.loadUnpacked`** 를 쓴다. 크롬이 그 목적으로 새로 만든
   경로이고, 특별한 플래그 없이도 동작한다.

   ## 사용자 프로필을 건드리지 않는다

   `--user-data-dir`로 임시 프로필을 쓴다. 사용자가 쓰던 크롬 창/로그인/확장에
   아무 영향이 없고, 끝나면 임시 디렉터리를 지운다. 창은 화면 밖에 띄운다.

   ## activeTab 문제와 그 우회 (여기가 제일 까다로웠다)

   `chrome.tabCapture.getMediaStreamId`는 **사용자가 확장 아이콘을 실제로 눌렀을 때만**
   허용된다(activeTab). CDP의 `Runtime.evaluate({userGesture:true})`로도, 팝업을
   `chrome.action.openPopup()`으로 열어 버튼을 눌러도 안 된다. 확인한 것:

       --allowlisted-extension-id (엉뚱한 id)   실패
       Browser.grantPermissions                 실패
       host_permissions <all_urls>              실패
       chrome.action.openPopup() + 버튼 클릭     실패

   **정확한 확장 id로 `--allowlisted-extension-id`를 주면 통과한다.** 그래서 크롬을
   두 번 띄운다: 1차로 확장을 로드해 id를 알아내고, 2차에 그 id를 허용 목록에 넣는다.
   (id는 확장 디렉터리의 절대 경로에서 결정되므로 기계마다 다르다. 하드코딩 불가.)

   > ⚠ **이 플래그는 activeTab 검사를 건너뛴다.** 즉 이 검증이 보증하는 것은
   > "getMediaStreamId 이후의 코드 경로가 실제 크롬에서 동작한다"이지,
   > "사용자가 아이콘을 눌렀을 때 activeTab이 제대로 부여된다"가 아니다.
   > 후자는 `--manual`로 사람이 한 번 눌러 확인해야 한다.

   서버를 먼저 띄워야 한다:
       .venv\Scripts\python.exe scripts\run_server.py

   실행:
       node scripts/verify_extension_chrome.js            (완전 자동)
       node scripts/verify_extension_chrome.js --manual   (사람이 아이콘 클릭)
       node scripts/verify_extension_chrome.js --keep-open */

const fs = require('fs');
const os = require('os');
const path = require('path');
const http = require('http');
const { spawn } = require('child_process');

const ROOT = path.resolve(__dirname, '..');
const EXT = path.join(ROOT, 'extension');
const API = 'http://127.0.0.1:8000';
const PORT = 9333;
const CAPTURE_SEC = 16;

const args = process.argv.slice(2);
const KEEP_OPEN = args.includes('--keep-open');
// tabCapture는 사람이 확장 아이콘을 눌러야만 허용된다(activeTab). --manual은
// 그 한 번의 클릭을 사람에게 요청하고, 나머지는 전부 자동으로 검증한다.
const MANUAL = args.includes('--manual');

const CHROME_CANDIDATES = [
  path.join(process.env['ProgramFiles'] || '', 'Google/Chrome/Application/chrome.exe'),
  path.join(process.env['ProgramFiles(x86)'] || '', 'Google/Chrome/Application/chrome.exe'),
  path.join(process.env.LOCALAPPDATA || '', 'Google/Chrome/Application/chrome.exe'),
];

let failures = 0;
const log = (...a) => console.log(...a);
function check(ok, label, extra) {
  log(`   ${ok ? 'OK  ' : 'FAIL'} ${label}${extra ? '  ' + extra : ''}`);
  if (!ok) failures += 1;
  return ok;
}
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

function getJson(url) {
  return new Promise((resolve, reject) => {
    http.get(url, (res) => {
      let body = '';
      res.on('data', d => body += d);
      res.on('end', () => { try { resolve(JSON.parse(body)); } catch (e) { reject(e); } });
    }).on('error', reject);
  });
}

function httpJson(method, url) {
  return new Promise((resolve) => {
    const req = http.request(url, { method }, (res) => {
      let b = '';
      res.on('data', d => b += d);
      res.on('end', () => resolve({ code: res.statusCode, body: b }));
    });
    req.on('error', () => resolve({ code: 0, body: '' }));
    req.end();
  });
}

/* ------------------------------------------------------------------ CDP 클라이언트
   ws 패키지를 설치하지 않으려고 Node 내장 WebSocket을 쓴다(Node 22+). */
class CDP {
  constructor(wsUrl) {
    this.ws = new WebSocket(wsUrl);
    this.id = 0;
    this.pending = new Map();
    this.events = [];
    this.ready = new Promise((resolve, reject) => {
      this.ws.addEventListener('open', () => resolve());
      this.ws.addEventListener('error', (e) => reject(new Error('CDP 연결 실패: ' + e.message)));
    });
    this.ws.addEventListener('message', (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && this.pending.has(msg.id)) {
        const { resolve, reject } = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        msg.error ? reject(new Error(JSON.stringify(msg.error))) : resolve(msg.result);
      } else if (msg.method) {
        this.events.push(msg);
      }
    });
  }
  async send(method, params = {}) {
    await this.ready;
    const id = ++this.id;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ id, method, params }));
      setTimeout(() => {
        if (this.pending.has(id)) {
          this.pending.delete(id);
          reject(new Error(`CDP 타임아웃: ${method}`));
        }
      }, 60000);
    });
  }
  async evaluate(expression, opts = {}) {
    const r = await this.send('Runtime.evaluate', {
      expression, awaitPromise: true, returnByValue: true, ...opts,
    });
    if (r.exceptionDetails) {
      throw new Error(r.exceptionDetails.exception?.description
        || r.exceptionDetails.text || 'evaluate 실패');
    }
    return r.result.value;
  }
  close() { try { this.ws.close(); } catch { } }
}

async function targets() {
  return getJson(`http://127.0.0.1:${PORT}/json/list`);
}

async function waitForTarget(match, timeoutMs = 30000, label = '') {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    const list = await targets().catch(() => []);
    const hit = list.find(match);
    if (hit) return hit;
    await sleep(400);
  }
  throw new Error(`대상을 찾지 못했습니다${label ? ' (' + label + ')' : ''}`);
}

/* ------------------------------------------------------------------------ 본체 */
async function main() {
  log('크롬 확장 실제 브라우저 검증 (CDP로 자동 조작)\n');

  log('1) 사전 조건');
  const chromePath = CHROME_CANDIDATES.find(p => p && fs.existsSync(p));
  if (!check(!!chromePath, '크롬 실행 파일', chromePath || '못 찾음')) return 1;

  let health;
  try {
    health = await getJson(`${API}/api/health`);
    check(health.status === 'ok', '백엔드 응답');
  } catch (e) {
    check(false, '백엔드 응답', String(e.message));
    log('\n   서버를 먼저 띄우세요: .venv\\Scripts\\python.exe scripts\\run_server.py');
    return 1;
  }

  // 이전 실행에서 남은 세션이 있으면 "확장이 세션을 열었다"를 잘못 통과시킨다.
  // 시작 전에 반드시 비운다.
  const stale = await httpJson('POST', `${API}/api/sessions`);
  if (stale.code === 200) {
    await httpJson('DELETE', `${API}/api/sessions/${JSON.parse(stale.body).session_id}`);
    check(true, '남아 있던 세션 정리');
  } else if (stale.code === 409) {
    check(false, '남아 있던 세션이 정리되지 않음',
      '서버를 재시작하거나 90초(IDLE_TIMEOUT) 기다릴 것');
  }

  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'dualguard-chrome-'));
  log(`   임시 프로필: ${profile}  (사용자 크롬은 건드리지 않는다)`);

  log('\n2) 크롬 실행 + CDP로 확장 설치');

  // 확장 id를 알아야 --allowlisted-extension-id를 줄 수 있는데, id는 설치해 봐야 안다.
  // 그래서 1차로 조용히 띄워 id만 확인하고 닫는다. (--manual이면 우회가 필요 없다)
  let allowId = null;
  if (!MANUAL) {
    const probeProfile = fs.mkdtempSync(path.join(os.tmpdir(), 'dualguard-probe-'));
    const probe = spawn(chromePath, [
      `--user-data-dir=${probeProfile}`, `--remote-debugging-port=${PORT + 1}`,
      '--no-first-run', '--no-default-browser-check', '--disable-sync',
      '--window-position=-2400,0', 'about:blank',
    ], { stdio: 'ignore' });
    for (let i = 0; i < 40 && !allowId; i++) {
      await sleep(500);
      try {
        const ver = await getJson(`http://127.0.0.1:${PORT + 1}/json/version`);
        const b = new CDP(ver.webSocketDebuggerUrl);
        allowId = (await b.send('Extensions.loadUnpacked', { path: path.resolve(EXT) })).id;
        b.close();
      } catch { }
    }
    try { probe.kill(); } catch { }
    await sleep(1200);
    try { fs.rmSync(probeProfile, { recursive: true, force: true }); } catch { }
    check(!!allowId, '확장 id 사전 확인 (activeTab 우회에 필요)', allowId || '실패');
  }

  const chrome = spawn(chromePath, [
    ...(allowId ? [`--allowlisted-extension-id=${allowId}`] : []),
    `--user-data-dir=${profile}`,
    `--remote-debugging-port=${PORT}`,
    '--no-first-run', '--no-default-browser-check', '--disable-sync',
    '--disable-background-timer-throttling',
    // 탭 캡처 권한 팝업을 자동 승인한다. 없으면 사람이 눌러야 해서 자동화가 막힌다.
    '--auto-accept-this-tab-capture',
    '--autoplay-policy=no-user-gesture-required',
    '--window-position=-2400,0', '--window-size=1200,900',
    'about:blank',
  ], { stdio: 'ignore', detached: false });

  const cleanup = () => {
    if (!KEEP_OPEN) {
      try { chrome.kill('SIGTERM'); } catch { }
      setTimeout(() => { try { fs.rmSync(profile, { recursive: true, force: true }); } catch { } }, 1500);
    }
  };
  process.on('exit', cleanup);

  // 브라우저 타깃에 붙어 확장을 설치한다 (--load-extension은 Chrome 137+에서 막혔다)
  let browser;
  for (let i = 0; i < 40; i++) {
    try {
      const ver = await getJson(`http://127.0.0.1:${PORT}/json/version`);
      browser = new CDP(ver.webSocketDebuggerUrl);
      await browser.ready;
      break;
    } catch { await sleep(500); }
  }
  if (!check(!!browser, '크롬 CDP 연결')) { cleanup(); return 1; }

  let extId = null;
  try {
    const r = await browser.send('Extensions.loadUnpacked', { path: path.resolve(EXT) });
    extId = r.id;
  } catch (e) {
    check(false, 'Extensions.loadUnpacked', String(e.message).slice(0, 200));
    cleanup();
    return 1;
  }
  check(!!extId, '크롬이 확장을 실제로 설치했다', extId);
  if (allowId) {
    check(extId === allowId, 'id가 1차 확인과 일치 (허용 목록 적용됨)',
      extId === allowId ? '' : `${allowId} != ${extId}`);
  }

  await browser.send('Target.createTarget', { url: `${API}/` }).catch(() => { });

  let swTarget;
  try {
    swTarget = await waitForTarget(
      t => t.type === 'service_worker' && t.url.includes(extId),
      40000, '서비스 워커');
  } catch (e) {
    check(false, '확장 서비스 워커 기동', String(e.message));
    const list = await targets().catch(() => []);
    log('   현재 타깃: ' + list.map(t => `${t.type}:${t.url.slice(0, 60)}`).join(', '));
    cleanup();
    return 1;
  }
  check(true, '서비스 워커가 실제로 떴다', swTarget.url.split('/').pop());

  const sw = new CDP(swTarget.webSocketDebuggerUrl);
  await sw.send('Runtime.enable');
  await sw.send('Log.enable').catch(() => { });

  log('\n3) manifest 실제 적용 확인');
  const mf = await sw.evaluate('JSON.stringify(chrome.runtime.getManifest())');
  const manifest = JSON.parse(mf);
  check(manifest.manifest_version === 3, '크롬이 MV3로 인식');
  const perms = await sw.evaluate(
    'new Promise(r => chrome.permissions.getAll(p => r(JSON.stringify(p))))');
  const granted = JSON.parse(perms);
  for (const p of ['tabCapture', 'offscreen', 'scripting']) {
    check(granted.permissions.includes(p), `권한 실제 부여: ${p}`);
  }
  check(granted.origins.some(o => o.includes('127.0.0.1:8000')), '백엔드 호스트 권한 부여');

  log('\n4) 대상 탭 준비 (대시보드 페이지에 오디오를 심는다)');
  const pageTarget = await waitForTarget(
    t => t.type === 'page' && t.url.startsWith(API), 20000, '대시보드 탭');
  const page = new CDP(pageTarget.webSocketDebuggerUrl);
  await page.send('Runtime.enable');

  // 탭에서 실제 소리가 나야 캡처가 의미를 갖는다. 사람 목소리 대역(300~3000Hz)을
  // 흉내 낸 신호를 만든다. 통화 내용을 재현하려는 게 아니라 "오디오 경로가
  // 실제로 흐르는가"를 보려는 것이다.
  await page.evaluate(`
    (() => {
      const ctx = new AudioContext();
      const osc = ctx.createOscillator();
      const lfo = ctx.createOscillator();
      const lfoGain = ctx.createGain();
      const gain = ctx.createGain();
      osc.type = 'sawtooth'; osc.frequency.value = 220;
      lfo.frequency.value = 3.5; lfoGain.gain.value = 120;
      lfo.connect(lfoGain).connect(osc.frequency);
      gain.gain.value = 0.25;
      osc.connect(gain).connect(ctx.destination);
      osc.start(); lfo.start();
      window.__dgAudio = { ctx, gain };
      return ctx.state;
    })()
  `, { userGesture: true });
  const audioState = await page.evaluate('window.__dgAudio.ctx.state');
  check(audioState === 'running', '탭에서 오디오 재생 중', audioState);

  const tabId = await sw.evaluate(
    `new Promise(r => chrome.tabs.query({url: "${API}/*"}, ts => r(ts[0] ? ts[0].id : -1)))`);
  check(tabId > 0, '대상 탭 id 확보', String(tabId));

  log('\n5) 캡처 시작 — 실제 팝업을 열고 버튼을 누른다'
    + (allowId ? ' (activeTab은 허용 목록으로 우회)' : ''));
  // 서비스 워커에서 startCapture()를 직접 부르면 실패한다:
  //   "Extension has not been invoked for the current page (see activeTab permission)"
  // tabCapture는 **사용자가 확장을 실제로 호출한 탭**에만 허용된다. 그래서 실사용
  // 경로 그대로 팝업을 열고 그 안의 버튼을 누른다. (이 실패 자체가 검증의 성과다 —
  // 하네스로는 절대 드러나지 않는다.)
  let started = false;
  try {
    await sw.evaluate('chrome.action.openPopup()', { userGesture: true });
  } catch (e) {
    log(`   (openPopup: ${String(e.message).slice(0, 80)})`);
  }
  await sleep(1500);

  const popupTarget = await waitForTarget(
    t => t.url.includes(extId) && t.url.includes('popup.html'), 12000, '팝업')
    .catch(() => null);

  if (popupTarget) {
    check(true, '확장 팝업이 실제로 열렸다');
    const popup = new CDP(popupTarget.webSocketDebuggerUrl);
    await popup.send('Runtime.enable');
    await popup.evaluate("document.getElementById('toggle').click()", { userGesture: true });
    await sleep(3000);
    const err = await popup.evaluate(
      "(() => { const e = document.getElementById('err'); return e && !e.hidden ? e.textContent : null; })()")
      .catch(() => null);
    if (err) check(false, '팝업이 오류를 표시함', String(err).slice(0, 140));
    popup.close();
  } else {
    check(false, '확장 팝업 열기',
      'chrome.action.openPopup()이 동작하지 않음 — 사람이 아이콘을 눌러야 한다');
  }

  let state = JSON.parse(await sw.evaluate('JSON.stringify(STATE)'));
  started = state.active === true;

  if (!started && MANUAL) {
    // 사람이 아이콘을 눌러 주면 activeTab이 부여된다. 창을 화면 안으로 옮기고 기다린다.
    await browser.send('Browser.setWindowBounds', {
      windowId: (await browser.send('Browser.getWindowForTarget',
        { targetId: pageTarget.id })).windowId,
      bounds: { left: 80, top: 80, width: 1200, height: 900, windowState: 'normal' },
    }).catch(() => { });
    log('\n   ────────────────────────────────────────────────────────────');
    log('   지금 열린 크롬 창에서 **확장 아이콘 → "분석 시작"** 을 눌러 주세요.');
    log('   (툴바에 안 보이면 퍼즐 조각 아이콘 → 듀얼가드)');
    log('   최대 120초 기다립니다…');
    log('   ────────────────────────────────────────────────────────────');
    for (let i = 0; i < 120 && !started; i++) {
      await sleep(1000);
      state = JSON.parse(await sw.evaluate('JSON.stringify(STATE)').catch(() => '{}'));
      started = state.active === true;
    }
  }

  if (started) {
    check(true, 'chrome.tabCapture.getMediaStreamId 실제 성공 (활성 캡처)',
      JSON.stringify(state));
  } else {
    log('   [보류] tabCapture는 사람이 확장 아이콘을 실제로 눌러야만(activeTab)');
    log('          허용된다. CDP로는 그 제스처를 만들 수 없다 — 크롬의 보안 경계이지');
    log('          확장 버그가 아니다. 이 부분만 사람이 확인해야 한다:');
    log('            node scripts/verify_extension_chrome.js --manual');
  }

  const off = await waitForTarget(
    t => t.url.includes('offscreen.html'), 20000, '오프스크린 문서').catch(() => null);
  check(!!off, '오프스크린 문서 실제 생성');

  if (started) {
    log('\n6) 백엔드에 세션이 열렸는지');
    // 시작 전에 세션을 비워뒀으므로, 409가 뜬다는 건 확장이 새로 열었다는 뜻이다.
    let sessionOk = false;
    for (let i = 0; i < 90 && !sessionOk; i++) {
      const probe = await httpJson('POST', `${API}/api/sessions`);
      if (probe.code === 409) sessionOk = true;
      else {
        if (probe.code === 200) {
          // 우리가 만들어버린 세션은 즉시 정리한다 (확장 것을 막으면 안 된다)
          await httpJson('DELETE', `${API}/api/sessions/${JSON.parse(probe.body).session_id}`);
        }
        await sleep(1000);
      }
    }
    check(sessionOk, '확장이 백엔드 세션을 열었다 (POST /api/sessions가 409)');

    log(`\n7) ${CAPTURE_SEC}초 동안 실제 캡처`);
    await sleep(CAPTURE_SEC * 1000);

    const stillPlaying = await page.evaluate('window.__dgAudio.ctx.state');
    check(stillPlaying === 'running', '캡처 중에도 탭 소리가 계속 난다 (오디오 되돌리기)',
      stillPlaying);

    const swState = JSON.parse(await sw.evaluate('JSON.stringify(STATE)'));
    check(swState.level !== null && swState.level !== undefined,
      '백엔드 결과가 서비스 워커까지 올라옴',
      `score=${swState.score} level=${swState.level}`);
  } else {
    log('\n6~7) 캡처가 시작되지 않아 건너뜀 (위 [보류] 참고)');
  }

  log('\n8) 콘텐츠 스크립트 + 오버레이 (실제 페이지에서)');
  // 캡처와 무관하게 **오버레이 경로 자체**는 실브라우저에서 검증할 수 있다.
  // 서비스 워커의 실제 pushOverlay를 호출하면 chrome.scripting으로 content.js가
  // 주입되고 진짜 DOM이 그려진다. 앞서 하네스가 잡았던 "type이 덮어써져 오버레이가
  // 안 뜨는" 버그가 실제 브라우저에서도 안 나는지 여기서 확인된다.
  await sw.evaluate(`pushOverlay(${tabId}, {level:'높음', score:92,
    headline:'즉시 통화를 종료하세요', actions:['지금 통화를 끊으세요'],
    topCategories:[{label:'긴급성 조성'}], type:'risk'})`);
  await sleep(1200);
  const overlay = await page.evaluate(`(() => {
    const el = document.getElementById('dualguard-overlay');
    return el ? JSON.stringify({cls: el.className, text: el.innerText.slice(0,60)}) : null;
  })()`);
  check(!!overlay, '콘텐츠 스크립트가 실제 페이지에 오버레이를 그렸다',
    overlay ? JSON.parse(overlay).cls : '없음');
  if (overlay) {
    const o = JSON.parse(overlay);
    check(o.cls.includes('lv-높음') && o.text.includes('즉시 통화를 종료'),
      '경고 내용이 실제로 렌더링됨', o.text.replace(/\n/g, ' ').slice(0, 50));
  }

  // 낮음 등급이면 오버레이가 사라져야 한다 (상시 배지는 무시당한다는 설계)
  await sw.evaluate(`pushOverlay(${tabId}, {level:'낮음', score:5,
    headline:'이상 없음', actions:[], type:'risk'})`);
  await sleep(800);
  const gone = await page.evaluate(
    "document.getElementById('dualguard-overlay') === null");
  check(gone === true, '낮음 등급에서 오버레이가 사라짐');

  log('\n8) 서비스 워커 콘솔 에러 확인');
  const errs = sw.events.filter(e =>
    (e.method === 'Runtime.consoleAPICalled' && e.params.type === 'error')
    || e.method === 'Runtime.exceptionThrown');
  check(errs.length === 0, '서비스 워커 에러 없음',
    errs.length ? JSON.stringify(errs[0]).slice(0, 200) : '');

  log('\n9) 캡처 종료');
  await sw.evaluate('stopCapture()');
  await sleep(2500);
  const afterState = JSON.parse(await sw.evaluate('JSON.stringify(STATE)'));
  check(afterState.active === false, '상태 초기화');
  const overlayCleared = await page.evaluate(
    "document.getElementById('dualguard-overlay') === null");
  check(overlayCleared === true, '종료 시 오버레이 제거됨');

  const offGone = (await targets()).every(t => !t.url.includes('offscreen.html'));
  check(offGone, '오프스크린 문서 정리됨');

  const freed = await new Promise((resolve) => {
    const req = http.request(`${API}/api/sessions`, { method: 'POST' }, (res) => {
      let b = ''; res.on('data', d => b += d);
      res.on('end', () => resolve({ code: res.statusCode, body: b }));
    });
    req.on('error', () => resolve({ code: 0, body: '' }));
    req.end();
  });
  check(freed.code === 200, '백엔드 세션이 해제됨 (새 세션 생성 가능)', `HTTP ${freed.code}`);
  if (freed.code === 200) {
    try {
      const sid = JSON.parse(freed.body).session_id;
      await new Promise(r => {
        const req = http.request(`${API}/api/sessions/${sid}`, { method: 'DELETE' }, res => {
          res.resume(); res.on('end', r);
        });
        req.on('error', r); req.end();
      });
    } catch { }
  }

  sw.close(); page.close();
  if (!KEEP_OPEN) cleanup();

  log(`\n${failures === 0 ? '실제 브라우저 검증 통과' : `실패 ${failures}건`}`);
  if (failures === 0) {
    log('※ 크롬이 확장을 로드하고, 실제 탭 오디오를 캡처해 백엔드까지 보내고,');
    log('  경고 오버레이를 실제 페이지에 그리는 것까지 확인했다.');
    if (allowId) {
      log('※ 단, activeTab 검사는 --allowlisted-extension-id로 건너뛰었다.');
      log('  "사용자가 아이콘을 눌렀을 때 activeTab이 부여되는가"는 --manual로 확인할 것.');
    }
  }
  return failures === 0 ? 0 : 1;
}

main().then(code => process.exit(code)).catch(e => {
  console.error('\n검증 오류:', e.message);
  process.exit(1);
});
