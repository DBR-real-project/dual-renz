/* 듀얼가드 대시보드 로직
   담당: 이상원 (업로드/결과 렌더링) / 홍수지 (온보딩·홈·실시간 화면)

   외부 라이브러리를 쓰지 않는다. 차트도 SVG를 직접 그린다.
   이유: 해커톤 시연 환경의 네트워크를 믿을 수 없다. CDN이 막히면 화면이 통째로 죽는다.
   d3/chart.js를 번들하려면 빌드 단계가 생기는데, 그것도 시연 직전에 깨질 수 있는 요소다. */

const $ = (id) => document.getElementById(id);
const screens = {
  onboarding: $('screenOnboarding'), home: $('screenHome'), realtime: $('screenRealtime'),
  upload: $('screenUpload'), progress: $('screenProgress'), result: $('screenResult'),
  history: $('screenHistory'),
};
let selectedFile = null;

function show(name) {
  Object.values(screens).forEach(s => s.classList.remove('active'));
  screens[name].classList.add('active');
  document.querySelectorAll('.navbtn').forEach(b => b.classList.toggle('active', b.dataset.nav === name));
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

/* ---------- 온보딩 ---------- */
$('onboardStart').addEventListener('click', () => {
  document.body.classList.add('app-ready');
  $('topnav').hidden = false;
  show('home');
  loadHomeStats();
});
$('onboardPolicyLink').addEventListener('click', e => {
  e.preventDefault();
  document.body.classList.add('app-ready');
  $('topnav').hidden = false;
  show('history');
  $('privacyDetails').open = true;
  $('privacyDetails').scrollIntoView({ behavior: 'smooth' });
});
$('brandHome').addEventListener('click', () => {
  if (document.body.classList.contains('app-ready')) { show('home'); loadHomeStats(); }
});
document.querySelectorAll('[data-nav]').forEach(el => {
  el.addEventListener('click', () => {
    const name = el.dataset.nav;
    show(name);
    if (name === 'home') loadHomeStats();
    if (name === 'history') loadHistory();
  });
});

/* ---------- 엔진 상태 ---------- */
async function loadHealth() {
  try {
    const r = await fetch('/api/health');
    const d = await r.json();
    const labels = {
      stt: 'STT', content_llm: 'LLM 화법분석', rag: 'RAG 사례',
      audio_spoof: '음성 스푸핑', deepfake: '딥페이크',
    };
    $('engineStatus').innerHTML = Object.entries(d.engines).map(([k, v]) =>
      `<span class="chip ${v.ready ? 'on' : 'off'}" title="${esc(v.detail)}">${
        v.ready ? '●' : '○'} ${labels[k] || k}</span>`).join('');
  } catch { /* 상태 표시는 실패해도 본 기능에 영향 없음 */ }
}

/* ---------- 홈 대시보드 ---------- */
async function loadHomeStats() {
  try {
    const r = await fetch('/api/history?limit=200');
    const d = await r.json();
    const items = d.items || [];
    $('statTotal').textContent = items.length;
    $('statThreat').textContent = items.filter(i => i.overall_level && i.overall_level !== '낮음').length;
    // 미디어 위조 탐지 건수: media_risk(음성 AASIST + 영상 FF++ 결합값)가 딥페이크 판정
    // 임계값(재척도 후 50, calibrate_deepfake.py 근거)을 넘긴 건수로 근사한다.
    $('statMedia').textContent = items.filter(i => (i.media_risk || 0) >= 50).length;

    const list = $('recentList');
    if (!items.length) { list.innerHTML = '<p class="hist-empty">아직 분석한 통화가 없습니다.</p>'; return; }
    list.innerHTML = items.slice(0, 5).map(historyRowHtml).join('');
    bindHistoryRows(list);
  } catch { /* 통계 실패가 홈 화면 진입을 막지 않는다 */ }
}

/* ---------- 항상 위에 뜨는 경고창 (Document Picture-in-Picture) ----------

   ## 왜 필요한가

   웹 대시보드로 화면 공유를 하면 분석은 되는데 **경고를 볼 수가 없다.** 사용자는
   통화 화면을 보고 있고 우리 탭은 뒤에 있기 때문이다. 브라우저 보안상 웹페이지는
   다른 사이트 위에 아무것도 그릴 수 없다(그게 가능하면 피싱 도구가 된다).

   크롬 확장이 그 권한을 정식으로 받는 경로지만, 확장은 설치가 필요하다.
   **Document Picture-in-Picture**(크롬 116+)를 쓰면 설치 없이도 된다 —
   항상 위에 떠 있는 작은 창을 만들 수 있고, 브라우저 밖 다른 프로그램 위에도 뜬다.

   즉 통화 화면을 전체화면으로 보면서 위험도를 계속 볼 수 있다.

   지원하지 않는 브라우저면 조용히 건너뛴다. 그때는 기존처럼 우리 탭에서 본다. */

let pipWin = null;

function pipSupported() {
  return 'documentPictureInPicture' in window;
}

async function openRiskPip() {
  if (!pipSupported() || pipWin) return;
  try {
    // 사용자 제스처(공유 시작 클릭) 안에서 호출해야 열린다.
    pipWin = await documentPictureInPicture.requestWindow({ width: 300, height: 190 });
  } catch (e) {
    pipWin = null;   // 차단됐으면 조용히 포기 — 통화를 방해하지 않는다
    return;
  }
  const d = pipWin.document;
  d.title = '듀얼가드 실시간 경고';
  const style = d.createElement('style');
  style.textContent = `
    :root{color-scheme:light}
    body{margin:0;font-family:"Malgun Gothic","Pretendard",sans-serif;
         background:#16202e;color:#fff;word-break:keep-all;
         display:flex;flex-direction:column;height:100vh}
    .hd{padding:9px 12px 0;font-size:11px;color:#9fb0c4;letter-spacing:-.2px}
    .mid{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px}
    .num{font-size:46px;font-weight:800;line-height:1}
    .lv{font-size:13px;font-weight:700;padding:1px 10px;border-radius:99px}
    .sub{font-size:11px;color:#9fb0c4}
    .why{padding:0 12px 10px;font-size:11.5px;line-height:1.4;text-align:center;min-height:32px}
    .lv-낮음{background:#1e7a45}.lv-중간{background:#a9761a}.lv-높음{background:#c0392b}
    .n-낮음{color:#5ee08f}.n-중간{color:#ffc861}.n-높음{color:#ff8a80}
    body.alert{animation:flash 1s ease-in-out 3}
    @keyframes flash{0%,100%{background:#16202e}50%{background:#5a1a1a}}
  `;
  d.head.appendChild(style);
  d.body.innerHTML = `
    <div class="hd">듀얼가드 · 통화 위험도</div>
    <div class="mid">
      <div class="num n-낮음" id="pNum">0</div>
      <div class="lv lv-낮음" id="pLv">분석 중</div>
      <div class="sub"><span id="pC">0</span> 화법 · <span id="pM">0</span> 음성</div>
    </div>
    <div class="why" id="pWhy">통화를 듣고 있습니다…</div>`;

  // 사용자가 PiP 창을 직접 닫으면 참조를 정리한다(다시 열 수 있게).
  pipWin.addEventListener('pagehide', () => { pipWin = null; });
}

function updateRiskPip(snap, reason) {
  if (!pipWin || pipWin.closed) return;
  const d = pipWin.document;
  const lv = snap.overall_level || '낮음';
  const num = d.getElementById('pNum');
  const lvEl = d.getElementById('pLv');
  if (!num || !lvEl) return;
  num.textContent = Math.round(snap.overall_score);
  num.className = `num n-${lv}`;
  lvEl.textContent = lv;
  lvEl.className = `lv lv-${lv}`;
  d.getElementById('pC').textContent = Math.round(snap.content_risk);
  d.getElementById('pM').textContent = Math.round(snap.media_risk);
  d.getElementById('pWhy').textContent =
    lv === '낮음' ? '지금까지 위험 신호 없음' : (reason || '위험 신호가 감지되었습니다.');
  // '높음'으로 올라간 순간에만 깜빡인다. 계속 깜빡이면 통화를 방해한다.
  if (lv === '높음' && !d.body.classList.contains('alert')) {
    d.body.classList.add('alert');
  } else if (lv !== '높음') {
    d.body.classList.remove('alert');
  }
}

function closeRiskPip() {
  try { pipWin && !pipWin.closed && pipWin.close(); } catch { }
  pipWin = null;
}

/* ---------- 실시간 화상통화 검증 (getDisplayMedia + 세션 스트리밍 API) ---------- */
let rtSession = null;      // { session_id, ... }
let rtRecorder = null;
let rtDisplayStream = null;
let rtAudioStream = null;
let rtSegments = [];       // 세션 동안 누적된 구간 (정규화 전 원본)
let rtTimer = null;
let rtStartedAt = null;
let rtWarningDismissedFor = null; // 같은 등급 경고를 반복해서 띄우지 않기 위한 표시
let rtCurrentLevel = '낮음';

$('ctaRealtime').addEventListener('click', () => { show('realtime'); resetRealtimeUI(); });
$('ctaUpload').addEventListener('click', () => show('upload'));

function resetRealtimeUI() {
  $('rtSetup').hidden = false;
  $('rtLive').hidden = true;
  $('rtErr').hidden = true;
  $('rtWarning').hidden = true;
}

$('rtStartBtn').addEventListener('click', startRealtime);
$('rtStopBtn').addEventListener('click', () => endRealtime(false));
$('rtContinueBtn').addEventListener('click', () => {
  // 닫기만 하고 계속 통화 — 같은 등급에서 경고가 반복 노출되지 않도록 표시해둔다.
  // 등급이 다시 올라가면(예: 중간→높음) 재노출된다.
  rtWarningDismissedFor = rtCurrentLevel;
  $('rtWarning').hidden = true;
});
$('rtEndBtn').addEventListener('click', () => endRealtime(true));

async function startRealtime() {
  $('rtErr').hidden = true;
  try {
    // 1) 백엔드 실시간 세션 생성 (모델을 미리 상주시킨다 — 안 하면 첫 청크가 26초 걸린다)
    const sr = await fetch('/api/sessions', { method: 'POST' });
    const sd = await sr.json();
    if (!sr.ok) throw new Error(sd.detail || '실시간 세션을 시작하지 못했습니다');
    rtSession = sd;

    // 2) 브라우저 네이티브 화면/탭 공유 — 별도 설치 없이 동작하는 기획서 핵심 제약
    rtDisplayStream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: true });
  } catch (e) {
    $('rtErr').textContent = e.name === 'NotAllowedError'
      ? '화면 공유가 취소되었습니다.'
      : (e.message || '화면 공유를 시작할 수 없습니다');
    $('rtErr').hidden = false;
    if (rtSession) { fetch(`/api/sessions/${rtSession.session_id}`, { method: 'DELETE' }).catch(() => {}); rtSession = null; }
    return;
  }

  const audioTrack = rtDisplayStream.getAudioTracks()[0];
  if (!audioTrack) {
    $('rtErr').textContent = '공유한 화면·탭에 오디오가 없습니다. 공유 선택창에서 "탭 오디오 공유"를 체크한 뒤 다시 시도하세요.';
    $('rtErr').hidden = false;
    rtDisplayStream.getTracks().forEach(t => t.stop());
    fetch(`/api/sessions/${rtSession.session_id}`, { method: 'DELETE' }).catch(() => {});
    rtSession = null;
    return;
  }

  // 영상 트랙은 즉시 끈다 — 현재 실시간 경로는 음성·화법만 본다(streaming.py 설계 제약).
  // 영상 딥페이크는 통화 종료 후(안전 종료) 별도 파일 분석으로 넘기는 것을 로드맵으로 남겨둔다.
  rtDisplayStream.getVideoTracks().forEach(t => t.stop());
  rtAudioStream = new MediaStream([audioTrack]);
  audioTrack.addEventListener('ended', () => endRealtime(false)); // 사용자가 브라우저 UI로 공유 중단

  // 통화 화면을 보는 동안에도 위험도를 볼 수 있게 항상 위에 뜨는 창을 연다.
  // 지원 안 하면 조용히 넘어가고 기존처럼 이 탭에서 본다.
  await openRiskPip();

  rtSegments = [];
  rtStartedAt = Date.now();
  rtWarningDismissedFor = null;
  rtCurrentLevel = '낮음';
  $('rtSetup').hidden = true;
  $('rtLive').hidden = false;
  setRtBadge('낮음');
  $('rtElapsed').textContent = '0:00';
  $('rtContent').textContent = '0';
  $('rtMedia').textContent = '0';
  $('rtOverall').textContent = '0';
  $('rtOverall').className = 'rt-overall-num lv-낮음';

  rtTimer = setInterval(() => {
    const sec = Math.floor((Date.now() - rtStartedAt) / 1000);
    $('rtElapsed').textContent = `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, '0')}`;
  }, 1000);

  // 4~5초 청크 — 기획서 "약 4초 Sliding Window" 근사, AASIST 최소 길이(3초) 확보
  let mimeType = 'audio/webm;codecs=opus';
  if (!MediaRecorder.isTypeSupported(mimeType)) mimeType = '';
  rtRecorder = new MediaRecorder(rtAudioStream, mimeType ? { mimeType } : undefined);
  rtRecorder.addEventListener('dataavailable', ev => { if (ev.data && ev.data.size) pushChunk(ev.data); });
  rtRecorder.start(5000);
}

