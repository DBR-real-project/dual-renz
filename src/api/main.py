"""
DualGuard 백엔드 API (FastAPI)
담당: 이상원

기획서 [Back-end & Infrastructure]:
  *"FastAPI — 실시간 스트리밍 처리에 유리한 비동기 Python 서버, STT·LLM·AASIST·
   딥페이크 모델 추론 오케스트레이션"*, *"WebSocket — 실시간 청크 송수신"*

엔드포인트:
  POST   /api/analyze              파일 업로드 -> job_id 반환 (분석은 백그라운드)
  GET    /api/jobs/{id}            진행률/상태 조회 (웹소켓을 못 쓰는 환경용 폴백)
  GET    /api/results/{id}         분석 리포트 (완료 후)
  WS     /ws/jobs/{id}             진행률 실시간 스트림
  GET    /api/history[/{id}]       지난 분석 목록·재조회 (DELETE로 삭제)
  POST   /api/sessions             실시간 세션 시작 (모델 상주)
  POST   /api/sessions/{id}/chunk  오디오 청크 투입 -> 갱신된 위험도
  GET    /api/sessions/{id}        현재까지의 구간별 결과
  DELETE /api/sessions/{id}        세션 종료 + 모델 해제
  GET    /api/health               엔진별 준비 상태
  GET    /                         결과 대시보드 (정적 파일)

동시성 설계:
  분석은 CPU를 오래 잡는 동기 작업(torch 추론)이라 이벤트 루프에서 직접 돌리면
  서버 전체가 멈춘다. ThreadPoolExecutor에 넘기고, 진행률은 스레드에서
  큐로 밀어 웹소켓이 읽어간다.

  워커는 1개다. 모델들이 메모리를 많이 쓰고, 동시에 여러 건을 돌리면
  AASIST 추론이 네이티브 크래시로 프로세스를 죽인다(pipeline.AASIST_BATCH 주석 참고).
  해커톤 데모는 동시 요청이 없으므로 안전을 택했다.
"""

import asyncio
import json
import sys
import tempfile
import time
import uuid
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

SRC_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = SRC_ROOT.parent
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from orchestration.pipeline import analyze  # noqa: E402
from scoring.fraud_risk_score import DEFAULT_STRATEGY  # noqa: E402

STATIC_DIR = PROJECT_ROOT / "web"
UPLOAD_DIR = Path(tempfile.gettempdir()) / "dualguard_uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# 분석 히스토리(결과 JSON만). 원본 미디어는 여기 저장하지 않는다.
# 전사 텍스트가 들어가므로 민감 정보로 다뤄야 한다 — .gitignore 대상.
HISTORY_DIR = PROJECT_ROOT / "data" / "reports"

# 업로드 상한. 기획서 MVP는 통화 파일 한 건 분석이므로 넉넉히 200MB.
MAX_UPLOAD_BYTES = 200 * 1024 * 1024
ALLOWED_SUFFIXES = {
    ".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aac",
    ".mp4", ".avi", ".mov", ".mkv", ".webm",
}


@dataclass
class Job:
    id: str
    file_name: str
    path: Path
    status: str = "queued"          # queued | running | done | error
    stage: str = "queued"
    ratio: float = 0.0
    message: str = "대기 중"
    report: Optional[dict] = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    events: List[dict] = field(default_factory=list)

    def snapshot(self) -> dict:
        return {
            "job_id": self.id,
            "file_name": self.file_name,
            "status": self.status,
            "stage": self.stage,
            "ratio": round(self.ratio, 3),
            "message": self.message,
            "error": self.error,
        }


JOBS: Dict[str, Job] = {}
# 실시간 세션. 동시에 하나만 살아 있지만, 종료 후 결과를 조회할 수 있게 dict로 둔다.
SESSIONS: Dict[str, "object"] = {}
EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dualguard")

app = FastAPI(
    title="DualGuard API",
    description="통화·화상통화 사기 위험도 교차분석 엔진",
    version="0.1.0",
)


