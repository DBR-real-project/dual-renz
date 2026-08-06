"""
FaceForensics++ (Xception) 딥페이크 탐지기
담당: 이상원 (BE/미디어 분석)

docs/model_research.md 1순위 경로. FF++ 데이터로 학습된 공식 Xception 베이스라인을
그대로 추론에만 쓴다. HuggingFace ViT 경로(deepfake_detector.py)와 달리 학습 데이터가
명확해서 심사 자료에 근거로 쓸 수 있다.

원본 detect_from_video.py를 그대로 쓰지 않고 이 래퍼를 따로 만든 이유:

  1. dlib 제거 — 원본은 dlib.get_frontal_face_detector()를 쓴다. Windows/Python 3.9에서
     dlib은 CMake + VS 빌드툴이 필요해 해커톤 일정에 리스크가 크다.
     face_utils.crop_face_square()가 원본 get_boundingbox()와 동일한 규칙
     (긴 변 * 1.3의 정사각형, 얼굴 중심 기준)으로 크롭하므로 전처리는 등가다.
  2. GUI 제거 — 원본은 cv2.imshow()로 매 프레임을 띄우고 결과 영상을 파일로 쓴다.
     서버에서 점수만 필요한 우리 용도에는 맞지 않는다.
  3. torch.load 호환 — 가중치가 torch 1.0 시절 "모델 객체 통째 pickle"이라
     torch 2.6+ 기본값(weights_only=True)으로는 로드에 실패한다. 아래에서 명시적으로
     weights_only=False로 로드한다. (신뢰할 수 있는 출처인 TUM 공식 배포본에 한함)

준비:
  1. git clone --depth 1 https://github.com/ondyari/FaceForensics.git external/FaceForensics
  2. http://kaldir.vc.in.tum.de/FaceForensics/models/faceforensics++_models.zip 를
     models/ 에 받아 압축 해제
  3. pip install -r requirements.txt (pretrainedmodels 필요)
"""

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence

import cv2
import numpy as np

from .deepfake_detector import FrameAggregation, aggregate_scores
from .face_utils import crop_face_square

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FF_CLASSIFICATION_DIR = PROJECT_ROOT / "external" / "FaceForensics" / "classification"
MODELS_DIR = PROJECT_ROOT / "models"

# 원본 코드 기준: 출력 인덱스 1 = fake, 0 = real
FAKE_INDEX = 1
INPUT_SIZE = 299


@dataclass
class FFVideoResult:
    video_path: str
    deepfake_score: float
    frame_scores: List[float]
    frames_analyzed: int
    faces_detected: int
    aggregation: FrameAggregation
    weights_path: str

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
            "weights_path": self.weights_path,
            "backbone": "xception (FF++ 공식 베이스라인)",
            "frame_scores": [round(s, 2) for s in self.frame_scores],
        }


def find_weights(models_dir: Path = MODELS_DIR) -> List[Path]:
    """models/ 아래에서 FF++ 가중치(.p) 파일을 모두 찾는다."""
    if not models_dir.exists():
        return []
    return sorted(models_dir.rglob("*.p"))


def default_weights(models_dir: Path = MODELS_DIR) -> Optional[Path]:
    """
    기본 가중치 선택. FF++ 배포본에는 압축률별(c0/c23/c40) 모델이 들어있는데,
    c23이 실제 통화/유튜브 영상의 압축률에 가장 가깝다는 게 FF++ 논문의 권장이다.
    """
    candidates = find_weights(models_dir)
    if not candidates:
        return None
    for keyword in ("c23", "c40", "raw", "c0"):
        for path in candidates:
            if keyword in path.name.lower() or keyword in str(path.parent).lower():
                return path
    return candidates[0]


