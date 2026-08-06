"""
딥페이크 탐지기 (HuggingFace ViT 기반) - 실제 모델 추론
담당: 이상원 (BE/미디어 분석)

docs/model_research.md의 후보 중 2순위(prithivMLmods/Deep-Fake-Detector-v2-Model)를
연동한 구현. 1순위였던 FF++ Xception(ondyari/FaceForensics)은 dlib 빌드가 필요해
Windows/Python 3.9 환경에서 세팅 리스크가 커서, 먼저 동작하는 경로를 확보하는 목적.

  ⚠ 실측 경고 (2026-08-06, 이상원): 실제 사람 사진(OpenCV 샘플 lena/messi)을 넣었더니
  "Deepfake" 확률이 각각 70.43 / 55.84로 나왔다. 진짜 사진을 위조로 판정한다는 뜻이다.
  합성 도형 영상도 68 근처가 나와서, 입력이 뭐든 55~77 사이 좁은 밴드에 몰린다.
  즉 이 모델은 현재 우리 입력에서 사실상 판별을 못 하고 있다.
  -> 이 모듈은 "파이프라인이 실제 추론으로 끝까지 돈다"를 보장하는 용도로만 쓰고,
     점수를 심사 자료의 탐지 근거로 인용하지 말 것.
     정확도 근거가 필요하면 faceforensics_detector.py(FF++ Xception) 경로를 쓸 것.
  (라벨 순서가 뒤집혔을 가능성도 배제 못 함 - id2label은 {0: Realism, 1: Deepfake}.
   확인하려면 확실한 딥페이크 샘플이 필요한데 아직 확보 못 했다.)

얼굴 크롭:
  ViT 분류기는 얼굴 위주 이미지로 학습됐기 때문에 통화 영상 전체 프레임을 그대로
  넣으면 배경이 노이즈로 작용한다. dlib 없이 OpenCV 내장 Haar cascade로 얼굴을
  크롭한다(정확도는 dlib/DSFD보다 낮지만 추가 의존성이 0). 얼굴을 못 찾으면
  전체 프레임으로 폴백하고, 그 사실을 결과에 함께 반환한다.

TODO(팀 논의): 프레임별 점수를 영상 하나의 점수로 합칠 때 mean/max/topk_mean 중
               무엇을 쓸지. 기본값은 topk_mean으로 뒀다 -- max는 프레임 한 장의
               오탐에 전체 점수가 끌려가고, mean은 짧게 스치는 위조 구간을 희석시킨다.
"""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import List, Optional, Sequence

import cv2
import numpy as np

from .face_utils import crop_face

DEFAULT_MODEL_ID = "prithivMLmods/Deep-Fake-Detector-v2-Model"


class FrameAggregation(str, Enum):
    MEAN = "mean"
    MAX = "max"
    TOPK_MEAN = "topk_mean"


@dataclass
class VideoDeepfakeResult:
    """영상 한 편에 대한 딥페이크 판정 결과 (대시보드 근거 표시용)."""
    video_path: str
    deepfake_score: float          # 0~100, 높을수록 위조 의심
    frame_scores: List[float]
    frames_analyzed: int
    faces_detected: int            # 얼굴 크롭에 성공한 프레임 수
    aggregation: FrameAggregation
    model_id: str

    def as_dict(self) -> dict:
        return {
            "video_path": self.video_path,
            "deepfake_score": round(self.deepfake_score, 2),
            "frames_analyzed": self.frames_analyzed,
            "faces_detected": self.faces_detected,
            "face_detection_rate": (
                round(self.faces_detected / self.frames_analyzed, 2)
                if self.frames_analyzed else 0.0
            ),
            "aggregation": self.aggregation.value,
            "model_id": self.model_id,
            "frame_scores": [round(s, 2) for s in self.frame_scores],
        }


