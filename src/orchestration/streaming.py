"""
실시간 스트리밍 분석 세션
담당: 이상원

기획서 Phase 2의 핵심. 지금까지 백엔드는 **파일 단건 분석**만 했고, 크롬 확장은
5초 청크를 만들어 `POST /api/analyze`를 반복 호출하는 임시 형태였다.
그 방식은 청크마다 Whisper/AASIST 모델을 다시 로드해서 실시간이라 부를 수 없었다
(청크 5초당 모델 로드만 10초 이상).

이 모듈은 **모델을 세션 동안 상주시키고 청크를 누적 분석**한다.

## 설계에서 신경 쓴 것

1. **모델 상주 vs 메모리** — 파일 분석 경로는 단계마다 모델을 내린다
   (`analyze(free_models=True)`). 여유 6GB 아래에서 네이티브 크래시(0xC0000005)가
   나기 때문이다. 스트리밍은 반대로 모델을 붙들고 있어야 하므로,
   **동시에 하나의 세션만** 허용하고 영상 분석은 기본으로 끈다.
   서버가 파일 분석과 스트리밍을 동시에 돌리지 않도록 API 계층에서 막는다.

2. **STT는 base가 기본** — 파일 분석은 small을 쓴다(5dB 소음에서 CER 0.34 vs 0.56).
   그런데 스트리밍은 청크가 도착하는 속도를 따라가야 한다. 실측에서 base가
   clean CER 0.057로 small(0.0455)과 큰 차이가 없으면서 3.1배 빠르다
   (docs/stt_benchmark.md). 지연이 쌓여 뒤처지는 쪽이 더 나쁘다.

3. **겹침 재전사** — 청크 경계에서 단어가 잘린다. 직전 청크의 끝
   `OVERLAP_SEC`초를 앞에 붙여 전사하고, 겹치는 구간에서 나온 세그먼트는 버린다.

4. **AASIST 최소 길이** — 3초보다 짧은 오디오는 반복 패딩 때문에 진짜 음성도
   합성으로 잘못 판정한다(실측: 1초 99.79, 전체 2.22). 그래서 누적 버퍼에서
   최소 3초 창을 만들어 넣는다. 창이 안 차면 그 청크는 음성 판정을 건너뛴다.

5. **콘텐츠는 오프라인 분류기** — LLM은 네트워크 왕복이 있어 실시간에 안 맞는다.
   키가 있어도 스트리밍에서는 오프라인 분류기를 쓰고, 통화가 끝난 뒤
   전체 파일을 다시 분석할 때 LLM을 쓰는 것을 권한다.

사용:
    session = StreamSession()
    session.add_chunk(webm_bytes)      # 반복 호출
    session.snapshot()                 # 현재까지의 위험도
    session.close()
"""

import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

from content_analysis.content_risk import classify_offline
from media_detection.deepfake_detector import FrameAggregation, aggregate_scores
from scoring.fraud_risk_score import (
    DEFAULT_CONTENT_WEIGHT,
    DEFAULT_MEDIA_WEIGHT,
    DEFAULT_STRATEGY,
    compute_fraud_risk_score,
)

from .pipeline import ACTION_PLANS, risk_level

SAMPLE_RATE = 16000

# 스트리밍 기본값 (근거는 모듈 docstring)
STREAM_STT_MODEL = "base"
OVERLAP_SEC = 1.5           # 청크 경계에서 잘린 단어를 살리기 위한 겹침
AASIST_MIN_SEC = 3.0        # 이보다 짧으면 진짜 음성도 합성으로 오판한다
AASIST_WINDOW_SEC = 4.04    # AASIST 학습 길이 (64,600 샘플)
MAX_BUFFER_SEC = 600.0      # 10분. 통화가 길어져도 메모리가 무한히 늘지 않게
MAX_SEGMENTS = 400

# 청크가 이만큼 끊기면 죽은 세션으로 보고 자동으로 닫는다. 확장이 DELETE를 못 보내고
# 죽는 경우(탭 종료, 브라우저 크래시, 네트워크 끊김)에 세션이 락을 영원히 쥐는 걸 막는다.
# 90초로 잡은 이유: 정상 청크 간격이 5초이므로 18배 여유다. 통화 중 긴 침묵이 있어도
# 오탐으로 세션을 끊지 않는다.
IDLE_TIMEOUT_SEC = 90.0


class StreamingBusyError(RuntimeError):
    """이미 다른 세션이 모델을 잡고 있을 때."""


