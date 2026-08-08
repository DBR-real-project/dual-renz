"""
DualGuard 백엔드 API (FastAPI)
담당: 이상원

기획서 [Back-end & Infrastructure]:
  *"FastAPI — 실시간 스트리밍 처리에 유리한 비동기 Python 서버, STT·LLM·AASIST·
   딥페이크 모델 추론 오케스트레이션"*, *"WebSocket — 실시간 청크 송수신"*

엔드포인트:
  POST /api/analyze        파일 업로드 -> job_id 반환 (분석은 백그라운드)
  GET  /api/jobs/{id}      진행률/상태 조회 (웹소켓을 못 쓰는 환경용 폴백)
  GET  /api/results/{id}   분석 리포트 (완료 후)
  WS   /ws/jobs/{id}       진행률 실시간 스트림
  GET  /api/health         엔진별 준비 상태
  GET  /                   결과 대시보드 (정적 파일)

동시성 설계:
  분석은 CPU를 오래 잡는 동기 작업(torch 추론)이라 이벤트 루프에서 직접 돌리면
  서버 전체가 멈춘다. ThreadPoolExecutor에 넘기고, 진행률은 스레드에서
  큐로 밀어 웹소켓이 읽어간다.

  워커는 1개다. 모델들이 메모리를 많이 쓰고, 동시에 여러 건을 돌리면
  AASIST 추론이 네이티브 크래시로 프로세스를 죽인다(pipeline.AASIST_BATCH 주석 참고).
  해커톤 데모는 동시 요청이 없으므로 안전을 택했다.
"""

import asyncio
import shutil
import sys
import tempfile
import time
import uuid
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
from scoring.fraud_risk_score import ScoringStrategy  # noqa: E402

STATIC_DIR = PROJECT_ROOT / "web"
UPLOAD_DIR = Path(tempfile.gettempdir()) / "dualguard_uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

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
EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dualguard")

app = FastAPI(
    title="DualGuard API",
    description="통화·화상통화 사기 위험도 교차분석 엔진",
    version="0.1.0",
)


def _run_job(job: Job, loop: asyncio.AbstractEventLoop) -> None:
    """워커 스레드에서 실행된다. 진행률은 이벤트 루프로 안전하게 넘긴다."""
    def progress(stage: str, ratio: float, message: str):
        job.stage, job.ratio, job.message = stage, ratio, message
        job.events.append({"stage": stage, "ratio": ratio, "message": message})

    job.status = "running"
    try:
        report = analyze(str(job.path), strategy=ScoringStrategy.MULTIPLICATIVE_BONUS,
                         progress=progress)
        job.report = report.as_dict()
        # 파이프라인은 디스크상의 임시 파일명을 쓴다. 사용자에게는 올린 원본 이름을 보여준다.
        job.report["file_name"] = job.file_name
        job.status = "done"
        job.message = "분석 완료"
        job.ratio = 1.0
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
    from content_analysis.llm_classifier import is_available as llm_ok
    from media_detection import audio_spoof_detector as aasist
    from media_detection import faceforensics_detector as ff

    return {
        "status": "ok",
        "engines": {
            "stt": {"ready": True, "detail": "faster-whisper (첫 실행 시 모델 다운로드)"},
            "content_llm": {"ready": llm_ok(),
                            "detail": "ANTHROPIC_API_KEY 필요. 없으면 키워드 규칙으로 폴백"},
            "rag": {"ready": rag_mod.is_available(), "detail": "ChromaDB + ko-sroberta"},
            "audio_spoof": {"ready": aasist.is_available(), "detail": "AASIST"},
            "deepfake": {"ready": ff.is_available(),
                         "detail": "FF++ Xception. 없으면 ViT 폴백(판별력 미검증)"},
        },
        "jobs_in_memory": len(JOBS),
    }


@app.post("/api/analyze")
async def create_analysis(file: UploadFile = File(...)) -> dict:
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