async function pushChunk(blob) {
  if (!rtSession) return;
  const fd = new FormData();
  fd.append('file', blob, 'chunk.webm');
  try {
    const r = await fetch(`/api/sessions/${rtSession.session_id}/chunk`, { method: 'POST', body: fd });
    if (!r.ok) return; // 청크 하나 실패는 무시하고 다음 청크로 계속 이어간다
    const d = await r.json();
    if (d.new_segments && d.new_segments.length) rtSegments.push(...d.new_segments);
    applyRtSnapshot(d);
  } catch { /* 네트워크 순간 끊김은 다음 청크에서 회복 시도 */ }
}

function applyRtSnapshot(snap) {
  $('rtContent').textContent = Math.round(snap.content_risk);
  $('rtMedia').textContent = Math.round(snap.media_risk);
  $('rtOverall').textContent = Math.round(snap.overall_score);
  $('rtOverall').className = `rt-overall-num lv-${snap.overall_level}`;
  rtCurrentLevel = snap.overall_level;
  setRtBadge(snap.overall_level);

  // 판단 근거는 등급과 무관하게 만든다 — 항상 위에 뜨는 창(PiP)에도 넘겨야 하고,
  // 거기서는 '중간'이어도 왜 올라갔는지 보여주는 게 쓸모 있다.
  const last = snap.new_segments && snap.new_segments.length ? snap.new_segments[snap.new_segments.length - 1] : null;
  const reason = last && last.top_category
    ? `감지된 패턴: ${esc(last.top_category)}${last.matched_terms && last.matched_terms.length ? ' · "' + esc(last.matched_terms[0]) + '"' : ''}`
    : (snap.media_risk >= 50 ? '음성 위조 위험 신호가 감지되었습니다.' : '누적된 대화 패턴에서 위험 신호가 감지되었습니다.');

  if (snap.overall_level !== '낮음' && rtWarningDismissedFor !== snap.overall_level) {
    $('rtWarningReason').textContent = reason;
    $('rtWarningScore').textContent = Math.round(snap.overall_score);
    $('rtWarning').hidden = false;
  }

  updateRiskPip(snap, reason);
}

