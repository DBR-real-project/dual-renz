"""
DualGuard 전체 분석 파이프라인
담당: 이상원

기획서 [시스템 구성 및 아키텍처]:
  *"사용자가 파일을 업로드하면 오디오·비디오 트랙이 분리되고, 콘텐츠 분석 엔진
   (STT→LLM 화법분석)과 미디어 분석 엔진(AASIST+딥페이크 탐지)이 병렬로 작동합니다.
   두 엔진의 결과는 통합 스코어링 모듈에서 하나의 Fraud Risk Score로 합쳐지고,
   최종적으로 타임라인 대시보드에 시각화됩니다."*

시간축 정렬이 이 모듈의 핵심이다.
  대시보드가 콘텐츠/미디어 위험도를 **같은 시간축의 이중 라인 그래프**로 그리려면
  두 엔진의 결과가 같은 구간에 대응해야 한다. 그래서 STT 세그먼트(발화 단위)를
  기준 구간으로 삼고, 각 구간의 [start, end] 안에서 프레임과 오디오를 잘라 분석한다.
  발화 단위로 끊는 이유는, 사용자가 위험 구간을 클릭했을 때 "이 말이 문제였다"를
  바로 보여줄 수 있어야 하기 때문이다.

효율:
  영상/오디오를 세그먼트마다 다시 열지 않는다. 파형은 한 번 읽어 메모리에서 자르고,
  영상은 한 번 열어 순차 스캔하며 각 구간의 프레임을 모은다.
"""

import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

import cv2
import numpy as np

SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from content_analysis import rag as rag_mod  # noqa: E402
from content_analysis.content_risk import ContentRiskBreakdown, classify_by_keywords  # noqa: E402
from content_analysis.llm_classifier import (  # noqa: E402
    active_provider_label,
    classify_segment,
    is_available as llm_available,
)
from content_analysis.stt import Transcript, release_model, transcribe  # noqa: E402
from media_detection import audio_spoof_detector as aasist  # noqa: E402
from media_detection.deepfake_detector import FrameAggregation, aggregate_scores  # noqa: E402
from media_detection.face_utils import crop_face, crop_face_square  # noqa: E402
from media_detection.media_risk import MediaCombineMode, resolve_backend  # noqa: E402
from scoring.fraud_risk_score import ScoringStrategy, compute_fraud_risk_score  # noqa: E402

VIDEO_SUFFIXES = {".mp4", ".avi", ".mov", ".mkv", ".webm"}

# AASIST 배치 크기. 4를 넘기지 말 것 — 다른 모델이 메모리를 잡고 있으면
# torch conv에서 접근 위반(0xC0000005)으로 프로세스가 죽는다.
# 파이썬 예외가 아니라 네이티브 크래시라 잡을 수 없으므로 애초에 피해야 한다.
AASIST_BATCH = 4

# 신호등 임계값. 기획서 [Phase 2-2] 신호등 UI + [3단계 액션 플랜] 대응.
# 값 근거는 docs/validation_report.md 4-1 참고 (팀 확정 필요 사항).
LEVEL_THRESHOLDS = {"높음": 70.0, "중간": 40.0}

