/* 웹 화면 UI 점검 하네스 (헤드리스 크롬)
   담당: 이상원

   ## 왜 필요한가

   글자가 잘리거나 레이아웃이 깨지는 건 코드를 읽어서는 안 보인다. 실제로 렌더링해
   봐야 한다. 심사장에서 처음 발견하면 고칠 시간이 없다.

   이 스크립트는 헤드리스 크롬으로 각 화면을 띄워서 두 가지를 한다:

     ① **스크린샷**을 떠서 사람이 눈으로 볼 수 있게 남긴다
     ② **잘림을 코드로 검출**한다 — 눈으로 놓치는 걸 잡는다
        - scrollWidth > clientWidth  (가로로 넘쳐서 잘림)
        - scrollHeight > clientHeight (세로로 넘쳐서 잘림)
        - 요소가 뷰포트 밖으로 나감
        - 텍스트가 부모 밖으로 삐져나감

   데스크톱(1280)과 모바일(390) 두 폭에서 각각 본다. style.css에
   `@media (max-width:820px)` 가 있어서 두 경로가 갈린다.

   서버를 먼저 띄워야 한다:
       .venv\Scripts\python.exe scripts\run_server.py

   실행:
       node scripts/audit_ui.js
       node scripts/audit_ui.js --out <디렉터리>   (스크린샷 저장 위치) */

const fs = require('fs');
const path = require('path');
const http = require('http');
const { spawn } = require('child_process');

const ROOT = path.resolve(__dirname, '..');
const API = 'http://127.0.0.1:8000';
const PORT = 9333;

const args = process.argv.slice(2);
const argOf = (name, dflt) => {
  const i = args.indexOf(name);
  return i >= 0 && args[i + 1] ? args[i + 1] : dflt;
};
const OUT = path.resolve(argOf('--out', path.join(ROOT, 'data', 'ui_audit')));

const CHROME_CANDIDATES = [
  path.join(process.env['ProgramFiles'] || '', 'Google/Chrome/Application/chrome.exe'),
  path.join(process.env['ProgramFiles(x86)'] || '', 'Google/Chrome/Application/chrome.exe'),
  path.join(process.env.LOCALAPPDATA || '', 'Google/Chrome/Application/chrome.exe'),
];

const log = (...a) => console.log(...a);
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

/* ------------------------------------------------------------------ CDP 최소 클라이언트
   puppeteer를 새로 깔지 않는다. 시연 환경에 의존성을 늘리지 않으려는 이 프로젝트의
   방침(README 「코드 컨벤션」)을 따른다. WebSocket 프레이밍만 직접 쓴다. */
const crypto = require('crypto');
const net = require('net');

