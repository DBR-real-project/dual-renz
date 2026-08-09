/* 콘텐츠 스크립트 — 화상통화 페이지 위 경고 오버레이
   담당: 이상원

   기획서 [Phase 2-2]: "숫자 점수 대신 초록/노랑/빨강 신호등 색과 한 줄 경고 문구로
   직관적으로 위험을 전달한다"

   설계 원칙:
     - 통화를 가리지 않는다. 우상단 고정, 클릭 통과(pointer-events)는 배너 밖만.
     - 낮음 등급에서는 아무것도 띄우지 않는다. 상시 배지는 금방 무시당한다.
     - 색만으로 구분하지 않고 아이콘과 텍스트를 함께 둔다. */

(() => {
  if (window.__dualguardInjected) return;
  window.__dualguardInjected = true;

  const ID = 'dualguard-overlay';
  let el = null;

  function ensure() {
    if (el && document.body.contains(el)) return el;
    el = document.createElement('div');
    el.id = ID;
    el.setAttribute('role', 'status');
    el.setAttribute('aria-live', 'polite');
    document.documentElement.appendChild(el);
    return el;
  }

  function hide() {
    if (el) el.remove();
    el = null;
  }

  function renderRisk(m) {
    if (m.level === '낮음') { hide(); return; }
    const node = ensure();
    const icon = m.level === '높음' ? '!!' : '!';
    const cats = (m.topCategories || []).slice(0, 3)
      .map(c => `<span class="dg-tag">${c.label}</span>`).join('');
    node.className = `dg lv-${m.level}`;
    node.innerHTML = `
      <div class="dg-head">
        <span class="dg-icon">${icon}</span>
        <span class="dg-title">${m.headline || '주의가 필요합니다'}</span>
        <button class="dg-x" title="닫기">×</button>
      </div>
      <div class="dg-score">위험 가능성 약 ${Math.round(m.score)}% · ${m.level}</div>
      ${cats ? `<div class="dg-tags">${cats}</div>` : ''}
      <ul class="dg-actions">
        ${(m.actions || []).slice(0, 2).map(a => `<li>${a}</li>`).join('')}
      </ul>
      <div class="dg-foot">듀얼가드 · 확률적 참고 정보이며 확정 판정이 아닙니다</div>`;
    node.querySelector('.dg-x').addEventListener('click', hide);
  }

  function renderStatus(active) {
    if (!active) { hide(); return; }
    const node = ensure();
    node.className = 'dg lv-대기';
    node.innerHTML = `
      <div class="dg-head">
        <span class="dg-icon">◎</span>
        <span class="dg-title">듀얼가드 분석 중</span>
        <button class="dg-x" title="닫기">×</button>
      </div>
      <div class="dg-score">통화 내용과 음성·영상을 검사하고 있습니다</div>`;
    node.querySelector('.dg-x').addEventListener('click', hide);
  }

  function renderError(m) {
    // 조용히 실패하면 사용자는 보호받고 있다고 착각한 채 통화를 계속한다.
    // 분석이 안 되고 있다는 사실 자체가 알려야 할 정보다.
    const node = ensure();
    node.className = 'dg lv-오류';
    node.innerHTML = `
      <div class="dg-head">
        <span class="dg-icon">×</span>
        <span class="dg-title">분석을 시작하지 못했습니다</span>
        <button class="dg-x" title="닫기">×</button>
      </div>
      <div class="dg-score">${m.message || '백엔드에 연결할 수 없습니다.'}</div>
      <div class="dg-foot">듀얼가드 · 이 통화는 검사되고 있지 않습니다</div>`;
    node.querySelector('.dg-x').addEventListener('click', hide);
  }

  chrome.runtime.onMessage.addListener((msg) => {
    if (msg.type === 'risk') renderRisk(msg);
    else if (msg.type === 'status') renderStatus(msg.active);
    else if (msg.type === 'error') renderError(msg);
  });
})();
