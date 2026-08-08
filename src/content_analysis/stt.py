"""
STT (음성 -> 텍스트) - faster-whisper
담당: 이상원 (원래 강동연 파트지만 전체 통합을 위해 구현)

기획서: *"STT(Whisper 또는 경량화된 faster-whisper)로 대화를 텍스트화"*

faster-whisper를 고른 이유:
  원본 openai-whisper는 PyTorch로 돌아 CPU에서 실시간 대비 여러 배 느리다.
  faster-whisper는 CTranslate2 백엔드라 같은 정확도에서 CPU 추론이 4배 이상 빠르고
  메모리도 적게 쓴다. 해커톤 데모는 GPU 없는 노트북에서 돌 가능성이 크다.

모델 크기 선택:
  기본값은 `small`. 한국어는 `tiny`/`base`에서 정확도가 눈에 띄게 떨어지고,
  `medium` 이상은 CPU에서 너무 느리다. `small`이 한국어 통화 데모에서 타협점이다.
  (첫 실행 시 모델을 자동 다운로드한다. small 기준 약 500MB)

출력이 왜 세그먼트 단위인가:
  기획서 결과 대시보드가 *"시간축 위에 콘텐츠 위험도와 미디어 위험도를 이중 라인
  그래프로 겹쳐"* 보여주는 구조다. 그러려면 텍스트가 타임스탬프와 함께 나와야
  미디어 분석(프레임 타임스탬프)과 시간축을 맞출 수 있다.
"""

import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

DEFAULT_MODEL_SIZE = "small"
VIDEO_SUFFIXES = {".mp4", ".avi", ".mov", ".mkv", ".webm"}


@dataclass
class TranscriptSegment:
    start: float
    end: float
    text: str
    no_speech_prob: float = 0.0

    def as_dict(self) -> dict:
        return {
            "start": round(self.start, 2),
            "end": round(self.end, 2),
            "text": self.text.strip(),
            "no_speech_prob": round(self.no_speech_prob, 3),
        }


@dataclass
class Transcript:
    segments: List[TranscriptSegment] = field(default_factory=list)
    language: str = ""
    language_probability: float = 0.0
    duration: float = 0.0
    model_size: str = DEFAULT_MODEL_SIZE

    @property
    def full_text(self) -> str:
        return " ".join(s.text.strip() for s in self.segments).strip()

    def as_dict(self) -> dict:
        return {
            "language": self.language,
            "language_probability": round(self.language_probability, 3),
            "duration": round(self.duration, 2),
            "model_size": self.model_size,
            "n_segments": len(self.segments),
            "full_text": self.full_text,
            "segments": [s.as_dict() for s in self.segments],
        }


def _extract_audio(path: Path) -> Path:
    """
    영상에서 16kHz 모노 wav를 뽑는다.
    faster-whisper도 영상을 직접 읽지만, 오디오 트랙이 없을 때 에러 메시지가
    불친절해서 여기서 먼저 걸러 명확한 예외를 낸다.
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="dualguard_stt_"))
    wav = tmp_dir / "audio.wav"
    cmd = [
        "ffmpeg", "-nostdin", "-loglevel", "error", "-y",
        "-i", str(path), "-vn", "-ac", "1", "-ar", "16000", "-f", "wav", str(wav),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not wav.exists() or wav.stat().st_size == 0:
        raise RuntimeError(
            f"오디오 트랙을 추출하지 못했습니다: {path.name}\n"
            f"오디오가 없는 영상일 수 있습니다. ffmpeg: {proc.stderr.strip()[:300]}"
        )
    return wav


class SpeechToText:
    """모델을 한 번만 로드해 재사용한다 (로딩이 수 초 걸린다)."""

    def __init__(self, model_size: str = DEFAULT_MODEL_SIZE, device: str = "cpu",
                 compute_type: str = "int8"):
        # int8 양자화: CPU에서 속도가 2배 가까이 빨라지고 한국어 정확도 손실은 미미하다.
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self._model = None

    def _ensure_loaded(self):
        if self._model is not None:
            return
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError(
                "faster-whisper가 필요합니다: "
                ".venv\\Scripts\\python.exe -m pip install faster-whisper"
            ) from exc
        self._model = WhisperModel(
            self.model_size, device=self.device, compute_type=self.compute_type
        )

    def transcribe(
        self,
        media_path: str,
        language: Optional[str] = "ko",
        vad_filter: bool = True,
        beam_size: int = 5,
    ) -> Transcript:
        """
        오디오/영상에서 타임스탬프가 붙은 세그먼트를 뽑는다.

        language="ko" 고정이 기본값이다. 자동 감지에 맡기면 짧은 통화에서
        일본어/중국어로 오인하는 경우가 있어 데모가 망가진다. 다국어 확장 시에는
        None으로 두면 자동 감지한다.

        vad_filter: 무음 구간을 잘라내 처리 시간을 줄이고 환청(hallucination)을 억제한다.
        """
        self._ensure_loaded()
        path = Path(media_path)
        if not path.exists():
            raise FileNotFoundError(f"파일을 찾을 수 없습니다: {media_path}")

        audio_path = _extract_audio(path) if path.suffix.lower() in VIDEO_SUFFIXES else path

        segments_iter, info = self._model.transcribe(
            str(audio_path),
            language=language,
            beam_size=beam_size,
            vad_filter=vad_filter,
        )

        segments = [
            TranscriptSegment(
                start=float(s.start), end=float(s.end), text=s.text,
                no_speech_prob=float(getattr(s, "no_speech_prob", 0.0) or 0.0),
            )
            for s in segments_iter
        ]

        return Transcript(
            segments=segments,
            language=info.language,
            language_probability=float(info.language_probability or 0.0),
            duration=float(info.duration or 0.0),
            model_size=self.model_size,
        )


_shared: Optional[SpeechToText] = None


def get_shared_stt(model_size: str = DEFAULT_MODEL_SIZE) -> SpeechToText:
    global _shared
    if _shared is None or _shared.model_size != model_size:
        _shared = SpeechToText(model_size=model_size)
    return _shared


def release_model() -> None:
    """
    로드된 Whisper 모델을 메모리에서 내린다.

    왜 필요한가: 파이프라인은 STT가 끝나면 곧바로 AASIST(torch) 추론으로 넘어가는데,
    Whisper(CTranslate2)가 메모리를 잡고 있으면 AASIST의 첫 conv에서
    **접근 위반(0xC0000005)으로 프로세스가 죽는다.** 파이썬 예외가 아니라 네이티브
    크래시라서 try/except로 잡을 수도 없다.

    실측: STT 로드 상태에서 AASIST 배치 4까지는 통과, 6부터 크래시.
          STT를 내리면 배치 8도 통과.

    STT는 파이프라인에서 한 번만 쓰이므로 쓰고 나서 내리는 것이 맞다.
    """
    global _shared
    if _shared is not None:
        _shared._model = None
        _shared = None
    import gc
    gc.collect()


def transcribe(media_path: str, model_size: str = DEFAULT_MODEL_SIZE, **kwargs) -> Transcript:
    return get_shared_stt(model_size).transcribe(media_path, **kwargs)