function setRtBadge(level) {
  const badge = $('rtBadge');
  badge.className = `rt-badge lv-${level}`;
  const text = { '낮음': '듀얼가드 모니터링: 정상', '중간': '듀얼가드 모니터링: 주의', '높음': '듀얼가드 모니터링: 위험' }[level] || '듀얼가드 모니터링: 정상';
  $('rtBadgeText').textContent = text;
}

async function endRealtime(userInitiatedFromWarning) {
  clearInterval(rtTimer);
  closeRiskPip();   // 통화가 끝나면 항상 위에 뜨던 창도 같이 닫는다
  if (rtRecorder && rtRecorder.state !== 'inactive') { try { rtRecorder.stop(); } catch {} }
  if (rtDisplayStream) rtDisplayStream.getTracks().forEach(t => t.stop());
  if (rtAudioStream) rtAudioStream.getTracks().forEach(t => t.stop());

  let finalSnap = null;
  if (rtSession) {
    try {
      const r = await fetch(`/api/sessions/${rtSession.session_id}`, { method: 'DELETE' });
      finalSnap = await r.json();
    } catch { /* 종료 요청이 실패해도 화면은 리포트로 넘어간다 */ }
  }
  rtSession = null;

  if (finalSnap && rtSegments.length) {
    render(buildStreamReport(finalSnap, rtSegments));
    show('result');
  } else {
    show('home');
  }
  loadHomeStats();
}

