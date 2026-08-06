"""
음성 스푸핑(합성·변조) 탐지 - AASIST
담당: 이상원 (BE/미디어 분석)

기획서의 미디어 분석 엔진 나머지 절반. 딥페이크(영상)와 짝을 이루는 오디오 쪽이다.
clovaai/aasist 공식 구현과 사전학습 가중치를 추론에만 쓴다.

  AASIST는 ASVspoof 2019 LA로 학습된 anti-spoofing 모델로, 원본 논문 기준
  EER 0.83%를 보고한다. 가중치(1.3MB)가 레포에 함께 배포돼 있어 별도 다운로드가 없다.

구현 노트:
  - 입력은 **16kHz 원시 파형 64,600 샘플(약 4.04초)** 고정이다. 통화는 이보다 기므로
    4초 창으로 잘라 각각 추론하고 집계한다. 짧으면 원본 코드처럼 반복 패딩한다.
  - 모델 출력은 2클래스. **인덱스 1이 bonafide(진짜), 0이 spoof(합성)** 이다.
    (원본 main.py의 `batch_score = batch_out[:, 1]`와 data_utils의
     `1 if label == "bonafide" else 0`에서 확인)
    우리는 "위험도"가 필요하므로 spoof 확률을 0~100으로 쓴다.
  - 영상 파일을 넣으면 ffmpeg으로 오디오 트랙을 뽑아 쓴다. 화상통화 분석에서
    영상 하나로 두 엔진을 모두 돌릴 수 있어야 하기 때문이다.

준비:
    git clone --depth 1 https://github.com/clovaai/aasist.git external/aasist
    (가중치는 레포에 포함돼 있어 추가 다운로드 불필요)
"""

import json
import subprocess
import sys
import tempfile
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import numpy as np

from .deepfake_detector import FrameAggregation, aggregate_scores

PROJECT_ROOT = Path(__file__).resolve().parents[2]
AASIST_DIR = PROJECT_ROOT / "external" / "aasist"
DEFAULT_WEIGHTS = AASIST_DIR / "models" / "weights" / "AASIST.pth"
DEFAULT_CONFIG = AASIST_DIR / "config" / "AASIST.conf"

SAMPLE_RATE = 16000
WINDOW_SAMPLES = 64600          # 원본 학습 길이 (약 4.04초)
SPOOF_INDEX = 0                 # 0 = spoof, 1 = bonafide
VIDEO_SUFFIXES = {".mp4", ".avi", ".mov", ".mkv", ".webm"}


@dataclass
class AudioSpoofResult:
    audio_path: str
    spoof_score: float           # 0~100, 높을수록 합성 의심
    window_scores: List[float]
    windows_analyzed: int
    duration_sec: float
    aggregation: FrameAggregation
    weights_path: str

    def as_dict(self) -> dict:
        return {
            "audio_path": self.audio_path,
            "spoof_score": round(self.spoof_score, 2),
            "windows_analyzed": self.windows_analyzed,
            "duration_sec": round(self.duration_sec, 2),
            "aggregation": self.aggregation.value,
            "weights_path": self.weights_path,
            "model": "AASIST (clovaai, ASVspoof2019 LA 학습)",
            "window_scores": [round(s, 2) for s in self.window_scores],
        }