class FaceForensicsDetector:
    """FF++ Xception 가중치를 로드해 프레임별 위조 확률(0~100)을 내는 래퍼."""

    def __init__(self, weights_path: Optional[Path] = None, device: str = "cpu"):
        self.weights_path = Path(weights_path) if weights_path else default_weights()
        self.device = device
        self._model = None
        self._transform = None
        self._torch = None

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return

        if self.weights_path is None or not self.weights_path.exists():
            raise FileNotFoundError(
                f"FF++ 가중치를 찾지 못했습니다 (찾은 위치: {MODELS_DIR}).\n"
                "faceforensics++_models.zip 을 models/ 에 받아 압축 해제하세요."
            )
        if not FF_CLASSIFICATION_DIR.exists():
            raise FileNotFoundError(
                f"FaceForensics 레포가 없습니다: {FF_CLASSIFICATION_DIR}\n"
                "git clone --depth 1 https://github.com/ondyari/FaceForensics.git "
                "external/FaceForensics"
            )

        import torch

        self._torch = torch

        # 가중치가 모델 객체 통째 pickle이라, 언피클 시점에 network.models 모듈이
        # import 가능해야 한다. 그래서 레포 경로를 sys.path에 넣는다.
        if str(FF_CLASSIFICATION_DIR) not in sys.path:
            sys.path.insert(0, str(FF_CLASSIFICATION_DIR))

        loaded = torch.load(
            str(self.weights_path), map_location=self.device, weights_only=False
        )

        if isinstance(loaded, torch.nn.Module):
            model = loaded
        else:
            # state_dict 형태로 배포된 경우
            from network.models import model_selection

            model, *_ = model_selection(modelname="xception", num_out_classes=2)
            model.load_state_dict(loaded)

        model.to(self.device)
        model.eval()
        self._model = model
        self._transform = self._build_transform()

    def _build_transform(self):
        """
        FF++ 학습 당시 전처리와 동일해야 한다.
        (external/.../dataset/transform.py의 xception_default_data_transforms['test'])
        레포가 있으면 원본을 그대로 쓰고, 없으면 동일 내용으로 폴백한다.
        """
        try:
            from dataset.transform import xception_default_data_transforms

            return xception_default_data_transforms["test"]
        except ImportError:
            from torchvision import transforms

            return transforms.Compose([
                transforms.Resize((INPUT_SIZE, INPUT_SIZE)),
                transforms.ToTensor(),
                transforms.Normalize([0.5] * 3, [0.5] * 3),
            ])

    def score_frames(self, frames_bgr: Sequence[np.ndarray]) -> List[float]:
        """BGR 프레임(얼굴 크롭 상태 권장) 리스트의 위조 확률(0~100)."""
        self._ensure_loaded()
        if not frames_bgr:
            return []

        from PIL import Image

        torch = self._torch
        tensors = [
            self._transform(Image.fromarray(cv2.cvtColor(f, cv2.COLOR_BGR2RGB)))
            for f in frames_bgr
        ]
        batch = torch.stack(tensors).to(self.device)

        with torch.no_grad():
            probs = torch.softmax(self._model(batch), dim=1)

        return [float(p) * 100.0 for p in probs[:, FAKE_INDEX].cpu().numpy()]

    def score_video(
        self,
        video_path: str,
        target_fps: float = 1.0,
        max_frames: int = 32,
        aggregation: FrameAggregation = FrameAggregation.TOPK_MEAN,
        batch_size: int = 8,
    ) -> FFVideoResult:
        path = Path(video_path)
        if not path.exists():
            raise FileNotFoundError(f"영상을 찾을 수 없습니다: {video_path}")

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
                face = crop_face_square(frame)
                if face is not None:
                    faces_detected += 1
                    frames.append(face)
                else:
                    # FF++ Xception은 얼굴 크롭으로만 학습됐다. 전체 프레임을 넣으면
                    # 의미 없는 값이 나오므로, 원본 코드처럼 그냥 건너뛴다.
                    pass
            idx += 1
        cap.release()

        if not frames:
            raise RuntimeError(
                f"얼굴이 검출된 프레임이 하나도 없습니다: {video_path}\n"
                "FF++ Xception은 얼굴 크롭 전용 모델이라 얼굴 없는 영상은 판정할 수 없습니다."
            )

        scores: List[float] = []
        for i in range(0, len(frames), batch_size):
            scores.extend(self.score_frames(frames[i:i + batch_size]))

        return FFVideoResult(
            video_path=str(path),
            deepfake_score=aggregate_scores(scores, aggregation),
            frame_scores=scores,
            frames_analyzed=len(scores),
            faces_detected=faces_detected,
            aggregation=aggregation,
            weights_path=str(self.weights_path),
        )


_shared: Optional[FaceForensicsDetector] = None


def get_shared_detector(weights_path: Optional[Path] = None) -> FaceForensicsDetector:
    global _shared
    if _shared is None or (weights_path and Path(weights_path) != _shared.weights_path):
        _shared = FaceForensicsDetector(weights_path=weights_path)
    return _shared


def is_available() -> bool:
    """FF++ 경로를 쓸 수 있는 상태인지 (레포 + 가중치 둘 다 있는지)."""
    return FF_CLASSIFICATION_DIR.exists() and default_weights() is not None