/* 실시간 세션 스냅샷 + 구간 목록을 업로드 분석 리포트와 같은 모양으로 맞춰서
   기존 render()/drawChart()/drawTimeline()을 그대로 재사용한다. */
function normalizeStreamSegment(s) {
  return {
    start: s.start, end: s.end, transcript: s.transcript,
    content_risk: s.content_risk, media_risk: s.media_risk,
    fraud_risk_score: s.fraud_risk_score, level: s.level,
    content_detail: {
      top_category_label: s.top_category,
      matched_terms: s.top_category ? { [s.top_category]: s.matched_terms || [] } : {},
    },
    // 실시간 경로에서 media_risk는 그 구간의 AASIST 음성 스푸핑 점수 그 자체다(영상은 안 봄).
    audio_spoof_score: s.media_risk, deepfake_score: null,
    frames_analyzed: null, faces_detected: null, rag_matches: [],
  };
}
function topCategoriesFromStreamSegments(normSegs) {
  const best = {};
  normSegs.forEach(s => {
    const label = s.content_detail.top_category_label;
    if (!label || s.content_risk <= 0) return;
    if (!best[label] || s.content_risk > best[label]) best[label] = s.content_risk;
  });
  return Object.entries(best).map(([label, score]) => ({ label, score })).sort((a, b) => b.score - a.score).slice(0, 8);
}
function buildStreamReport(snapshot, rawSegments) {
  const segments = rawSegments.map(normalizeStreamSegment);
  return {
    overall_score: snapshot.overall_score, overall_level: snapshot.overall_level,
    content_risk: snapshot.content_risk, media_risk: snapshot.media_risk,
    action_plan: snapshot.action_plan, warnings: snapshot.warnings || [],
    file_name: '실시간 화상통화 세션', duration: snapshot.audio_sec, elapsed_sec: snapshot.elapsed_sec,
    strategy: 'multiplicative_bonus', // 스트리밍 세션도 DEFAULT_STRATEGY(곱연산 가산)를 그대로 쓴다
    segments, top_categories: topCategoriesFromStreamSegments(segments),
    engines: snapshot.engines || {},
  };
}

/* ---------- 업로드 ---------- */
const dz = $('dropzone'), fileInput = $('fileInput'), consent = $('consent'), startBtn = $('startBtn');

dz.addEventListener('click', () => fileInput.click());
dz.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fileInput.click(); } });
['dragenter', 'dragover'].forEach(ev => dz.addEventListener(ev, e => { e.preventDefault(); dz.classList.add('over'); }));
['dragleave', 'drop'].forEach(ev => dz.addEventListener(ev, e => { e.preventDefault(); dz.classList.remove('over'); }));
dz.addEventListener('drop', e => { if (e.dataTransfer.files[0]) setFile(e.dataTransfer.files[0]); });
fileInput.addEventListener('change', () => { if (fileInput.files[0]) setFile(fileInput.files[0]); });
consent.addEventListener('change', updateStart);

function setFile(f) {
  selectedFile = f;
  dz.classList.add('has-file');
  dz.querySelector('.dz-title').textContent = f.name;
  dz.querySelector('.dz-sub').textContent = `${(f.size / 1024 / 1024).toFixed(1)} MB · 선택됨`;
  $('uploadErr').hidden = true;
  updateStart();
}
function updateStart() { startBtn.disabled = !(selectedFile && consent.checked); }

startBtn.addEventListener('click', async () => {
  if (!selectedFile) return;
  startBtn.disabled = true;
  const fd = new FormData();
  fd.append('file', selectedFile);
  try {
    const r = await fetch('/api/analyze', { method: 'POST', body: fd });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || '업로드에 실패했습니다');
    $('progressFile').textContent = d.file_name;
    show('progress');
    watch(d.job_id);
  } catch (e) {
    $('uploadErr').textContent = e.message;
    $('uploadErr').hidden = false;
    startBtn.disabled = false;
  }
});

/* ---------- 진행률 ---------- */
const STAGE_ORDER = ['stt', 'audio', 'video', 'content', 'scoring'];

