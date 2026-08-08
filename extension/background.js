/* 서비스 워커 — 상태 조율
   담당: 이상원

   Manifest V3의 3계층 구조에서 이 파일이 하는 일:
     팝업(사용자 제스처) → [여기] getMediaStreamId 발급 + 오프스크린 문서 관리
     → 오프스크린 문서(실제 MediaStream 처리) → 콘텐츠 스크립트(경고 오버레이)

   왜 이렇게 나뉘어 있나:
     Chrome은 보안상 서비스 워커에 DOM을 주지 않는다. MediaStream을 실제로 다루려면
     DOM이 있는 컨텍스트가 필요한데, 팝업은 닫히면 죽는다. 그래서 백그라운드에서
     살아 있는 오프스크린 문서가 필요하다. 이 3계층이 MV3에서 실시간 캡처를 하는
     표준 구조다. */

const STATE = { active: false, tabId: null, level: null, score: 0 };

async function ensureOffscreen() {
  const existing = await chrome.offscreen.hasDocument?.();
  if (existing) return;
  await chrome.offscreen.createDocument({
    url: 'offscreen.html',
    reasons: ['USER_MEDIA'],
    justification: '화상통화 탭의 오디오·영상을 분석용 청크로 캡처합니다.',
  });
}

async function startCapture(tabId) {
  // getMediaStreamId는 사용자 제스처(팝업 클릭) 컨텍스트에서 호출돼야 성공한다.
  const streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: tabId });
  await ensureOffscreen();
  await chrome.runtime.sendMessage({ target: 'offscreen', type: 'start', streamId });
  STATE.active = true;
  STATE.tabId = tabId;
  await pushOverlay(tabId, { type: 'status', active: true });
}

async function stopCapture() {
  try {
    await chrome.runtime.sendMessage({ target: 'offscreen', type: 'stop' });
  } catch { /* 오프스크린이 이미 없을 수 있다 */ }
  try { await chrome.offscreen.closeDocument(); } catch { }
  if (STATE.tabId) await pushOverlay(STATE.tabId, { type: 'status', active: false });
  STATE.active = false;
  STATE.tabId = null;
  STATE.level = null;
  STATE.score = 0;
}

async function pushOverlay(tabId, payload) {
  try {
    await chrome.tabs.sendMessage(tabId, payload);
  } catch {
    // 콘텐츠 스크립트가 아직 없으면 주입한다 (탭이 방금 열린 경우)
    try {
      await chrome.scripting.executeScript({ target: { tabId }, files: ['content.js'] });
      await chrome.tabs.sendMessage(tabId, payload);
    } catch { /* 지원하지 않는 페이지 */ }
  }
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  (async () => {
    if (msg.type === 'getState') {
      sendResponse(STATE);
    } else if (msg.type === 'start') {
      try {
        await startCapture(msg.tabId);
        sendResponse({ ok: true });
      } catch (e) {
        sendResponse({ ok: false, error: String(e.message || e) });
      }
    } else if (msg.type === 'stop') {
      await stopCapture();
      sendResponse({ ok: true });
    } else if (msg.type === 'result') {
      // 오프스크린이 백엔드 분석 결과를 올려보낸다 → 오버레이로 전달
      STATE.level = msg.level;
      STATE.score = msg.score;
      if (STATE.tabId) await pushOverlay(STATE.tabId, { type: 'risk', ...msg });
    }
  })();
  return true;   // 비동기 응답을 쓰므로 채널을 열어둔다
});

chrome.tabs.onRemoved.addListener(tabId => {
  if (STATE.tabId === tabId) stopCapture();
});