ACTION_PLANS = {
    "높음": {
        "level": "높음",
        "color": "red",
        "headline": "즉시 통화를 종료하세요",
        "actions": [
            "지금 통화를 끊으세요. 상대가 끊지 말라고 요구하는 것 자체가 위험 신호입니다.",
            "어떤 금액도 이체하지 말고, OTP·보안카드·비밀번호를 알려주지 마세요.",
            "112(경찰) 또는 1332(금융감독원)로 직접 전화해 확인하세요.",
            "이미 이체했다면 즉시 은행 콜센터에 지급정지를 요청하세요.",
        ],
        "links": [
            {"label": "경찰청 신고 112", "url": "tel:112"},
            {"label": "금융감독원 1332", "url": "tel:1332"},
            {"label": "사이버범죄 신고", "url": "https://ecrm.police.go.kr"},
        ],
    },
    "중간": {
        "level": "중간",
        "color": "yellow",
        "headline": "확인이 필요합니다",
        "actions": [
            "통화를 끊고, 기관 공식 대표번호로 직접 다시 걸어 확인하세요.",
            "상대가 알려준 번호가 아니라 홈페이지에 공개된 번호를 쓰세요.",
            "가족이나 지인에게 상황을 이야기해 보세요. 혼자 판단하지 마세요.",
            "화상통화라면 상대에게 손을 얼굴 앞에서 흔들어 달라고 요청해 보세요.",
        ],
        "links": [{"label": "금융감독원 1332", "url": "tel:1332"}],
    },
    "낮음": {
        "level": "낮음",
        "color": "green",
        "headline": "특별한 위험 신호가 없습니다",
        "actions": [
            "현재 구간에서는 사기 정황이 발견되지 않았습니다.",
            "다만 이 결과는 참고용이며, 금전 요구나 인증번호 요구가 나오면 즉시 의심하세요.",
        ],
        "links": [],
    },
}


def risk_level(score: float) -> str:
    if score >= LEVEL_THRESHOLDS["높음"]:
        return "높음"
    if score >= LEVEL_THRESHOLDS["중간"]:
        return "중간"
    return "낮음"


@dataclass
class SegmentResult:
    index: int
    start: float
    end: float
    transcript: str

    content_risk: float = 0.0
    content_detail: dict = field(default_factory=dict)
    rag_matches: List[dict] = field(default_factory=list)

    deepfake_score: Optional[float] = None
    audio_spoof_score: Optional[float] = None
    media_risk: float = 0.0
    faces_detected: int = 0
    frames_analyzed: int = 0

    fraud_risk_score: float = 0.0
    level: str = "낮음"

    def as_dict(self) -> dict:
        return {
            "index": self.index,
            "start": round(self.start, 2),
            "end": round(self.end, 2),
            "transcript": self.transcript,
            "content_risk": round(self.content_risk, 2),
            "content_detail": self.content_detail,
            "rag_matches": self.rag_matches,
            "deepfake_score": (round(self.deepfake_score, 2)
                               if self.deepfake_score is not None else None),
            "audio_spoof_score": (round(self.audio_spoof_score, 2)
                                  if self.audio_spoof_score is not None else None),
            "media_risk": round(self.media_risk, 2),
            "faces_detected": self.faces_detected,
            "frames_analyzed": self.frames_analyzed,
            "fraud_risk_score": round(self.fraud_risk_score, 2),
            "level": self.level,
        }


@dataclass
class AnalysisReport:
    file_name: str
    duration: float
    segments: List[SegmentResult]
    overall_score: float
    overall_level: str
    content_risk: float
    media_risk: float
    engines: Dict[str, str]
    strategy: str
    elapsed_sec: float
    top_categories: List[dict] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "file_name": self.file_name,
            "duration": round(self.duration, 2),
            "overall_score": round(self.overall_score, 2),
            "overall_level": self.overall_level,
            "action_plan": ACTION_PLANS[self.overall_level],
            "content_risk": round(self.content_risk, 2),
            "media_risk": round(self.media_risk, 2),
            "engines": self.engines,
            "strategy": self.strategy,
            "elapsed_sec": round(self.elapsed_sec, 1),
            "top_categories": self.top_categories,
            "warnings": self.warnings,
            "segments": [s.as_dict() for s in self.segments],
        }