function markStages(stage) {
  const i = STAGE_ORDER.indexOf(stage);
  document.querySelectorAll('.stages li').forEach(li => {
    const j = STAGE_ORDER.indexOf(li.dataset.stage);
    li.classList.toggle('active', j === i);
    li.classList.toggle('done', i >= 0 && j < i);
  });
}

function watch(jobId) {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(`${proto}://${location.host}/ws/jobs/${jobId}`);
  let closedOk = false;

  ws.onmessage = ev => {
    const m = JSON.parse(ev.data);
    if (m.type === 'progress') {
      updateProgressDonut(m.ratio);
      $('progressMsg').textContent = m.message;
      markStages(m.stage);
    } else if (m.type === 'done') {
      closedOk = true;
      document.querySelectorAll('.stages li').forEach(li => li.classList.add('done'));
      render(m.report);
      show('result');
      loadHomeStats();
    } else if (m.type === 'error') {
      closedOk = true;
      $('progressMsg').textContent = `분석 실패: ${m.message}`;
    }
  };

  // 웹소켓이 막히는 환경(사내 프록시 등)을 대비한 폴링 폴백
  ws.onerror = () => { if (!closedOk) poll(jobId); };
  ws.onclose = () => { if (!closedOk) poll(jobId); };
}

async function poll(jobId) {
  const timer = setInterval(async () => {
    try {
      const r = await fetch(`/api/jobs/${jobId}`);
      const d = await r.json();
      updateProgressDonut(d.ratio);
      $('progressMsg').textContent = d.message;
      markStages(d.stage);
      if (d.status === 'done') {
        clearInterval(timer);
        const rr = await fetch(`/api/results/${jobId}`);
        const dd = await rr.json();
        render(dd.report);
        show('result');
        loadHomeStats();
      } else if (d.status === 'error') {
        clearInterval(timer);
        $('progressMsg').textContent = `분석 실패: ${d.error}`;
      }
    } catch { clearInterval(timer); }
  }, 800);
}

/* ---------- 결과 렌더 ---------- */
function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
const fmtTime = s => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;

/* 콘텐츠 위험도 도넛 + 미디어 위험도 게이지 — 스티치 아이템1 목업 그대로 재현.
   담당: 홍수지. 값에 따라 색은 LEVEL_COLOR 신호등을 따른다. */
function levelOf(v) { return v >= 55 ? '높음' : v >= 30 ? '중간' : '낮음'; }

function svgDonut(value, trackColor, size = 116) {
  const v = Math.max(0, Math.min(100, value || 0));
  const color = LEVEL_COLOR[levelOf(v)];
  const r = size / 2 - 9, c = 2 * Math.PI * r, dash = (v / 100) * c;
  return `<svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" role="img" aria-label="콘텐츠 위험도 ${Math.round(v)}점">
    <circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="#f0e3c8" stroke-width="9"/>
    <circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="${color}" stroke-width="9"
      stroke-linecap="round" stroke-dasharray="${dash.toFixed(1)} ${c.toFixed(1)}"
      transform="rotate(-90 ${size / 2} ${size / 2})"/>
    <text x="${size / 2}" y="${size / 2 - 1}" text-anchor="middle" font-size="22" font-weight="800" fill="${color}">${Math.round(v)}</text>
    <text x="${size / 2}" y="${size / 2 + 16}" text-anchor="middle" font-size="10" fill="#a79c87">/100</text>
  </svg>`;
}

function svgGauge(value, size = 150) {
  const v = Math.max(0, Math.min(100, value || 0));
  const color = LEVEL_COLOR[levelOf(v)];
  const cx = size / 2, cy = size * 0.6, r = size * 0.4;
  const rad = ((180 - (v / 100) * 180) * Math.PI) / 180;
  const nx = cx + (r - 14) * Math.cos(rad), ny = cy - (r - 14) * Math.sin(rad);
  const gid = 'gaugeGrad' + Math.random().toString(36).slice(2, 9);
  // 캔버스 높이는 **아래 텍스트까지 담을 만큼** 잡아야 한다.
  // 예전에는 size*0.72(=108px)였는데 값 텍스트가 y=cy+22(=112), "/100"이
  // y=cy+37(=127)이라 둘 다 밖으로 나갔다 — 실제로 값 아랫부분이 잘리고
  // "/100"은 아예 안 보였다. 마지막 텍스트 baseline + 글자 아래 여유(5px)까지 잡는다.
  const h = Math.round(cy + 37 + 5);
  return `<svg width="${size}" height="${h}" viewBox="0 0 ${size} ${h}" role="img" aria-label="미디어 위험도 ${Math.round(v)}점">
    <defs><linearGradient id="${gid}" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0%" stop-color="#2e9e5b"/><stop offset="50%" stop-color="#e2960f"/><stop offset="100%" stop-color="#e14b4b"/>
    </linearGradient></defs>
    <path d="M ${cx - r} ${cy} A ${r} ${r} 0 0 1 ${cx + r} ${cy}" fill="none" stroke="url(#${gid})" stroke-width="10" stroke-linecap="round"/>
    <line x1="${cx}" y1="${cy}" x2="${nx.toFixed(1)}" y2="${ny.toFixed(1)}" stroke="#1c2333" stroke-width="3" stroke-linecap="round"/>
    <circle cx="${cx}" cy="${cy}" r="5" fill="#1c2333"/>
    <text x="${cx}" y="${cy + 22}" text-anchor="middle" font-size="19" font-weight="800" fill="${color}">${Math.round(v)}</text>
    <text x="${cx}" y="${cy + 37}" text-anchor="middle" font-size="10" fill="#a79c87">/100</text>
  </svg>`;
}

