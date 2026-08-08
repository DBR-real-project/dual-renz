/* 오프스크린 문서 — 실제 미디어 캡처와 청크 전송
   담당: 이상원

   기획서 [Phase 2-1]: "오프스크린 문서(chrome.tabCapture.getMediaStreamId()로 탭
   오디오·영상 캡처, 3~5초 단위 청크 전송) → 백엔드(실시간 분석)"

   ⚠ 현재 상태: 백엔드가 아직 **파일 업로드 단건 분석**만 지원한다(POST /api/analyze).
   여기서는 캡처한 청크를 그 엔드포인트로 보내 동작을 확인하는 수준이다.
   실시간 스트리밍(웹소켓으로 청크를 밀어 넣고 부분 결과를 받는 구조)은
   백엔드에 청크 누적/세션 API가 생긴 뒤에 붙여야 한다. */

const CHUNK_MS = 5000;         // 기획서의 3~5초 청크
const API = 'http://127.0.0.1:8000';

let stream = null;
let recorder = null;
let stopping = false;

chrome.runtime.onMessage.addListener((msg) => {
  if (msg.target !== 'offscreen') return;
  if (msg.type === 'start') start(msg.streamId);
  if (msg.type === 'stop') stop();
});

async function start(streamId) {
  stopping = false;
  stream = await navigator.mediaDevices.getUserMedia({
    audio: { mandatory: { chromeMediaSource: 'tab', chromeMediaSourceId: streamId } },
    video: { mandatory: { chromeMediaSource: 'tab', chromeMediaSourceId: streamId } },
  });

  // 탭 캡처는 탭 소리를 가로채므로, 사용자가 통화를 계속 들을 수 있게 되돌려준다.
  // 이 처리를 빼면 캡처 시작과 동시에 통화 소리가 끊긴다.
  const ctx = new AudioContext();
  ctx.createMediaStreamSource(stream).connect(ctx.destination);

  const mime = ['video/webm;codecs=vp8,opus', 'video/webm']
    .find(t => MediaRecorder.isTypeSupported(t));
  recorder = new MediaRecorder(stream, { mimeType: mime });

  recorder.ondataavailable = async (e) => {
    if (stopping || !e.data || e.data.size < 20000) return;   // 너무 짧은 청크는 버린다
    await analyzeChunk(e.data);
  };
  recorder.start(CHUNK_MS);
}

function stop() {
  stopping = true;
  try { recorder && recorder.state !== 'inactive' && recorder.stop(); } catch { }
  try { stream && stream.getTracks().forEach(t => t.stop()); } catch { }
  recorder = null;
  stream = null;
}

async function analyzeChunk(blob) {
  const fd = new FormData();
  fd.append('file', blob, `chunk_${Date.now()}.webm`);
  try {
    const r = await fetch(`${API}/api/analyze`, { method: 'POST', body: fd });
    if (!r.ok) return;
    const { job_id } = await r.json();
    const report = await waitForResult(job_id);
    if (!report) return;
    chrome.runtime.sendMessage({
      type: 'result',
      level: report.overall_level,
      score: report.overall_score,
      headline: report.action_plan.headline,
      actions: report.action_plan.actions,
      topCategories: report.top_categories,
      transcript: (report.segments || []).map(s => s.transcript).join(' ').slice(0, 200),
    });
  } catch { /* 백엔드가 꺼져 있으면 조용히 무시 — 통화를 방해하지 않는다 */ }
}

async function waitForResult(jobId, timeoutMs = 60000) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    const r = await fetch(`${API}/api/results/${jobId}`);
    if (r.status === 200) return (await r.json()).report;
    if (r.status >= 400 && r.status !== 202) return null;
    await new Promise(res => setTimeout(res, 700));
  }
  return null;
}