class CDP {
  constructor(wsUrl) {
    this.wsUrl = wsUrl;
    this.id = 0;
    this.pending = new Map();
    this.buf = Buffer.alloc(0);
  }
  connect() {
    return new Promise((resolve, reject) => {
      const u = new URL(this.wsUrl);
      this.sock = net.connect(Number(u.port), u.hostname, () => {
        const key = crypto.randomBytes(16).toString('base64');
        this.sock.write(
          `GET ${u.pathname}${u.search} HTTP/1.1\r\n` +
          `Host: ${u.host}\r\n` +
          `Upgrade: websocket\r\nConnection: Upgrade\r\n` +
          `Sec-WebSocket-Key: ${key}\r\nSec-WebSocket-Version: 13\r\n\r\n`);
      });
      this.sock.on('error', reject);
      let handshook = false;
      this.sock.on('data', (d) => {
        if (!handshook) {
          const s = d.toString('binary');
          const i = s.indexOf('\r\n\r\n');
          if (i < 0) return;
          handshook = true;
          this.buf = Buffer.from(s.slice(i + 4), 'binary');
          this._drain();
          resolve();
          return;
        }
        this.buf = Buffer.concat([this.buf, d]);
        this._drain();
      });
    });
  }
  _drain() {
    for (;;) {
      if (this.buf.length < 2) return;
      const len0 = this.buf[1] & 0x7f;
      let off = 2, len = len0;
      if (len0 === 126) { if (this.buf.length < 4) return; len = this.buf.readUInt16BE(2); off = 4; }
      else if (len0 === 127) { if (this.buf.length < 10) return; len = Number(this.buf.readBigUInt64BE(2)); off = 10; }
      if (this.buf.length < off + len) return;
      const payload = this.buf.subarray(off, off + len).toString('utf8');
      this.buf = this.buf.subarray(off + len);
      try {
        const msg = JSON.parse(payload);
        if (msg.id && this.pending.has(msg.id)) {
          const { resolve, reject } = this.pending.get(msg.id);
          this.pending.delete(msg.id);
          msg.error ? reject(new Error(JSON.stringify(msg.error))) : resolve(msg.result);
        }
      } catch (_) { /* 이벤트 프레임은 무시한다 */ }
    }
  }
  send(method, params = {}) {
    const id = ++this.id;
    // 클라이언트→서버 프레임은 마스킹이 필수다(RFC 6455). 빼면 크롬이 연결을 끊는다.
    const body = Buffer.from(JSON.stringify({ id, method, params }), 'utf8');
    const mask = crypto.randomBytes(4);
    const head = [];
    head.push(0x81);
    if (body.length < 126) head.push(0x80 | body.length);
    else if (body.length < 65536) { head.push(0x80 | 126, body.length >> 8 & 0xff, body.length & 0xff); }
    else {
      head.push(0x80 | 127);
      for (let i = 7; i >= 0; i--) head.push(Number((BigInt(body.length) >> BigInt(8 * i)) & 0xffn));
    }
    const masked = Buffer.alloc(body.length);
    for (let i = 0; i < body.length; i++) masked[i] = body[i] ^ mask[i % 4];
    this.sock.write(Buffer.concat([Buffer.from(head), mask, masked]));
    return new Promise((resolve, reject) => this.pending.set(id, { resolve, reject }));
  }
  async eval(expr) {
    const r = await this.send('Runtime.evaluate', {
      expression: expr, returnByValue: true, awaitPromise: true,
    });
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.text + ' ' + (r.result && r.result.description || ''));
    return r.result.value;
  }
  close() { try { this.sock.destroy(); } catch (_) {} }
}

/* --------------------------------------------------------------- 잘림 검출 스크립트
   브라우저 안에서 도는 코드. 보이는 요소만 본다(hidden 화면까지 재면 오탐이 쏟아진다). */
const CLIP_PROBE = `(() => {
  const out = [];
  const seen = new Set();
  const vw = window.innerWidth;
  for (const el of document.querySelectorAll('body *')) {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || cs.opacity === '0') continue;
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;

    const label = (el.id ? '#' + el.id : el.tagName.toLowerCase() + (el.className && typeof el.className === 'string' ? '.' + el.className.trim().split(/\\s+/)[0] : ''));
    const text = (el.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 48);

    // ① 가로 넘침 — overflow가 숨김/자름이면 글자가 실제로 안 보인다
    const oxHidden = cs.overflowX === 'hidden' || cs.overflowX === 'clip';
    if (el.scrollWidth > el.clientWidth + 1 && el.clientWidth > 0 && oxHidden) {
      const k = 'w|' + label; if (!seen.has(k)) { seen.add(k);
        out.push({ kind: '가로잘림', el: label, detail: el.scrollWidth + 'px > ' + el.clientWidth + 'px', text }); }
    }
    // ② 세로 넘침
    const oyHidden = cs.overflowY === 'hidden' || cs.overflowY === 'clip';
    if (el.scrollHeight > el.clientHeight + 1 && el.clientHeight > 0 && oyHidden) {
      const k = 'h|' + label; if (!seen.has(k)) { seen.add(k);
        out.push({ kind: '세로잘림', el: label, detail: el.scrollHeight + 'px > ' + el.clientHeight + 'px', text }); }
    }
    // ③ 뷰포트 밖으로 삐져나감 (가로 스크롤바를 만든다)
    if (r.right > vw + 1) {
      const k = 'v|' + label; if (!seen.has(k)) { seen.add(k);
        out.push({ kind: '화면밖', el: label, detail: Math.round(r.right) + 'px > 화면폭 ' + vw + 'px', text }); }
    }
  }
  return {
    issues: out,
    pageScrollX: document.documentElement.scrollWidth > document.documentElement.clientWidth,
    docW: document.documentElement.scrollWidth,
    viewW: document.documentElement.clientWidth,
  };
})()`;