function updateProgressDonut(ratio) {
  $('progressDonut').innerHTML = svgDonut(Math.round((ratio || 0) * 100), null, 130);
}

/* 감지된 주요 순간 — 스티치 패널4 왼쪽 카드("감지된 주요 순간" 리스트) 재현 */
function drawMoments(segs) {
  const risky = segs.filter(s => s.level !== '낮음')
    .sort((a, b) => b.fraud_risk_score - a.fraud_risk_score).slice(0, 5)
    .sort((a, b) => a.start - b.start);
  const el = $('moments');
  if (!risky.length) { el.innerHTML = '<li style="color:var(--dim)">감지된 위험 순간이 없습니다.</li>'; return; }
  el.innerHTML = risky.map(s => {
    const d = s.content_detail || {};
    const label = d.top_category_label || (s.media_risk >= 50 ? '음성·영상 위조 위험' : '위험 신호 감지');
    return `<li><time>${fmtTime(s.start)}</time><span class="lv-${s.level}">${esc(label)}</span></li>`;
  }).join('');
}

/* 위험도 → 색상 (스티치 목업 실측: 낮음 초록/중간 황토/높음 빨강) */
const LEVEL_COLOR = { '높음': '#e14b4b', '중간': '#e2960f', '낮음': '#2e9e5b' };
const LEVEL_BADGE = { '높음': '높은 위험', '중간': '중간 위험', '낮음': '낮은 위험' };

function render(rep) {
  const lv = rep.overall_level;
  $('scoreValue').textContent = rep.overall_score.toFixed(0);
  $('scoreValue').className = `lv-${lv}`;
  $('verdictBadge').textContent = LEVEL_BADGE[lv] || lv;
  $('verdictBadge').className = `verdict-badge lv-${lv}`;
  $('contentDonut').innerHTML = svgDonut(rep.content_risk, '#2e9e5b');
  $('mediaGauge').innerHTML = svgGauge(rep.media_risk);
  $('verdictHeadline').textContent = rep.action_plan.headline;
  $('verdictHeadline').className = `verdict-headline lv-${lv}`;
  // 기획서 설계 원칙: 단정하지 않고 확률적으로 표현해 과신을 막는다
  $('verdictProb').textContent =
    `사기 위험 가능성 약 ${rep.overall_score.toFixed(0)}% · 위험도 ${lv} — 확정 판정이 아닙니다`;

  $('metaFile').textContent = rep.file_name;
  $('metaDuration').textContent = fmtTime(rep.duration);
  $('metaElapsed').textContent = `${rep.elapsed_sec}초`;
  $('metaStrategy').textContent =
    rep.strategy === 'multiplicative_bonus' ? '곱연산 가산 (DOCX)' : '임계 가산 (PDF)';

  const w = $('warnings');
  if (rep.warnings && rep.warnings.length) {
    w.hidden = false;
    w.innerHTML = rep.warnings.map(x => `<p>${esc(x)}</p>`).join('');
  } else { w.hidden = true; }

  drawChart(rep.segments);
  drawMoments(rep.segments);
  drawCategories(rep.top_categories);
  drawActions(rep.action_plan);
  drawTimeline(rep.segments);
  $('engines').innerHTML = Object.entries(rep.engines).map(([k, v]) => {
    const label = { stt: 'STT', content: '화법 분석', rag: '사례 검색',
                    audio: '음성 스푸핑', video: '딥페이크' }[k] || k;
    return `<div class="eng"><b>${label}</b>${esc(v)}</div>`;
  }).join('');
}

/* 이중 라인 그래프 + 통합 점수 막대.
   기획서 결과 대시보드: "시간축 위에 콘텐츠/미디어 위험도를 이중 라인으로 겹쳐 보여주고,
   위험 구간을 클릭하면 근거를 보여준다" */