def _save_history(job: Job) -> None:
    """
    분석 결과를 디스크에 남긴다. 기획서 [Phase 2-4] 분석 히스토리 대시보드 대응.

    **원본 미디어가 아니라 분석 결과(JSON)만 저장한다.** 기획서 개인정보 보호 설계가
    "업로드 파일은 분석 완료 즉시 삭제"라고 못박고 있으므로, 통화 내용 자체를
    다시 들을 수 있게 남기지 않는다. 다만 전사 텍스트는 결과에 포함되므로
    (근거 표시에 필요하다) 이 디렉터리도 민감 정보로 다뤄야 한다 — .gitignore 대상.
    """
    if not job.report:
        return
    try:
        HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "job_id": job.id,
            "file_name": job.file_name,
            "analyzed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "report": job.report,
        }
        (HISTORY_DIR / f"{job.id}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        # 히스토리 저장 실패가 분석 결과 반환을 막으면 안 된다.
        pass


def _run_job(job: Job, loop: asyncio.AbstractEventLoop) -> None:
    """워커 스레드에서 실행된다. 진행률은 이벤트 루프로 안전하게 넘긴다."""
    def progress(stage: str, ratio: float, message: str):
        job.stage, job.ratio, job.message = stage, ratio, message
        job.events.append({"stage": stage, "ratio": ratio, "message": message})

    job.status = "running"
    try:
        report = analyze(str(job.path), strategy=DEFAULT_STRATEGY,
                         progress=progress)
        job.report = report.as_dict()
        # 파이프라인은 디스크상의 임시 파일명을 쓴다. 사용자에게는 올린 원본 이름을 보여준다.
        job.report["file_name"] = job.file_name
        job.status = "done"
        job.message = "분석 완료"
        job.ratio = 1.0
        _save_history(job)
    except Exception as exc:
        job.status = "error"
        job.error = f"{type(exc).__name__}: {exc}"
        job.message = "분석 실패"
    finally:
        # 기획서 [개인정보 보호 설계]: "분석 완료 즉시 서버에서 삭제하는 것을 원칙으로"
        try:
            job.path.unlink(missing_ok=True)
        except Exception:
            pass


@app.get("/api/health")
def health() -> dict:
    """각 엔진이 실제로 준비됐는지 확인한다. 데모 전 점검용."""
    from content_analysis import rag as rag_mod
    from content_analysis.llm_classifier import active_provider_label, is_available as llm_ok
    from media_detection import audio_spoof_detector as aasist
    from media_detection import faceforensics_detector as ff

    return {
        "status": "ok",
        "engines": {
            "stt": {"ready": True, "detail": "faster-whisper (첫 실행 시 모델 다운로드)"},
            # 어느 백엔드가 실제로 잡혔는지 그대로 보여준다 (Claude / Gemini / 폴백)
            "content_llm": {"ready": llm_ok(),
                            "detail": active_provider_label()
                            if llm_ok()
                            else "ANTHROPIC_API_KEY 또는 GEMINI_API_KEY 필요. "
                                 "없으면 키워드 규칙으로 폴백"},
            "rag": {"ready": rag_mod.is_available(), "detail": "ChromaDB + ko-sroberta"},
            "audio_spoof": {"ready": aasist.is_available(), "detail": "AASIST"},
            "deepfake": {"ready": ff.is_available(),
                         "detail": "FF++ Xception. 없으면 ViT 폴백(판별력 미검증)"},
        },
        "jobs_in_memory": len(JOBS),
    }


# ---------------------------------------------------------------- 실시간 세션
#
# 기획서 Phase 2. 파일 단건 분석과 달리 모델을 세션 동안 상주시킨다.
# 메모리 한계 때문에 **동시에 하나의 세션만** 허용하고, 세션이 열려 있는 동안에는
# 파일 분석을 막는다(둘이 겹치면 AASIST 추론에서 네이티브 크래시가 난다).
# 엔진 쪽 설계는 src/orchestration/streaming.py docstring 참고.

STREAM_CHUNK_SUFFIXES = {".webm", ".wav", ".ogg", ".mp4", ".m4a"}
MAX_CHUNK_BYTES = 8 * 1024 * 1024


@app.post("/api/sessions")
async def create_session(stt_model: str = "base", audio_spoof: bool = True) -> dict:
    from orchestration.streaming import StreamSession, StreamingBusyError, reap_stale

    # 확장이 DELETE를 못 보내고 죽으면 세션이 락을 쥔 채 남는다. 새로 만들려는
    # 지금이 바로 그게 문제가 되는 순간이라 여기서 정리한다.
    reaped = reap_stale()

    try:
        session = StreamSession(stt_model=stt_model, use_audio_spoof=audio_spoof)
    except StreamingBusyError as exc:
        raise HTTPException(409, str(exc))

    SESSIONS[session.session_id] = session

    # 모델을 여기서 미리 올린다. 안 하면 첫 청크 하나가 26초 걸려
    # 통화 시작 직후 — 사기에서 가장 중요한 구간 — 를 놓친다.
    loop = asyncio.get_running_loop()
    try:
        warm = await loop.run_in_executor(EXECUTOR, session.warmup)
    except Exception as exc:
        # 여기서 그냥 던지면 **실패한 세션이 전역 락을 쥔 채 남아** 이후 모든
        # 세션 생성이 409로 막힌다. 실제로 메모리 부족(mkl_malloc) 때 그렇게 됐다.
        # 반드시 정리하고 이유를 알려준다.
        SESSIONS.pop(session.session_id, None)
        try:
            session.close()
        except Exception:
            pass
        detail = f"{type(exc).__name__}: {exc}"
        if "malloc" in detail or "memory" in detail.lower():
            detail += ("\n메모리가 부족합니다. 다른 프로그램(브라우저 탭 등)을 정리하고 "
                       "서버를 다시 띄우세요. 여유 6GB 아래에서는 모델이 올라가지 않습니다.")
        raise HTTPException(503, f"세션을 시작하지 못했습니다 — {detail}")

    out = {**session.snapshot(), **warm}
    if reaped:
        out["reaped_session"] = reaped   # 죽은 세션을 정리했다는 사실을 숨기지 않는다
    return out


@app.post("/api/sessions/{session_id}/chunk")
async def push_chunk(session_id: str, file: UploadFile = File(...)) -> dict:
    session = SESSIONS.get(session_id)
    if not session:
        raise HTTPException(404, "해당 세션을 찾을 수 없습니다")

    suffix = Path(file.filename or "").suffix.lower() or ".webm"
    if suffix not in STREAM_CHUNK_SUFFIXES:
        raise HTTPException(400, f"지원하지 않는 청크 형식입니다: {suffix}")

    blob = await file.read(MAX_CHUNK_BYTES + 1)
    if len(blob) > MAX_CHUNK_BYTES:
        raise HTTPException(413, f"청크가 너무 큽니다 (상한 {MAX_CHUNK_BYTES // 1024 // 1024}MB)")
    if not blob:
        raise HTTPException(400, "빈 청크입니다")

    # 추론은 CPU를 오래 잡는 동기 작업이라 이벤트 루프에서 직접 돌리면 서버가 멈춘다.
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(EXECUTOR, session.add_chunk, blob, suffix)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/sessions/{session_id}")
def get_session(session_id: str) -> dict:
    session = SESSIONS.get(session_id)
    if not session:
        raise HTTPException(404, "해당 세션을 찾을 수 없습니다")
    return {**session.snapshot(), "segments": session.segments()}


@app.delete("/api/sessions/{session_id}")
def close_session(session_id: str) -> dict:
    session = SESSIONS.pop(session_id, None)
    if not session:
        raise HTTPException(404, "해당 세션을 찾을 수 없습니다")
    return session.close()


@app.post("/api/analyze")
async def create_analysis(file: UploadFile = File(...)) -> dict:
    from orchestration.streaming import is_busy

    if is_busy():
        raise HTTPException(
            409,
            "실시간 세션이 열려 있어 파일 분석을 시작할 수 없습니다. "
            "세션을 먼저 종료하세요 (DELETE /api/sessions/{id}). "
            "모델이 메모리를 동시에 잡으면 프로세스가 죽습니다.",
        )

    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(
            400,
            f"지원하지 않는 형식입니다: {suffix or '(확장자 없음)'}. "
            f"지원: {', '.join(sorted(ALLOWED_SUFFIXES))}",
        )

    job_id = uuid.uuid4().hex[:12]
    dest = UPLOAD_DIR / f"{job_id}{suffix}"

    # 스트리밍으로 받아 상한을 넘으면 즉시 끊는다 (메모리에 통째로 올리지 않는다)
    size = 0
    with open(dest, "wb") as out:
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                out.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(
                    413, f"파일이 너무 큽니다 (상한 {MAX_UPLOAD_BYTES // 1024 // 1024}MB)")
            out.write(chunk)

    if size == 0:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, "빈 파일입니다")

    job = Job(id=job_id, file_name=file.filename or dest.name, path=dest)
    JOBS[job_id] = job

    loop = asyncio.get_running_loop()
    loop.run_in_executor(EXECUTOR, _run_job, job, loop)

    return {"job_id": job_id, "file_name": job.file_name, "size_bytes": size,
            "status": job.status}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "해당 작업을 찾을 수 없습니다")
    return job.snapshot()


