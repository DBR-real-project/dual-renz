/* 팝업 — 사용자 시작 트리거
   담당: 이상원

   tabCapture 권한은 "사용자가 보고 있는 화면에서 제스처로" 요청해야만 성공한다.
   그래서 캡처 시작은 반드시 팝업 클릭에서 출발한다. */

const $ = id => document.getElementById(id);

function paint(state) {
  const on = !!state.active;
  $('dot').classList.toggle('on', on);
  $('stateText').textContent = on ? '분석 중' : '분석 중지됨';
  $('toggle').textContent = on ? '분석 중지' : '분석 시작';
  $('toggle').classList.toggle('on', on);
  if (on && state.level) {
    $('score').textContent = `${Math.round(state.score)} ${state.level}`;
    $('score').className = `score lv-${state.level}`;
  } else {
    $('score').textContent = '';
  }
}

async function refresh() {
  const state = await chrome.runtime.sendMessage({ type: 'getState' });
  paint(state || {});
  return state || {};
}

$('toggle').addEventListener('click', async () => {
  $('err').hidden = true;
  const state = await refresh();
  if (state.active) {
    await chrome.runtime.sendMessage({ type: 'stop' });
  } else {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab) return;
    const res = await chrome.runtime.sendMessage({ type: 'start', tabId: tab.id });
    if (!res?.ok) {
      $('err').textContent = res?.error || '캡처를 시작하지 못했습니다';
      $('err').hidden = false;
    }
  }
  refresh();
});

refresh();
setInterval(refresh, 1500);