@dataclass
class StreamSegment:
    index: int
    start: float
    end: float
    transcript: str
    content_risk: float = 0.0
    media_risk: float = 0.0
    fraud_risk_score: float = 0.0
    level: str = "낮음"
    top_category: Optional[str] = None
    matched_terms: List[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "index": self.index,
            "start": round(self.start, 2),
            "end": round(self.end, 2),
            "transcript": self.transcript,
            "content_risk": round(self.content_risk, 2),
            "media_risk": round(self.media_risk, 2),
            "fraud_risk_score": round(self.fraud_risk_score, 2),
            "level": self.level,
            "top_category": self.top_category,
            "matched_terms": self.matched_terms[:6],
        }


def _decode_to_wave(blob: bytes, suffix: str = ".webm") -> np.ndarray:
    """
    브라우저가 보낸 청크(webm/opus 등)를 16kHz 모노 float32로 디코딩한다.

    ffmpeg에 파이프로 넣지 않고 임시 파일을 거치는 이유: webm은 컨테이너라
    스트림 중간 조각만으로는 헤더가 없어 파이프 디코딩이 자주 실패한다.
    확장이 청크마다 완결된 컨테이너를 보내도록 돼 있어서 파일이 안전하다.
    """
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / f"chunk{suffix}"
        src.write_bytes(blob)
        cmd = [
            "ffmpeg", "-nostdin", "-loglevel", "error", "-y",
            "-i", str(src),
            "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE),
            "-f", "f32le", "-",
        ]
        proc = subprocess.run(cmd, capture_output=True)
        if proc.returncode != 0 or not proc.stdout:
            raise RuntimeError(
                "청크를 디코딩하지 못했습니다. 완결된 컨테이너(webm/wav)를 보내야 합니다. "
                f"ffmpeg: {proc.stderr.decode('utf-8', 'replace').strip()[:200]}"
            )
        return np.frombuffer(proc.stdout, dtype=np.float32).copy()


