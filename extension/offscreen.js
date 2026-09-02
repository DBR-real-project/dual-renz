/* 오프스크린 문서 — 실제 미디어 캡처와 청크 전송
   담당: 이상원

   기획서 [Phase 2-1]: "오프스크린 문서(chrome.tabCapture.getMediaStreamId()로 탭
   오디오·영상 캡처, 3~5초 단위 청크 전송) → 백엔드(실시간 분석)"

   ## 세션 API로 교체 (2026-08-09)

   예전에는 청크마다 `POST /api/analyze`(파일 단건 분석)를 호출하고 결과를 폴링했다.
   그 방식은 청크마다 Whisper/AASIST를 **다시 로드**해서 5초 청크 하나에 30초 넘게
   걸렸다. 실시간이라 부를 수 없었다.

   지금은 백엔드에 세션 API가 있다:
     POST   /api/sessions              통화 시작 시 한 번. 모델을 미리 올린다
     POST   /api/sessions/{id}/chunk   청크마다. 갱신된 위험도를 바로 응답으로 받는다
     DELETE /api/sessions/{id}         통화 종료 시. 모델을 내린다

   폴링이 사라져서 왕복이 한 번으로 줄었고, 실측으로 오디오 78초를 26초에 처리한다
   (약 3배속 — 실시간을 따라간다).

   ## 오디오만 캡처하는 이유

   스트리밍 경로는 음성 스푸핑 + 화법만 본다. 영상 딥페이크는 프레임마다 얼굴을
   검출하고 Xception을 돌려야 해서 실시간 예산에 안 맞는다(영상은 통화 종료 후
   파일 분석 경로에서 본다). 영상까지 캡처하면 청크가 10배 이상 커져 업로드만
   느려지므로 오디오만 받는다.

   ## 밀림 방지

   청크는 5초마다 계속 생긴다. 어떤 이유로 서버 응답이 5초를 넘기면 요청이
   쌓여서 점점 뒤처진다. **처리 중이면 새 청크를 버린다**(큐에 쌓지 않는다).
   실시간 경고에서는 오래된 결과를 늦게 보여주는 것보다 최신 상태를 보는 게 낫다. */

const CHUNK_MS = 5000;         // 기획서의 3~5초 청크
const API = 'http://127.0.0.1:8000';
const MIN_CHUNK_BYTES = 2000;  // 무음 구간에서 나오는 빈 청크는 보내지 않는다

let stream = null;
let recorder = null;
let stopping = false;
let sessionId = null;
let busy = false;
let dropped = 0;
// 첫 청크(EBML 헤더 포함)를 보냈는지. 크기 필터를 헤더 뒤부터만 적용하려고 쓴다.
let headerSent = false;
// AudioContext를 모듈 스코프에 둔다. 지역변수로 두면 stop()에서 닫을 수가 없는데,
// **닫지 않으면 탭 캡처가 계속 살아있는 것으로 남는다.** 트랙을 stop()해도
// AudioContext가 그 스트림을 물고 있으면 크롬이 탭을 '캡처 중'으로 유지해서,
// 다시 시작할 때 getUserMedia가 "Cannot capture a tab with an active stream"으로
// 거부한다(실측). 탭을 새로고침해야만 풀렸다.
let audioCtx = null;

chrome.runtime.onMessage.addListener((msg) => {
  if (msg.target !== 'offscreen') return;
  // start는 async라 실패하면 unhandled rejection이 된다. 그러면 사용자에게는
  // 영어 원문 에러만 보이고 정리도 안 된 채 상태만 어긋난다(실제로 그랬다).
  if (msg.type === 'start') start(msg.streamId).catch(async (e) => {
    await stop();
    notifyError('탭 오디오 캡처를 시작하지 못했습니다. 통화 탭을 새로고침한 뒤 다시 시도하세요.', String(e));
  });
  if (msg.type === 'stop') stop();
});