def _sample_frames_by_segment(
    video_path: Path,
    segments: List,
    fps_per_segment: float = 2.0,
    max_frames_per_segment: int = 6,
    square_crop: bool = True,
):
    """
    영상을 한 번만 열고 순차 스캔하며 각 세그먼트의 얼굴 크롭을 모은다.

    세그먼트마다 VideoCapture를 새로 열고 seek하면 구간 수만큼 디코딩이 반복된다.
    (실측 기준 30구간이면 수십 초 차이) 한 번 훑으면서 나눠 담는 편이 훨씬 싸다.
    """
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"영상을 열 수 없습니다: {video_path}")
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0

    buckets: Dict[int, List[np.ndarray]] = {i: [] for i in range(len(segments))}
    face_counts: Dict[int, int] = {i: 0 for i in range(len(segments))}
    interval = max(1, int(round(src_fps / fps_per_segment)))

    seg_i = 0
    frame_idx = 0
    while seg_i < len(segments):
        ok, frame = cap.read()
        if not ok:
            break
        t = frame_idx / src_fps
        # 현재 시각이 속한 세그먼트로 커서를 옮긴다 (STT 세그먼트는 시간순 정렬)
        while seg_i < len(segments) and t >= segments[seg_i].end:
            seg_i += 1
        if seg_i >= len(segments):
            break
        if t >= segments[seg_i].start and frame_idx % interval == 0:
            if len(buckets[seg_i]) < max_frames_per_segment:
                face = crop_face_square(frame) if square_crop else crop_face(frame)
                if face is not None:
                    face_counts[seg_i] += 1
                    buckets[seg_i].append(face)
                elif not square_crop:
                    buckets[seg_i].append(frame)
        frame_idx += 1
    cap.release()
    return buckets, face_counts