@app.get("/api/results/{job_id}")
def get_result(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "해당 작업을 찾을 수 없습니다")
    if job.status == "error":
        return JSONResponse({"job_id": job_id, "status": "error", "error": job.error},
                            status_code=500)
    if job.status != "done":
        return JSONResponse({"job_id": job_id, "status": job.status,
                             "message": "아직 분석 중입니다"}, status_code=202)
    return {"job_id": job_id, "status": "done", "report": job.report}


@app.get("/api/history")
def list_history(limit: int = 30) -> dict:
    """
    분석 히스토리 목록. 기획서 [Phase 2-4] + 메뉴 구조도의 '분석 히스토리' 대응.

    메모리(JOBS)가 아니라 디스크에서 읽는다. 서버를 재시작해도 남아야 하고,
    심사 중 실수로 창을 닫아도 결과를 다시 열 수 있어야 하기 때문이다.
    """
    items = []
    if HISTORY_DIR.exists():
        for p in sorted(HISTORY_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime,
                        reverse=True)[:limit]:
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                r = d.get("report") or {}
                items.append({
                    "job_id": d.get("job_id", p.stem),
                    "file_name": d.get("file_name", ""),
                    "analyzed_at": d.get("analyzed_at", ""),
                    "overall_score": r.get("overall_score"),
                    "overall_level": r.get("overall_level"),
                    "content_risk": r.get("content_risk"),
                    "media_risk": r.get("media_risk"),
                    "duration": r.get("duration"),
                    "n_segments": len(r.get("segments") or []),
                })
            except Exception:
                continue   # 깨진 파일 하나가 목록 전체를 막지 않게
    return {"count": len(items), "items": items}