const VIEWPORTS = [
  { name: 'desktop', width: 1280, height: 900 },
  { name: 'mobile', width: 390, height: 844 },   // iPhone 14 기준. @media(max-width:820px) 경로
];

async function main() {
  // 서버가 떠 있는지 먼저 본다 — 안 떠 있으면 빈 화면만 찍힌다
  try { await getJson(`${API}/api/health`); }
  catch (e) {
    console.error('서버가 응답하지 않습니다. 먼저 띄우세요:');
    console.error('   .venv\\Scripts\\python.exe scripts\\run_server.py');
    process.exit(1);
  }

  const chrome = CHROME_CANDIDATES.find(p => p && fs.existsSync(p));
  if (!chrome) { console.error('크롬을 찾지 못했습니다.'); process.exit(1); }

  fs.mkdirSync(OUT, { recursive: true });
  const userDir = path.join(OUT, '_profile');

  const proc = spawn(chrome, [
    '--headless=new', `--remote-debugging-port=${PORT}`,
    `--user-data-dir=${userDir}`, '--no-first-run', '--no-default-browser-check',
    '--hide-scrollbars', '--force-device-scale-factor=1',
    'about:blank',
  ], { stdio: 'ignore' });

  let target = null;
  for (let i = 0; i < 60 && !target; i++) {
    await sleep(500);
    try {
      const list = await getJson(`http://127.0.0.1:${PORT}/json/list`);
      target = list.find(t => t.type === 'page');
    } catch (_) { /* 아직 안 떴다 */ }
  }
  if (!target) { proc.kill(); console.error('크롬 기동 실패'); process.exit(1); }

  const cdp = new CDP(target.webSocketDebuggerUrl);
  await cdp.connect();
  await cdp.send('Page.enable');
  await cdp.send('Runtime.enable');
  // 캐시를 반드시 꺼야 한다. --user-data-dir를 재사용하므로 style.css/app.js를
  // 고친 뒤 다시 돌려도 **옛 파일로 검사하게 된다**(실제로 그래서 고친 문제가
  // 그대로 남아있는 것처럼 나왔다).
  await cdp.send('Network.enable');
  await cdp.send('Network.setCacheDisabled', { cacheDisabled: true });

  // 콘솔 에러도 같이 모은다
  await cdp.eval(`window.__errs = []; window.addEventListener('error', e => window.__errs.push(String(e.message)));
                  console.error = ((o) => (...a) => { window.__errs.push(a.join(' ')); o(...a); })(console.error); true`);

  // 결과 화면을 보려면 분석 기록이 하나는 있어야 한다
  // 결과 화면은 저장된 분석 기록을 열어서 본다. 상세는 /api/history/{id} 가 준다
  // (/api/results/{id} 는 메모리에 살아있는 job 전용이라 서버를 재시작하면 없다).
  const hist = await getJson(`${API}/api/history?limit=50`);
  const items = hist.items || [];
  // 빨강 사례가 요소가 제일 많아서(구간·근거·경고) 잘림을 보기에 좋다
  const anyJob = items.find(x => x.overall_level === '높음') || items[0];

  const SCREENS = [
    { id: 'onboarding', label: '⓪ 온보딩', go: `document.querySelectorAll('.screen').forEach(s=>s.classList.remove('active')); document.getElementById('screenOnboarding').classList.add('active'); true` },
    { id: 'home', label: '① 홈 대시보드', go: `document.getElementById('onboardStart').click(); true` },
    { id: 'realtime', label: '② 실시간 모니터링', go: `document.querySelectorAll('.screen').forEach(s=>s.classList.remove('active')); document.getElementById('screenRealtime').classList.add('active'); true` },
    { id: 'upload', label: '③ 업로드', go: `document.querySelectorAll('.screen').forEach(s=>s.classList.remove('active')); document.getElementById('screenUpload').classList.add('active'); true` },
  ];

  const report = [];

  for (const vp of VIEWPORTS) {
    await cdp.send('Emulation.setDeviceMetricsOverride', {
      width: vp.width, height: vp.height, deviceScaleFactor: 1, mobile: vp.name === 'mobile',
    });

    for (const sc of SCREENS) {
      await cdp.send('Page.navigate', { url: API });
      await sleep(1400);
      try { await cdp.eval(sc.go); } catch (e) { /* 화면 id가 없을 수 있다 */ }
      await sleep(500);

      const probe = await cdp.eval(CLIP_PROBE);
      const shot = await cdp.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true });
      const file = path.join(OUT, `${vp.name}_${sc.id}.png`);
      fs.writeFileSync(file, Buffer.from(shot.data, 'base64'));

      report.push({ viewport: vp.name, screen: sc.label, file, ...probe });
    }
  }

  // 결과 화면은 실제 분석 기록을 열어서 본다
  if (anyJob) {
    const jid = anyJob.job_id || anyJob.id;
    for (const vp of VIEWPORTS) {
      await cdp.send('Emulation.setDeviceMetricsOverride', {
        width: vp.width, height: vp.height, deviceScaleFactor: 1, mobile: vp.name === 'mobile',
      });
      await cdp.send('Page.navigate', { url: API });
      await sleep(1400);
      try {
        await cdp.eval(`(async () => {
          const r = await fetch('/api/history/${jid}'); const d = await r.json();
          render(d.report);
          document.querySelectorAll('.screen').forEach(s=>s.classList.remove('active'));
          document.getElementById('screenResult').classList.add('active');
          return true;
        })()`);
      } catch (e) { console.log('   (결과 화면 렌더 실패: ' + String(e).slice(0, 90) + ')'); continue; }
      await sleep(900);
      const probe = await cdp.eval(CLIP_PROBE);
      const shot = await cdp.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true });
      const file = path.join(OUT, `${vp.name}_result.png`);
      fs.writeFileSync(file, Buffer.from(shot.data, 'base64'));
      report.push({ viewport: vp.name, screen: '④ 결과', file, ...probe });
    }
  } else {
    console.log('   (분석 기록이 없어 결과 화면은 건너뜁니다)');
  }

  const errs = await cdp.eval('window.__errs || []');

  cdp.close();
  proc.kill();

  // ------------------------------------------------------------------ 리포트
  log('\n=== UI 점검 결과 ===\n');
  let total = 0;
  for (const r of report) {
    const n = r.issues.length;
    total += n;
    const scroll = r.pageScrollX ? `  ⚠ 가로 스크롤 발생(${r.docW} > ${r.viewW})` : '';
    log(`[${r.viewport}] ${r.screen}  문제 ${n}건${scroll}`);
    for (const i of r.issues) {
      log(`    - ${i.kind}  ${i.el}  (${i.detail})`);
      if (i.text) log(`        "${i.text}"`);
    }
    log(`    스크린샷: ${path.relative(ROOT, r.file)}`);
  }
  log(`\n총 ${total}건`);
  if (errs && errs.length) {
    log('\n콘솔 에러:');
    for (const e of errs.slice(0, 10)) log('   - ' + String(e).slice(0, 160));
  } else {
    log('\n콘솔 에러 없음');
  }
  log(`\n스크린샷 위치: ${OUT}`);
}

main().catch(e => { console.error(e); process.exit(1); });
