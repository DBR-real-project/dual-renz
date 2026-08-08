/* 듀얼가드 대시보드 로직
   담당: 이상원

   외부 라이브러리를 쓰지 않는다. 차트도 SVG를 직접 그린다.
   이유: 해커톤 시연 환경의 네트워크를 믿을 수 없다. CDN이 막히면 화면이 통째로 죽는다.
   d3/chart.js를 번들하려면 빌드 단계가 생기는데, 그것도 시연 직전에 깨질 수 있는 요소다. */

const $ = (id) => document.getElementById(id);
const screens = { upload: $('screenUpload'), progress: $('screenProgress'), result: $('screenResult') };
let selectedFile = null;

function show(name) {
  Object.values(screens).forEach(s => s.classList.remove('active'));
  screens[name].classList.add('active');
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

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
      $('progressBar').style.width = `${Math.round(m.ratio * 100)}%`;
      $('progressMsg').textContent = m.message;
      markStages(m.stage);
    } else if (m.type === 'done') {
      closedOk = true;
      document.querySelectorAll('.stages li').forEach(li => li.classList.add('done'));
      render(m.report);
      show('result');
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
      $('progressBar').style.width = `${Math.round(d.ratio * 100)}%`;
      $('progressMsg').textContent = d.message;
      markStages(d.stage);
      if (d.status === 'done') {
        clearInterval(timer);
        const rr = await fetch(`/api/results/${jobId}`);
        const dd = await rr.json();
        render(dd.report);
        show('result');
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

function render(rep) {
  const lv = rep.overall_level;
  $('signal').dataset.level = lv;
  $('signal').innerHTML = ['높음', '중간', '낮음'].map(l =>
    `<span style="${l === lv ? `background:var(--${{ '높음': 'red', '중간': 'yellow', '낮음': 'green' }[l]});color:var(--${{ '높음': 'red', '중간': 'yellow', '낮음': 'green' }[l]})` : ''}"></span>`
  ).join('');
  $('scoreValue').textContent = rep.overall_score.toFixed(0);
  $('scoreValue').className = `lv-${lv}`;
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
  if (!segs.length) return;
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
    `<line x1="${P.l}" y1="${y(v)}" x2="${W - P.r}" y2="${y(v)}" stroke="#2a323c" stroke-width="1"
       ${v === 70 ? '' : ''}/>
     <text x="${P.l - 7}" y="${y(v) + 4}" fill="#6b7887" font-size="10" text-anchor="end">${v}</text>`
  ).join('');

  // 위험 임계선(70) — 신호등 '높음' 기준
  const thresh = `<line x1="${P.l}" y1="${y(70)}" x2="${W - P.r}" y2="${y(70)}"
      stroke="#ff5c5c" stroke-width="1" stroke-dasharray="4 4" opacity=".55"/>
    <text x="${W - P.r}" y="${y(70) - 5}" fill="#ff5c5c" font-size="10" text-anchor="end">위험 70</text>`;

  const bars = segs.map((s, i) => {
    const h = (s.fraud_risk_score / 100) * ih;
    const col = { '높음': '#ff5c5c', '중간': '#f5b942', '낮음': '#2ecc71' }[s.level];
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
      ? `<text x="${cx(i)}" y="${H - 9}" fill="#6b7887" font-size="10" text-anchor="middle">${fmtTime(s.start)}</text>`
      : '').join('');

  $('chart').innerHTML =
    `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="구간별 위험도 추이 그래프">
      ${grid}${bars}${thresh}
      ${line('content_risk', '#5aa9ff')}
      ${line('media_risk', '#c084fc')}
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

$('againBtn').addEventListener('click', () => {
  selectedFile = null;
  fileInput.value = '';
  dz.classList.remove('has-file');
  dz.querySelector('.dz-title').textContent = '통화 녹음 또는 화상통화 파일을 여기에 놓으세요';
  dz.querySelector('.dz-sub').textContent =
    '클릭해서 선택할 수도 있습니다 · wav mp3 m4a flac mp4 mov webm · 최대 200MB';
  consent.checked = false;
  updateStart();
  $('progressBar').style.width = '0';
  show('upload');
});

loadHealth();