function drawChart(segs) {
  if (!segs.length) { $('chart').innerHTML = '<p class="card-sub">아직 표시할 구간이 없습니다.</p>'; return; }
  const W = 900, H = 260, P = { t: 14, r: 14, b: 30, l: 34 };
  const iw = W - P.l - P.r, ih = H - P.t - P.b;
  const n = segs.length;
  const bw = iw / n;
  const y = v => P.t + ih - (v / 100) * ih;
  const cx = i => P.l + bw * (i + 0.5);

  const line = (key, color) => {
    const pts = segs.map((s, i) => `${cx(i).toFixed(1)},${y(s[key]).toFixed(1)}`).join(' ');
    return `<polyline points="${pts}" fill="none" stroke="${color}" stroke-width="2.5"
      stroke-linejoin="round" stroke-linecap="round"/>` +
      segs.map((s, i) => `<circle cx="${cx(i).toFixed(1)}" cy="${y(s[key]).toFixed(1)}" r="3.2"
        fill="${color}"/>`).join('');
  };

  const grid = [0, 25, 50, 75, 100].map(v =>
    `<line x1="${P.l}" y1="${y(v)}" x2="${W - P.r}" y2="${y(v)}" stroke="#c9bfa2" stroke-width="1" opacity=".4"/>
     <text x="${P.l - 7}" y="${y(v) + 4}" fill="#a79c87" font-size="10" text-anchor="end">${v}</text>`
  ).join('');

  // 위험 임계선(55) — 신호등 '높음' 기준 (decide_scoring.py 실측으로 확정된 값)
  const thresh = `<line x1="${P.l}" y1="${y(55)}" x2="${W - P.r}" y2="${y(55)}"
      stroke="#e14b4b" stroke-width="1" stroke-dasharray="4 4" opacity=".55"/>
    <text x="${W - P.r}" y="${y(55) - 5}" fill="#e14b4b" font-size="10" text-anchor="end">위험 55</text>`;

  const bars = segs.map((s, i) => {
    const h = (s.fraud_risk_score / 100) * ih;
    const col = LEVEL_COLOR[s.level];
    return `<g class="bar-hit" data-i="${i}">
      <rect class="hit" x="${P.l + bw * i}" y="${P.t}" width="${bw}" height="${ih}" fill="transparent"/>
      <rect x="${P.l + bw * i + bw * 0.24}" y="${P.t + ih - h}" width="${bw * 0.52}"
            height="${h}" rx="2" fill="${col}" opacity=".30"/>
      <title>${esc(fmtTime(s.start))} — 통합 ${s.fraud_risk_score} (${s.level})
콘텐츠 ${s.content_risk} / 미디어 ${s.media_risk}
${esc(s.transcript.slice(0, 60))}</title>
    </g>`;
  }).join('');

  const ticks = segs.map((s, i) =>
    (n <= 12 || i % Math.ceil(n / 10) === 0)
      ? `<text x="${cx(i)}" y="${H - 9}" fill="#a79c87" font-size="10" text-anchor="middle">${fmtTime(s.start)}</text>`
      : '').join('');

  $('chart').innerHTML =
    `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="구간별 위험도 추이 그래프">
      ${grid}${bars}${thresh}
      ${line('content_risk', '#2e9e5b')}
      ${line('media_risk', '#2f6fed')}
      ${ticks}
    </svg>`;

  $('chart').querySelectorAll('.bar-hit').forEach(g => {
    g.addEventListener('click', () => focusSegment(+g.dataset.i));
  });
}

function drawCategories(cats) {
  if (!cats || !cats.length) {
    $('categories').innerHTML = '<p class="card-sub">탐지된 사회공학 기법이 없습니다.</p>';
    return;
  }
  $('categories').innerHTML = cats.map(c =>
    `<div class="cat-row"><span>${esc(c.label)}</span>
      <span class="cat-bar"><i style="width:${c.score}%"></i></span>
      <span class="cat-val">${c.score.toFixed(0)}</span></div>`).join('');
}

function drawActions(plan) {
  $('actions').innerHTML = plan.actions.map(a => `<li>${esc(a)}</li>`).join('');
  $('actionLinks').innerHTML = (plan.links || []).map(l =>
    `<a href="${esc(l.url)}" ${l.url.startsWith('http') ? 'target="_blank" rel="noopener"' : ''}>${esc(l.label)}</a>`
  ).join('');
}

function drawTimeline(segs) {
  $('timeline').innerHTML = segs.map((s, i) => {
    const d = s.content_detail || {};
    const terms = Object.values(d.matched_terms || {}).flat().slice(0, 6);
    const rag = (s.rag_matches || []).map(m =>
      `<div class="rag"><b>유사 사례 ${(m.similarity * 100).toFixed(0)}%</b> — ${esc(m.title)}
        <div>${esc(m.summary)}</div>
        <div class="src">출처: ${esc(m.source)}</div></div>`).join('');
    return `<div class="seg lv-${s.level}" id="seg-${i}" data-i="${i}">
      <div class="seg-head">
        <span>${fmtTime(s.start)} – ${fmtTime(s.end)}</span>
        <span class="seg-scores">콘텐츠 ${s.content_risk} · 미디어 ${s.media_risk} ·
          <strong class="lv-${s.level}">통합 ${s.fraud_risk_score}</strong></span>
      </div>
      <p class="seg-text">${esc(s.transcript) || '<i style="color:var(--dim)">(발화 없음)</i>'}</p>
      <div class="seg-detail">
        ${d.top_category_label && s.content_risk > 0
          ? `<div><span class="tag">${esc(d.top_category_label)}</span>
             ${terms.length ? `<span class="evidence">근거: ${esc(terms.join(', '))}</span>` : ''}</div>` : ''}
        ${s.deepfake_score != null
          ? `<div>딥페이크 점수 ${s.deepfake_score} (분석 프레임 ${s.frames_analyzed}, 얼굴 검출 ${s.faces_detected})</div>` : ''}
        ${s.audio_spoof_score != null ? `<div>음성 합성 점수 ${s.audio_spoof_score}</div>` : ''}
        ${rag}
      </div>
    </div>`;
  }).join('');

  $('timeline').querySelectorAll('.seg').forEach(el => {
    el.addEventListener('click', () => el.classList.toggle('open'));
  });
}