def _load_waveform(path: Path) -> np.ndarray:
    """
    16kHz 모노 파형으로 읽는다. 영상이면 ffmpeg으로 오디오를 먼저 뽑는다.
    """
    import soundfile as sf

    if path.suffix.lower() in VIDEO_SUFFIXES:
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "audio.wav"
            cmd = [
                "ffmpeg", "-nostdin", "-loglevel", "error", "-y",
                "-i", str(path),
                "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE),
                "-f", "wav", str(wav),
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0 or not wav.exists():
                raise RuntimeError(
                    f"영상에서 오디오를 추출하지 못했습니다: {path.name}\n"
                    f"ffmpeg: {proc.stderr.strip()[:300]}"
                )
            data, sr = sf.read(str(wav))
    else:
        data, sr = sf.read(str(path))

    if data.ndim > 1:                      # 스테레오 -> 모노
        data = data.mean(axis=1)
    if sr != SAMPLE_RATE:
        # AASIST는 16kHz로 학습됐다. 리샘플링 없이 넣으면 주파수 축이 어긋나 결과가 무의미해진다.
        data = _resample_linear(data, sr, SAMPLE_RATE)
    return data.astype(np.float64)


def _resample_linear(x: np.ndarray, sr_from: int, sr_to: int) -> np.ndarray:
    """
    선형 보간 리샘플링. scipy/librosa 의존성을 늘리지 않기 위한 최소 구현이다.
    품질은 다운샘플 시 앤티앨리어싱이 없어 이상적이지 않다.
    TODO(이상원): 실제 통화 오디오(대부분 8k/44.1k)를 다루게 되면 soxr나 librosa로 교체 검토.
    """
    if sr_from == sr_to or x.size == 0:
        return x
    n_out = int(round(x.size * sr_to / sr_from))
    return np.interp(
        np.linspace(0, x.size - 1, n_out),
        np.arange(x.size),
        x,
    )


def _pad(x: np.ndarray, max_len: int = WINDOW_SAMPLES) -> np.ndarray:
    """원본 data_utils.pad()와 동일: 짧으면 반복해서 채운다."""
    if x.shape[0] >= max_len:
        return x[:max_len]
    repeats = int(max_len / x.shape[0]) + 1
    return np.tile(x, repeats)[:max_len]


class AudioSpoofDetector:
    def __init__(self, weights_path: Optional[Path] = None, device: str = "cpu"):
        self.weights_path = Path(weights_path) if weights_path else DEFAULT_WEIGHTS
        self.device = device
        self._model = None
        self._torch = None

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        if not AASIST_DIR.exists():
            raise FileNotFoundError(
                f"AASIST 레포가 없습니다: {AASIST_DIR}\n"
                "git clone --depth 1 https://github.com/clovaai/aasist.git external/aasist"
            )
        if not self.weights_path.exists():
            raise FileNotFoundError(f"AASIST 가중치가 없습니다: {self.weights_path}")

        import torch

        self._torch = torch

        # models.AASIST 를 import 하려면 레포 루트가 sys.path에 있어야 한다.
        if str(AASIST_DIR) not in sys.path:
            sys.path.insert(0, str(AASIST_DIR))

        with open(DEFAULT_CONFIG, "r", encoding="utf-8") as f:
            model_config = json.load(f)["model_config"]

        from models.AASIST import Model  # noqa: E402

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            model = Model(model_config)
            state = torch.load(str(self.weights_path), map_location=self.device)
            model.load_state_dict(state)
        model.to(self.device)
        model.eval()
        self._model = model

    def score_windows(self, windows: List[np.ndarray]) -> List[float]:
        """4초 창 목록을 배치 추론해 각각의 spoof 확률(0~100)을 반환."""
        self._ensure_loaded()
        if not windows:
            return []
        torch = self._torch
        batch = torch.tensor(np.stack(windows), dtype=torch.float32).to(self.device)
        with torch.no_grad():
            _, out = self._model(batch)
            probs = torch.softmax(out, dim=1)
        return [float(p) * 100.0 for p in probs[:, SPOOF_INDEX].cpu().numpy()]

    def score_audio(
        self,
        audio_path: str,
        aggregation: FrameAggregation = FrameAggregation.TOPK_MEAN,
        max_windows: int = 16,
        batch_size: int = 8,
    ) -> AudioSpoofResult:
        path = Path(audio_path)
        if not path.exists():
            raise FileNotFoundError(f"오디오/영상을 찾을 수 없습니다: {audio_path}")

        wave = _load_waveform(path)
        if wave.size == 0:
            raise RuntimeError(f"오디오가 비어 있습니다: {audio_path}")

        # 4초 창으로 자른다. 마지막 자투리는 반복 패딩해서 살린다.
        windows: List[np.ndarray] = []
        for start in range(0, wave.size, WINDOW_SAMPLES):
            if len(windows) >= max_windows:
                break
            chunk = wave[start:start + WINDOW_SAMPLES]
            if chunk.size < WINDOW_SAMPLES // 4 and windows:
                break                      # 1초 미만 자투리는 버린다
            windows.append(_pad(chunk))

        scores: List[float] = []
        for i in range(0, len(windows), batch_size):
            scores.extend(self.score_windows(windows[i:i + batch_size]))

        return AudioSpoofResult(
            audio_path=str(path),
            spoof_score=aggregate_scores(scores, aggregation),
            window_scores=scores,
            windows_analyzed=len(scores),
            duration_sec=wave.size / SAMPLE_RATE,
            aggregation=aggregation,
            weights_path=str(self.weights_path),
        )


_shared: Optional[AudioSpoofDetector] = None


def get_shared_detector() -> AudioSpoofDetector:
    global _shared
    if _shared is None:
        _shared = AudioSpoofDetector()
    return _shared


def is_available() -> bool:
    return AASIST_DIR.exists() and DEFAULT_WEIGHTS.exists()
