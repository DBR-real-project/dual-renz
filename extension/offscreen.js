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

chrome.runtime.onMessage.addListener((msg) => {
  if (msg.target !== 'offscreen') return;
  if (msg.type === 'start') start(msg.streamId);
  if (msg.type === 'stop') stop();
});

async function start(streamId) {
  stopping = false;
  dropped = 0;

  stream = await navigator.mediaDevices.getUserMedia({
    audio: { mandatory: { chromeMediaSource: 'tab', chromeMediaSourceId: streamId } },
  });

  // 탭 캡처는 탭 소리를 가로채므로, 사용자가 통화를 계속 들을 수 있게 되돌려준다.
  // 이 처리를 빼면 캡처 시작과 동시에 통화 소리가 끊긴다.
  const ctx = new AudioContext();
  ctx.createMediaStreamSource(stream).connect(ctx.destination);

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

  recorder.ondataavailable = (e) => {
    if (stopping || !e.data || e.data.size < MIN_CHUNK_BYTES) return;
    if (busy) { dropped += 1; return; }   // 밀림 방지 (위 주석 참고)
    sendChunk(e.data);
  };
  recorder.start(CHUNK_MS);
}

async function stop() {
  stopping = true;
  try { recorder && recorder.state !== 'inactive' && recorder.stop(); } catch { }
  try { stream && stream.getTracks().forEach(t => t.stop()); } catch { }
  recorder = null;
  stream = null;

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
    if (!r.ok) return;
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