function focusSegment(i) {
  const el = $(`seg-${i}`);
  if (!el) return;
  document.querySelectorAll('.seg').forEach(s => s.classList.remove('focus'));
  el.classList.add('focus');
  el.classList.add('open');
  el.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

/* ---------- 보호 이력 (기획서 6번 화면) ---------- */
function historyRowHtml(it) {
  const when = (it.analyzed_at || '').replace('T', ' ').slice(5, 16);
  return `<div class="hist lv-${it.overall_level}" data-id="${esc(it.job_id)}">
    <span class="hist-score lv-${it.overall_level}">${
      it.overall_score == null ? '-' : Math.round(it.overall_score)}</span>
    <span class="hist-name" title="${esc(it.file_name)}">${esc(it.file_name)}</span>
    <span class="hist-meta">${when} · ${fmtTime(it.duration || 0)} · ${it.n_segments}구간</span>
    <button class="hist-del" title="이 기록 삭제">×</button>
  </div>`;
}
function bindHistoryRows(container) {
  container.querySelectorAll('.hist').forEach(el => {
    el.addEventListener('click', async ev => {
      if (ev.target.classList.contains('hist-del')) {
        ev.stopPropagation();
        await fetch(`/api/history/${el.dataset.id}`, { method: 'DELETE' });
        loadHistory(); loadHomeStats();
        return;
      }
      const rr = await fetch(`/api/history/${el.dataset.id}`);
      if (!rr.ok) return;
      const dd = await rr.json();
      render(dd.report);
      show('result');
    });
  });
}

async function loadHistory() {
  try {
    const r = await fetch('/api/history?limit=50');
    const d = await r.json();
    const list = $('historyList');
    if (!d.items || !d.items.length) {
      list.innerHTML = '<p class="hist-empty">아직 분석한 통화가 없습니다.</p>';
      $('historyTrend').innerHTML = '';
      return;
    }
    drawTrend(d.items);
    list.innerHTML = d.items.map(historyRowHtml).join('');
    bindHistoryRows(list);
  } catch { /* 히스토리 실패가 다른 기능을 막지 않는다 */ }
}

/* 위험도 추이 — 기획서 메뉴 구조도의 "위험도 추이 그래프" */
function drawTrend(items) {
  const pts = items.slice().reverse();
  if (pts.length < 2) { $('historyTrend').innerHTML = ''; return; }
  const W = 900, H = 90, P = { t: 10, r: 10, b: 10, l: 26 };
  const iw = W - P.l - P.r, ih = H - P.t - P.b;
  const x = i => P.l + (pts.length === 1 ? iw / 2 : (iw * i) / (pts.length - 1));
  const y = v => P.t + ih - (Math.max(0, Math.min(100, v || 0)) / 100) * ih;

  const line = pts.map((p, i) => `${x(i).toFixed(1)},${y(p.overall_score).toFixed(1)}`).join(' ');
  const dots = pts.map((p, i) => {
    const col = LEVEL_COLOR[p.overall_level] || '#a79c87';
    return `<circle cx="${x(i).toFixed(1)}" cy="${y(p.overall_score).toFixed(1)}" r="4" fill="${col}">
      <title>${esc(p.file_name)} — ${Math.round(p.overall_score || 0)} (${p.overall_level})</title></circle>`;
  }).join('');

  $('historyTrend').innerHTML =
    `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="분석 위험도 추이">
      <line x1="${P.l}" y1="${y(55)}" x2="${W - P.r}" y2="${y(55)}"
            stroke="#e14b4b" stroke-width="1" stroke-dasharray="4 4" opacity=".4"/>
      <line x1="${P.l}" y1="${y(0)}" x2="${W - P.r}" y2="${y(0)}" stroke="#c9bfa2" opacity=".5"/>
      <text x="${P.l - 6}" y="${y(100) + 4}" fill="#a79c87" font-size="9" text-anchor="end">100</text>
      <text x="${P.l - 6}" y="${y(0) + 4}" fill="#a79c87" font-size="9" text-anchor="end">0</text>
      <polyline points="${line}" fill="none" stroke="#2f6fed" stroke-width="2"
                stroke-linejoin="round"/>
      ${dots}
    </svg>`;
}

$('againBtn').addEventListener('click', () => {
  selectedFile = null;
  fileInput.value = '';
  dz.classList.remove('has-file');
  dz.querySelector('.dz-title').textContent = '통화 녹음 또는 화상통화 파일을 여기에 놓으세요';
  dz.querySelector('.dz-sub').textContent =
    '클릭해서 선택할 수도 있습니다 · wav mp3 m4a flac mp4 mov webm · 최대 200MB';
  consent.checked = false;
  updateStart();
  $('progressDonut').innerHTML = '';
  loadHomeStats();
  show('home');
});

/* ---------- 설정 (보호 이력 화면) ---------- */
const RETENTION_KEY = 'dualguard_retention', THEME_KEY = 'dualguard_theme';

(function initSettings() {
  const savedRetention = localStorage.getItem(RETENTION_KEY);
  if (savedRetention) $('retentionPolicy').value = savedRetention;
  $('retentionPolicy').addEventListener('change', e => localStorage.setItem(RETENTION_KEY, e.target.value));

  const savedTheme = localStorage.getItem(THEME_KEY) || 'light';
  applyTheme(savedTheme);
  $('darkModeToggle').addEventListener('change', e => applyTheme(e.target.checked ? 'dark' : 'light'));
})();

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  $('darkModeToggle').checked = theme === 'dark';
  localStorage.setItem(THEME_KEY, theme);
}

loadHealth();