async function start(streamId) {
  // 이미 캡처 중이면 먼저 정리한다. 안 그러면 getUserMedia가 거부한다.
  // 앞선 시도가 중간에 실패해 스트림만 남는 경우가 실제로 생긴다
  // (예: 서버가 409를 돌려줘 세션을 못 연 뒤 다시 누르는 경우).
  if (stream || audioCtx) await stop();

  stopping = false;
  dropped = 0;

  stream = await navigator.mediaDevices.getUserMedia({
    audio: { mandatory: { chromeMediaSource: 'tab', chromeMediaSourceId: streamId } },
  });

  // 탭 캡처는 탭 소리를 가로채므로, 사용자가 통화를 계속 들을 수 있게 되돌려준다.
  // 이 처리를 빼면 캡처 시작과 동시에 통화 소리가 끊긴다.
  audioCtx = new AudioContext();
  audioCtx.createMediaStreamSource(stream).connect(audioCtx.destination);

  // 세션을 먼저 연다. 서버가 모델을 올리는 동안(수십 초) 기다리므로,
  // 녹음은 응답을 받은 뒤에 시작해야 첫 청크가 버려지지 않는다.
  try {
    const r = await fetch(`${API}/api/sessions`, { method: 'POST' });
    if (!r.ok) {
      const detail = await r.text();
      notifyError(r.status === 409
        ? '다른 분석이 진행 중입니다. 끝난 뒤 다시 시작하세요.'
        : `백엔드가 세션을 열지 못했습니다 (HTTP ${r.status}).`, detail);
      stop();
      return;
    }
    sessionId = (await r.json()).session_id;
  } catch (e) {
    notifyError('백엔드에 연결할 수 없습니다. 서버가 켜져 있는지 확인하세요.', String(e));
    stop();
    return;
  }

  const mime = ['audio/webm;codecs=opus', 'audio/webm']
    .find(t => MediaRecorder.isTypeSupported(t));
  recorder = new MediaRecorder(stream, { mimeType: mime });

  headerSent = false;
  recorder.ondataavailable = (e) => {
    if (stopping || !e.data) return;
    // **첫 청크는 크기와 상관없이 반드시 보낸다.**
    // MediaRecorder는 EBML(webm) 헤더를 첫 청크에만 싣는다. 통화 초반이 조용하면
    // 그 첫 청크가 MIN_CHUNK_BYTES 미만이라 여기서 버려지는데, 그러면 헤더까지
    // 같이 사라져서 **이후 모든 청크가 서버에서 디코딩 불가(400)** 가 된다.
    // 실측: Meet 통화 시작 직후 전 청크가 400으로 거부됐다.
    if (headerSent && e.data.size < MIN_CHUNK_BYTES) return;
    if (busy) { dropped += 1; return; }   // 밀림 방지 (위 주석 참고)
    headerSent = true;
    sendChunk(e.data);
  };
  recorder.start(CHUNK_MS);
}

async function stop() {
  stopping = true;
  try { recorder && recorder.state !== 'inactive' && recorder.stop(); } catch { }
  try { stream && stream.getTracks().forEach(t => t.stop()); } catch { }
  // 트랙만 멈추면 부족하다. AudioContext가 스트림을 물고 있는 한 탭이
  // '캡처 중'으로 남아 다음 시작이 막힌다(위 audioCtx 선언부 주석 참고).
  try { audioCtx && await audioCtx.close(); } catch { }
  recorder = null;
  stream = null;
  audioCtx = null;

  // 세션을 반드시 닫아야 서버가 모델을 내리고 다음 분석이 가능해진다.
  if (sessionId) {
    try {
      await fetch(`${API}/api/sessions/${sessionId}`, { method: 'DELETE' });
    } catch { /* 서버가 이미 죽었을 수 있다 */ }
    sessionId = null;
  }
  busy = false;
}

async function sendChunk(blob) {
  busy = true;
  const fd = new FormData();
  fd.append('file', blob, 'chunk.webm');
  try {
    const r = await fetch(`${API}/api/sessions/${sessionId}/chunk`,
      { method: 'POST', body: fd });
    if (!r.ok) {
      // 예전에는 여기서 조용히 return했다. 그래서 서버가 청크를 전부 400으로
      // 거부해도 **화면에는 아무 표시가 없고** 위험도만 안 움직였다 — 원인을
      // 찾는 데 한참 걸렸다. 실패는 사용자에게 올린다.
      const detail = await r.text().catch(() => '');
      notifyError(r.status === 404
        ? '분석 세션이 끊겼습니다. 분석을 다시 시작하세요.'
        : `서버가 오디오 조각을 처리하지 못했습니다 (HTTP ${r.status}).`, detail);
      return;
    }
    const res = await r.json();
    chrome.runtime.sendMessage({
      type: 'result',
      level: res.overall_level,
      score: res.overall_score,
      headline: res.action_plan.headline,
      actions: res.action_plan.actions,
      contentRisk: res.content_risk,
      mediaRisk: res.media_risk,
      elapsed: res.audio_sec,
      dropped,
      transcript: (res.new_segments || []).map(s => s.transcript).join(' ').slice(0, 200),
    });
  } catch { /* 백엔드가 꺼져 있으면 조용히 무시 — 통화를 방해하지 않는다 */ }
  finally { busy = false; }
}

function notifyError(message, detail) {
  chrome.runtime.sendMessage({ type: 'error', message, detail: String(detail || '').slice(0, 300) });
}