class StreamSession:
    """
    청크를 누적하며 위험도를 갱신하는 세션.

    스레드 세이프가 필요한 이유: FastAPI가 청크 업로드를 워커 스레드에서 처리하므로
    같은 세션에 대한 호출이 겹칠 수 있다. 모델 추론이 재진입 가능하지 않아 잠근다.
    """

    _global_lock = threading.Lock()
    _active: Optional["StreamSession"] = None

    def __init__(self, stt_model: str = STREAM_STT_MODEL, use_audio_spoof: bool = True):
        with StreamSession._global_lock:
            if StreamSession._active is not None:
                raise StreamingBusyError(
                    "이미 실행 중인 스트리밍 세션이 있습니다. "
                    "메모리 한계 때문에 동시에 하나만 허용합니다."
                )
            StreamSession._active = self

        self.session_id = uuid.uuid4().hex[:12]
        self.created_at = time.time()
        self.last_activity = self.created_at
        self.stt_model = stt_model
        self.use_audio_spoof = use_audio_spoof

        self._lock = threading.Lock()
        self._buffer = np.zeros(0, dtype=np.float32)
        self._consumed_sec = 0.0        # 전사를 끝낸 지점
        self._last_end = 0.0            # 마지막으로 **내보낸** 구간의 끝 (중복 방지용)
        self._segments: List[StreamSegment] = []
        self._audio_scores: List[float] = []
        self._warnings: List[str] = []
        self._closed = False
        self._chunks = 0

    # ------------------------------------------------------------------ 내부

    def _transcribe_new(self) -> List[dict]:
        """아직 전사하지 않은 구간을 전사한다. 겹침 구간 세그먼트는 버린다."""
        from content_analysis import stt as stt_mod
        import soundfile as sf

        total_sec = len(self._buffer) / SAMPLE_RATE
        if total_sec - self._consumed_sec < 1.0:
            return []                     # 1초도 안 쌓였으면 다음 청크를 기다린다

        start_sec = max(0.0, self._consumed_sec - OVERLAP_SEC)
        seg_audio = self._buffer[int(start_sec * SAMPLE_RATE):]

        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "seg.wav"
            sf.write(str(wav), seg_audio, SAMPLE_RATE, subtype="PCM_16")
            transcript = stt_mod.get_shared_stt(self.stt_model).transcribe(str(wav))

        out = []
        for s in transcript.segments:
            abs_start = start_sec + s.start
            abs_end = start_sec + s.end
            text = s.text.strip()
            if not text:
                continue

            # 겹침 때문에 같은 말이 두 번 전사된다. **이미 내보낸 구간의 끝보다
            # 앞에서 시작하는 세그먼트는 버린다.** 끝 시각만 비교하면(이전 구현)
            # 겹침에서 시작해 새 구간까지 걸친 세그먼트가 통과해 중복이 남는다.
            # 실측: 78초 통화가 29구간으로 부풀었고, 같은 문장이 두 번 찍혔다.
            if abs_start < self._last_end - 0.2:
                continue

            out.append({"start": abs_start, "end": abs_end, "text": text})
            self._last_end = max(self._last_end, abs_end)

        self._consumed_sec = total_sec
        return out

    def warmup(self) -> dict:
        """
        모델을 미리 로드한다.

        안 하면 **첫 청크 하나가 26초** 걸린다(실측). 통화 시작 직후가 사기에서
        가장 중요한 구간인데 거기서 밀리면 의미가 없다. 세션을 만들 때
        미리 불러두고, 그 대가로 세션 생성이 느려지는 쪽을 택했다.
        """
        t0 = time.time()
        from content_analysis import stt as stt_mod

        stt_mod.get_shared_stt(self.stt_model)._ensure_loaded()
        if self.use_audio_spoof:
            from media_detection.audio_spoof_detector import get_shared_detector

            get_shared_detector()._ensure_loaded()
        return {"warmup_sec": round(time.time() - t0, 1)}

    def _audio_risk_for(self, start: float, end: float) -> Optional[float]:
        """
        구간의 음성 스푸핑 점수. 3초가 안 되면 주변으로 넓혀서 채운다.
        그래도 모자라면 None(판정 보류)을 준다 — 억지로 넣으면 오탐이 난다.
        """
        if not self.use_audio_spoof:
            return None

        from media_detection.audio_spoof_detector import get_shared_detector

        total_sec = len(self._buffer) / SAMPLE_RATE
        if total_sec < AASIST_MIN_SEC:
            return None

        want = max(AASIST_WINDOW_SEC, end - start)
        center = (start + end) / 2.0
        lo = max(0.0, center - want / 2.0)
        hi = min(total_sec, lo + want)
        lo = max(0.0, hi - want)

        chunk = self._buffer[int(lo * SAMPLE_RATE):int(hi * SAMPLE_RATE)]
        if chunk.size < int(AASIST_MIN_SEC * SAMPLE_RATE):
            return None

        detector = get_shared_detector()
        from media_detection.audio_spoof_detector import WINDOW_SAMPLES, _pad

        windows = [_pad(chunk.astype(np.float64), WINDOW_SAMPLES)]
        scores = detector.score_windows(windows)
        return float(scores[0]) if scores else None

    # ------------------------------------------------------------------ 공개

    def add_chunk(self, blob: bytes, suffix: str = ".webm") -> dict:
        """청크 하나를 받아 누적 분석하고, 이번에 새로 생긴 구간들을 돌려준다."""
        if self._closed:
            raise RuntimeError("이미 종료된 세션입니다.")

        with self._lock:
            self.last_activity = time.time()
            wave = _decode_to_wave(blob, suffix)
            if wave.size == 0:
                return {"new_segments": [], **self._snapshot_locked()}

            self._buffer = np.concatenate([self._buffer, wave])
            self._chunks += 1

            # 버퍼 상한. 앞쪽을 버리되 타임스탬프는 절대 시각을 유지한다.
            max_samples = int(MAX_BUFFER_SEC * SAMPLE_RATE)
            if self._buffer.size > max_samples:
                self._buffer = self._buffer[-max_samples:]
                if "버퍼" not in " ".join(self._warnings):
                    self._warnings.append(
                        f"통화가 {MAX_BUFFER_SEC / 60:.0f}분을 넘어 앞부분 오디오는 "
                        "메모리에서 지웠습니다. 이미 계산된 구간 점수는 유지됩니다."
                    )

            new_segments: List[StreamSegment] = []
            for raw in self._transcribe_new():
                breakdown = classify_offline(raw["text"])
                content = breakdown.content_risk
                audio = self._audio_risk_for(raw["start"], raw["end"])
                if audio is not None:
                    self._audio_scores.append(audio)

                media = audio if audio is not None else 0.0
                frs = compute_fraud_risk_score(
                    content, media,
                    strategy=DEFAULT_STRATEGY,
                    content_weight=DEFAULT_CONTENT_WEIGHT,
                    media_weight=DEFAULT_MEDIA_WEIGHT,
                ).fraud_risk_score

                top = breakdown.top_category if breakdown.top_score > 0 else None
                terms = list(breakdown.matched_terms.get(top, [])) if top else []

                seg = StreamSegment(
                    index=len(self._segments),
                    start=raw["start"], end=raw["end"], transcript=raw["text"],
                    content_risk=content, media_risk=media,
                    fraud_risk_score=frs, level=risk_level(frs),
                    top_category=top, matched_terms=terms,
                )
                self._segments.append(seg)
                new_segments.append(seg)

            if len(self._segments) > MAX_SEGMENTS:
                self._segments = self._segments[-MAX_SEGMENTS:]

            return {
                "new_segments": [s.as_dict() for s in new_segments],
                **self._snapshot_locked(),
            }

    def _snapshot_locked(self) -> dict:
        """
        현재까지의 통합 위험도.

        전체 점수는 **구간 최고점이 아니라 상위 k개 평균**으로 낸다. 한 구간이
        튀었다고 통화 전체를 '높음'으로 올리면 헛경보가 늘기 때문이다
        (파일 분석 경로와 같은 집계 규칙).
        """
        content_scores = [s.content_risk for s in self._segments]
        content = aggregate_scores(content_scores, FrameAggregation.TOPK_MEAN) \
            if content_scores else 0.0
        media = aggregate_scores(self._audio_scores, FrameAggregation.TOPK_MEAN) \
            if self._audio_scores else 0.0

        overall = compute_fraud_risk_score(
            content, media,
            strategy=DEFAULT_STRATEGY,
            content_weight=DEFAULT_CONTENT_WEIGHT,
            media_weight=DEFAULT_MEDIA_WEIGHT,
        ).fraud_risk_score
        level = risk_level(overall)

        return {
            "session_id": self.session_id,
            "elapsed_sec": round(time.time() - self.created_at, 1),
            "audio_sec": round(len(self._buffer) / SAMPLE_RATE, 1),
            "chunks": self._chunks,
            "n_segments": len(self._segments),
            "content_risk": round(content, 2),
            "media_risk": round(media, 2),
            "overall_score": round(overall, 2),
            "overall_level": level,
            "action_plan": ACTION_PLANS[level],
            "engines": {
                "stt": f"faster-whisper ({self.stt_model})",
                "content": "오프라인 (키워드 + 의미 유사도)",
                "audio": "AASIST" if self.use_audio_spoof else "미사용",
                "video": "미사용 (스트리밍은 음성·화법만)",
            },
            "warnings": list(self._warnings),
            "closed": self._closed,
        }

    def snapshot(self) -> dict:
        with self._lock:
            return self._snapshot_locked()

    def segments(self) -> List[dict]:
        with self._lock:
            return [s.as_dict() for s in self._segments]

    def close(self) -> dict:
        """세션을 끝내고 모델을 내린다. 반드시 불러야 다음 분석이 가능하다."""
        with self._lock:
            if self._closed:
                return self._snapshot_locked()
            self._closed = True
            snap = self._snapshot_locked()
            self._buffer = np.zeros(0, dtype=np.float32)

        # 모델 해제가 실패해도 전역 락은 반드시 풀어야 한다. 안 풀면 이후 모든
        # 세션 생성이 막힌다(실측: warmup 실패 후 서버 재시작 전까지 409 지속).
        try:
            from content_analysis import stt as stt_mod
            from media_detection import audio_spoof_detector as aasist

            stt_mod.release_model()
            aasist.release_model()
        except Exception:
            pass

        with StreamSession._global_lock:
            if StreamSession._active is self:
                StreamSession._active = None
        return snap


def active_session() -> Optional[StreamSession]:
    return StreamSession._active


def is_busy() -> bool:
    return StreamSession._active is not None


def reap_stale(max_idle_sec: float = IDLE_TIMEOUT_SEC) -> Optional[str]:
    """
    청크가 끊긴 지 오래된 세션을 닫는다. 닫았으면 그 session_id를 돌려준다.

    필요한 이유: 세션을 닫는 건 확장(DELETE 호출)의 책임인데, 브라우저가 죽거나
    탭이 닫히거나 네트워크가 끊기면 그 호출이 영영 안 온다. 그러면 **세션 하나가
    전역 락을 쥔 채 남아 이후 모든 분석이 409로 막힌다.** 실제로 테스트 중에
    그 상태에 빠져서 서버를 재시작해야 했다.

    타이머 스레드를 따로 두지 않고 세션을 새로 만들 때 검사한다. 막히는 순간이
    곧 새 세션을 만들려는 순간이라 이 시점 검사만으로 충분하고,
    유휴 상태에서 도는 스레드가 없어 메모리에도 유리하다.
    """
    session = StreamSession._active
    if session is None:
        return None
    if time.time() - session.last_activity < max_idle_sec:
        return None
    sid = session.session_id
    session.close()
    return sid