class DeepfakeDetector:
    """
    모델을 한 번만 로드해두고 여러 영상/프레임에 재사용하는 래퍼.
    (매 호출마다 로드하면 CPU에서 수 초씩 낭비된다)
    """

    def __init__(self, model_id: str = DEFAULT_MODEL_ID, device: str = "cpu"):
        self.model_id = model_id
        self.device = device
        self._model = None
        self._processor = None
        self._fake_label_index: Optional[int] = None

    # --- 모델 로딩 (최초 사용 시점까지 미룸) ---------------------------------

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        try:
            import torch
            from transformers import AutoImageProcessor, AutoModelForImageClassification
        except ImportError as exc:
            raise RuntimeError(
                "torch/transformers가 필요합니다. .venv 활성화 후 "
                "`pip install -r requirements.txt`를 실행하세요."
            ) from exc

        self._torch = torch
        self._processor = AutoImageProcessor.from_pretrained(self.model_id)
        self._model = AutoModelForImageClassification.from_pretrained(self.model_id)
        self._model.to(self.device)
        self._model.eval()
        self._fake_label_index = self._resolve_fake_label_index()

    def _resolve_fake_label_index(self) -> int:
        """
        모델마다 라벨 이름/순서가 다르므로 하드코딩하지 않고 id2label에서 찾는다.
        (라벨 순서를 잘못 가정하면 점수가 통째로 뒤집혀도 눈치채기 어렵다)
        """
        id2label = self._model.config.id2label
        for idx, label in id2label.items():
            normalized = str(label).lower().replace("-", "").replace("_", "").replace(" ", "")
            if "fake" in normalized or "deepfake" in normalized or normalized == "ai":
                return int(idx)
        raise RuntimeError(
            f"모델 라벨에서 'fake' 클래스를 찾지 못했습니다: {id2label}. "
            "_resolve_fake_label_index()에 이 모델의 라벨 규칙을 추가하세요."
        )

    @property
    def label_map(self) -> dict:
        self._ensure_loaded()
        return dict(self._model.config.id2label)

    # --- 얼굴 크롭 (face_utils에 위임, dlib 불필요) ---------------------------

    def crop_face(self, frame_bgr: np.ndarray, margin: float = 0.25) -> Optional[np.ndarray]:
        """가장 큰 얼굴 하나를 여백 포함해 크롭. 못 찾으면 None."""
        return crop_face(frame_bgr, margin=margin)

    # --- 추론 ---------------------------------------------------------------

    def score_frames(self, frames_bgr: Sequence[np.ndarray]) -> List[float]:
        """BGR 프레임 리스트를 배치로 추론해 각각의 위조 확률(0~100)을 반환."""
        self._ensure_loaded()
        if not frames_bgr:
            return []

        from PIL import Image

        images = [Image.fromarray(cv2.cvtColor(f, cv2.COLOR_BGR2RGB)) for f in frames_bgr]
        inputs = self._processor(images=images, return_tensors="pt").to(self.device)

        with self._torch.no_grad():
            logits = self._model(**inputs).logits
            probs = self._torch.softmax(logits, dim=-1)

        fake_probs = probs[:, self._fake_label_index].cpu().numpy()
        return [float(p) * 100.0 for p in fake_probs]

    def score_video(
        self,
        video_path: str,
        target_fps: float = 1.0,
        max_frames: int = 32,
        aggregation: FrameAggregation = FrameAggregation.TOPK_MEAN,
        use_face_crop: bool = True,
        batch_size: int = 8,
    ) -> VideoDeepfakeResult:
        """
        영상에서 target_fps 간격으로 프레임을 뽑아 추론하고 하나의 점수로 집계한다.
        디스크에 프레임을 저장하지 않는다 (scripts/extract_frames.py는 포맷 확인용 별도 도구).
        """
        path = Path(video_path)
        if not path.exists():
            raise FileNotFoundError(f"영상을 찾을 수 없습니다: {video_path}")

        frames, faces_detected = self._sample_frames(path, target_fps, max_frames, use_face_crop)
        if not frames:
            raise RuntimeError(f"영상에서 프레임을 하나도 읽지 못했습니다: {video_path}")

        scores: List[float] = []
        for i in range(0, len(frames), batch_size):
            scores.extend(self.score_frames(frames[i:i + batch_size]))

        return VideoDeepfakeResult(
            video_path=str(path),
            deepfake_score=aggregate_scores(scores, aggregation),
            frame_scores=scores,
            frames_analyzed=len(scores),
            faces_detected=faces_detected,
            aggregation=aggregation,
            model_id=self.model_id,
        )

    def _sample_frames(self, path: Path, target_fps: float, max_frames: int, use_face_crop: bool):
        cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            raise RuntimeError(f"영상을 열 수 없습니다: {path}")

        source_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        interval = max(1, round(source_fps / target_fps))

        frames: List[np.ndarray] = []
        faces_detected = 0
        idx = 0
        while len(frames) < max_frames:
            ok, frame = cap.read()
            if not ok:
                break
            if idx % interval == 0:
                if use_face_crop:
                    face = self.crop_face(frame)
                    if face is not None and face.size > 0:
                        faces_detected += 1
                        frame = face
                frames.append(frame)
            idx += 1
        cap.release()
        return frames, faces_detected


def aggregate_scores(
    scores: Sequence[float],
    aggregation: FrameAggregation = FrameAggregation.TOPK_MEAN,
    top_ratio: float = 0.25,
) -> float:
    """프레임별 점수를 영상 하나의 점수로 합친다."""
    if not scores:
        return 0.0
    if aggregation == FrameAggregation.MEAN:
        return float(np.mean(scores))
    if aggregation == FrameAggregation.MAX:
        return float(np.max(scores))
    if aggregation == FrameAggregation.TOPK_MEAN:
        k = max(1, int(round(len(scores) * top_ratio)))
        return float(np.mean(sorted(scores, reverse=True)[:k]))
    raise ValueError(f"알 수 없는 aggregation: {aggregation}")


# 모듈 전역 싱글턴 - media_risk.py에서 반복 호출할 때 모델 재로딩을 막는다.
_shared_detector: Optional[DeepfakeDetector] = None


def get_shared_detector(model_id: str = DEFAULT_MODEL_ID) -> DeepfakeDetector:
    global _shared_detector
    if _shared_detector is None or _shared_detector.model_id != model_id:
        _shared_detector = DeepfakeDetector(model_id=model_id)
    return _shared_detector


# CLI는 scripts/detect_deepfake.py 에 있다 (src/는 라이브러리만, 실행 진입점은 scripts/).