@app.get("/api/history/{job_id}")
def get_history(job_id: str):
    """저장된 리포트를 그대로 돌려준다 (서버 재시작 후에도 열람 가능)."""
    p = HISTORY_DIR / f"{Path(job_id).name}.json"     # 경로 조작 방지
    if not p.exists():
        raise HTTPException(404, "저장된 분석 결과가 없습니다")
    d = json.loads(p.read_text(encoding="utf-8"))
    return {"job_id": d.get("job_id", job_id), "status": "done",
            "analyzed_at": d.get("analyzed_at"), "report": d.get("report")}


@app.delete("/api/history/{job_id}")
def delete_history(job_id: str) -> dict:
    """
    개별 기록 삭제.

    기획서 개인정보 보호 설계상 사용자가 자기 분석 기록을 지울 수 있어야 한다.
    전사 텍스트가 들어 있으므로 '남길지 말지'는 사용자가 정하는 게 맞다.
    """
    p = HISTORY_DIR / f"{Path(job_id).name}.json"
    if not p.exists():
        raise HTTPException(404, "저장된 분석 결과가 없습니다")
    p.unlink()
    return {"deleted": job_id}


@app.websocket("/ws/jobs/{job_id}")
async def job_progress(ws: WebSocket, job_id: str):
    await ws.accept()
    job = JOBS.get(job_id)
    if not job:
        await ws.send_json({"type": "error", "message": "해당 작업을 찾을 수 없습니다"})
        await ws.close()
        return

    sent = 0
    try:
        while True:
            # 워커 스레드가 쌓아둔 진행 이벤트를 순서대로 흘려보낸다
            while sent < len(job.events):
                await ws.send_json({"type": "progress", **job.events[sent]})
                sent += 1

            if job.status == "done":
                await ws.send_json({"type": "done", "report": job.report})
                break
            if job.status == "error":
                await ws.send_json({"type": "error", "message": job.error})
                break

            await asyncio.sleep(0.25)
    except WebSocketDisconnect:
        return
    finally:
        try:
            await ws.close()
        except Exception:
            pass


# --- 정적 대시보드 ---
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/")
    def index():
        return FileResponse(str(STATIC_DIR / "index.html"))