def analyze(
    media_path: str,
    strategy: ScoringStrategy = ScoringStrategy.MULTIPLICATIVE_BONUS,
    combine_mode: MediaCombineMode = MediaCombineMode.MAX,
    aggregation: FrameAggregation = FrameAggregation.TOPK_MEAN,
    use_llm: bool = True,
    use_rag: bool = True,
    stt_model: str = "small",
    free_models: bool = True,
    progress: Optional[Callable[[str, float, str], None]] = None,
) -> AnalysisReport:
    """
    파일 하나를 끝까지 분석해 리포트를 만든다.

    progress(stage, ratio, message): 진행률 콜백. 웹소켓으로 흘려보내기 위한 것.

    free_models: 단계가 끝날 때마다 그 단계의 모델을 메모리에서 내린다.

      기본값이 True인 이유. 이 파이프라인은 Whisper + AASIST + FF++ Xception +
      임베딩 모델을 순서대로 쓰는데, 전부 붙들고 있으면 수 GB가 필요하다.
      실측 환경(RAM 16GB, 브라우저 등 상시 실행)에서 여유 메모리가 6GB 아래로
      떨어지면 torch가 `mkl_malloc: failed to allocate memory`로 실패하거나
      아예 접근 위반으로 프로세스가 죽는다.

      단계마다 내리면 다음 요청에서 다시 로드하느라 몇 초가 더 걸리지만,
      데모 도중 죽는 것보다 낫다. 메모리가 넉넉한 서버에서는 False로 두면
      재로딩 비용을 없앨 수 있다.
    """
    t0 = time.time()
    path = Path(media_path)
    if not path.exists():
        raise FileNotFoundError(f"파일을 찾을 수 없습니다: {media_path}")

    def emit(stage: str, ratio: float, message: str):
        if progress:
            progress(stage, ratio, message)

    warnings: List[str] = []
    is_video = path.suffix.lower() in VIDEO_SUFFIXES

    # --- 1. STT (콘텐츠 엔진 입구) ---
    emit("stt", 0.05, "음성을 텍스트로 변환하는 중…")
    transcript: Transcript = transcribe(str(path), model_size=stt_model)
    segments = transcript.segments
    if not segments:
        raise RuntimeError("음성에서 발화를 찾지 못했습니다. 무음 파일일 수 있습니다.")
    emit("stt", 0.30, f"{len(segments)}개 구간 인식 완료")

    # STT 모델을 즉시 내린다. Whisper(CTranslate2)가 메모리를 붙들고 있으면
    # 바로 다음 단계인 AASIST의 conv 연산이 접근 위반으로 프로세스를 죽인다.
    # (자세한 내용은 content_analysis.stt.release_model 참고)
    release_model()

    results = [
        SegmentResult(index=i, start=s.start, end=s.end, transcript=s.text.strip())
        for i, s in enumerate(segments)
    ]

    # --- 2. 미디어 엔진: 오디오 스푸핑 (구간별) ---
    audio_ok = False
    if aasist.is_available():
        emit("audio", 0.35, "음성 합성 여부를 분석하는 중…")
        try:
            wave = aasist._load_waveform(path)
            detector = aasist.get_shared_detector()
            sr = aasist.SAMPLE_RATE
            windows, owners = [], []
            for r in results:
                a, b = int(r.start * sr), int(r.end * sr)
                chunk = wave[a:b]
                if chunk.size < sr // 2:          # 0.5초 미만 구간은 건너뛴다
                    continue
                windows.append(aasist._pad(chunk))
                owners.append(r.index)
            # 배치를 4로 제한한다. STT를 내려도 여유를 두는 편이 안전하다
            # (실측: STT 로드 상태에서 6 이상이면 네이티브 크래시)
            scores: List[float] = []
            for i in range(0, len(windows), AASIST_BATCH):
                scores.extend(detector.score_windows(windows[i:i + AASIST_BATCH]))
            for idx, sc in zip(owners, scores):
                results[idx].audio_spoof_score = sc
            audio_ok = bool(scores)
        except Exception as exc:
            warnings.append(f"음성 스푸핑 분석을 건너뜀: {type(exc).__name__}: {exc}")
        finally:
            if free_models:
                aasist.release_model()
    else:
        warnings.append("AASIST 미설치 — 음성 스푸핑 분석을 건너뜀")
    emit("audio", 0.50, "음성 분석 완료" if audio_ok else "음성 분석 건너뜀")

    # --- 3. 미디어 엔진: 영상 딥페이크 (구간별) ---
    video_backend = None
    if is_video:
        emit("video", 0.55, "영상 프레임에서 얼굴 위조를 탐지하는 중…")
        try:
            video_backend = resolve_backend("auto")
            if video_backend == "ff":
                from media_detection import faceforensics_detector as ff
                vdet = ff.get_shared_detector()
                square = True
            else:
                from media_detection import deepfake_detector as vit
                vdet = vit.get_shared_detector()
                square = False
                warnings.append(
                    "FF++ 가중치가 없어 ViT 폴백으로 분석했습니다. "
                    "ViT는 판별력이 검증되지 않았으므로 영상 점수를 근거로 쓰지 마세요."
                )
            buckets, face_counts = _sample_frames_by_segment(path, results, square_crop=square)
            for r in results:
                frames = buckets.get(r.index, [])
                r.faces_detected = face_counts.get(r.index, 0)
                r.frames_analyzed = len(frames)
                if frames:
                    r.deepfake_score = aggregate_scores(vdet.score_frames(frames), aggregation)
            if all(r.frames_analyzed == 0 for r in results):
                warnings.append("얼굴이 검출된 프레임이 없어 영상 분석 결과가 없습니다.")
        except Exception as exc:
            warnings.append(f"영상 분석을 건너뜀: {type(exc).__name__}: {exc}")
        finally:
            if free_models and video_backend == "ff":
                from media_detection import faceforensics_detector as ff
                ff.release_model()
    emit("video", 0.70, "영상 분석 완료" if is_video else "오디오 전용 파일 — 영상 분석 없음")

    # --- 4. 콘텐츠 엔진: 8대 사회공학 기법 분류 (+RAG) ---
    llm_on = use_llm and llm_available()
    if not llm_on and use_llm:
        warnings.append("LLM API 키가 없어 키워드 규칙으로 분류했습니다.")
    rag_on = use_rag and rag_mod.is_available()

    texts = [r.transcript for r in results]
    for i, r in enumerate(results):
        emit("content", 0.70 + 0.25 * (i / max(1, len(results))),
             f"대화 분석 {i + 1}/{len(results)}")
        before = texts[i - 1] if i > 0 else ""
        after = texts[i + 1] if i + 1 < len(texts) else ""
        try:
            b: ContentRiskBreakdown = (
                classify_segment(r.transcript, before, after) if llm_on
                else classify_by_keywords(r.transcript)
            )
        except Exception as exc:
            warnings.append(f"구간 {i} 분류 실패, 키워드 폴백: {type(exc).__name__}")
            b = classify_by_keywords(r.transcript)
        r.content_risk = b.content_risk
        r.content_detail = b.as_dict()

        if rag_on and r.transcript:
            try:
                r.rag_matches = [
                    m.as_dict()
                    for m in rag_mod.search_cases(r.transcript, top_k=2, exclude_normal=True)
                ]
            except Exception:
                pass

    # --- 5. 통합 스코어링 ---
    emit("scoring", 0.96, "통합 위험도를 산출하는 중…")
    for r in results:
        a = r.audio_spoof_score or 0.0
        v = r.deepfake_score or 0.0
        if combine_mode == MediaCombineMode.MAX:
            r.media_risk = max(a, v)
        else:
            r.media_risk = 0.5 * a + 0.5 * v
        r.fraud_risk_score = compute_fraud_risk_score(
            content_risk=r.content_risk,
            media_risk=r.media_risk,
            strategy=strategy,
            media_risk_is_dummy=not (audio_ok or video_backend),
        ).fraud_risk_score
        r.level = risk_level(r.fraud_risk_score)

    # 통화 전체 점수: 최고 위험 구간을 대표로 삼는다.
    # 평균을 쓰면 30분 통화 중 30초짜리 결정적 구간이 희석돼 경고가 안 뜬다.
    overall = max((r.fraud_risk_score for r in results), default=0.0)

    # 합성 음성인데 대화 내용은 멀쩡한 경우를 구분해 알려준다.
    # TTS 안내방송, 오디오북, 자동응답도 '합성 음성'이라 AASIST가 높게 잡는다.
    # 이걸 그대로 경고로 띄우면 사기가 아닌 통화에 헛경보가 난다.
    if audio_ok and results:
        audio_scores = [r.audio_spoof_score for r in results if r.audio_spoof_score is not None]
        if audio_scores and min(audio_scores) > 90 and max(r.content_risk for r in results) < 20:
            warnings.append(
                "음성은 합성으로 판정됐지만 대화 내용에는 사기 신호가 없습니다. "
                "TTS 안내방송이나 자동응답, 또는 합성음으로 만든 테스트 파일일 수 있습니다."
            )

    cat_totals: Dict[str, float] = {}
    for r in results:
        for cat, sc in (r.content_detail.get("category_scores") or {}).items():
            cat_totals[cat] = max(cat_totals.get(cat, 0.0), float(sc))
    from content_analysis.content_risk import CATEGORY_LABELS_KO, SocialEngineeringCategory
    top_categories = [
        {"category": c, "label": CATEGORY_LABELS_KO.get(SocialEngineeringCategory(c), c),
         "score": round(s, 1)}
        for c, s in sorted(cat_totals.items(), key=lambda kv: -kv[1])[:5] if s > 0
    ]

    emit("done", 1.0, "분석 완료")
    return AnalysisReport(
        file_name=path.name,
        duration=transcript.duration or (results[-1].end if results else 0.0),
        segments=results,
        overall_score=overall,
        overall_level=risk_level(overall),
        content_risk=max((r.content_risk for r in results), default=0.0),
        media_risk=max((r.media_risk for r in results), default=0.0),
        engines={
            "stt": f"faster-whisper ({stt_model})",
            # 어느 백엔드(Claude/Gemini/Ollama)가 실제로 돌았는지 그대로 보여준다.
            "content": active_provider_label() if llm_on else "키워드 규칙 (LLM 키 없음)",
            "rag": "ChromaDB + ko-sroberta" if rag_on else "미사용",
            "audio": "AASIST" if audio_ok else "미사용",
            "video": {"ff": "FF++ Xception", "vit": "HuggingFace ViT (미검증)"}.get(
                video_backend, "미사용"),
        },
        strategy=strategy.value,
        elapsed_sec=time.time() - t0,
        top_categories=top_categories,
        warnings=warnings,
    )
