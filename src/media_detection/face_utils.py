"""
얼굴 검출 유틸 (dlib 대체)
담당: 이상원

원본 FaceForensics 코드와 대부분의 딥페이크 탐지 레포는 dlib으로 얼굴을 찾는다.
그런데 Windows + Python 3.9에서 dlib은 CMake/Visual Studio 빌드툴이 필요해
해커톤 일정에서 리스크가 크다. 그래서 OpenCV 내장 검출기로 대체했다.

## 검출기가 두 개인 이유

처음에는 Haar cascade만 썼는데, **정면이 아닌 프레임을 자주 놓쳤다.**
FF++ 검증 영상 50개에서 프레임 검출률이 낮으면 딥페이크 판정 자체를 못 한다
(FF++ Xception은 얼굴 크롭 전용이라 얼굴이 없으면 예외를 던진다).
실제 통화는 고개를 돌리거나 화면 밖으로 나가는 구간이 많아 더 불리하다.

그래서 **YuNet**(OpenCV DNN 기반 경량 얼굴 검출기, 232KB ONNX)을 1순위로 두고
Haar를 폴백으로 남겼다. YuNet은 opencv 4.x에 `cv2.FaceDetectorYN`으로 들어 있어
새 파이썬 패키지를 설치할 필요가 없다 — 가중치 파일 하나만 있으면 된다.

실측 (FF++ 검증 영상 50개, 1fps 샘플링): docs/validation_report.md 4-5절 참고.

  가중치가 없으면 조용히 Haar로 폴백한다. 데모 환경에서 파일 하나 때문에
  전체가 죽는 것보다 낫고, 어느 검출기가 쓰였는지는 active_backend()로 확인된다.

준비 (선택):
    .venv\\Scripts\\python.exe scripts/download_face_detector.py

주의: opencv-python 5.0에는 cv2.CascadeClassifier가 없다. requirements.txt에서
      4.11.x로 고정한 이유다.
"""

import os
from pathlib import Path
from typing import Optional, Tuple

import cv2
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[2]
YUNET_PATH = PROJECT_ROOT / "models" / "face_detection_yunet_2023mar.onnx"

# YuNet 신뢰도 임계값. 기본 0.9는 통화 영상(측면·모션 블러)에서 너무 빡빡해
# 검출을 놓친다. 0.6으로 낮춰도 오검출은 거의 없었다 — 어차피 가장 큰 얼굴
# 하나만 쓰고, 딥페이크 판정은 크롭 이후 모델이 한다.
YUNET_SCORE_THRESHOLD = 0.6
YUNET_NMS_THRESHOLD = 0.3
YUNET_TOP_K = 50

_cascade: Optional["cv2.CascadeClassifier"] = None
_yunet = None
_yunet_size: Optional[Tuple[int, int]] = None
_yunet_failed = False


def _backend_pref() -> str:
    """DUALGUARD_FACE_DETECTOR: auto(기본) | yunet | haar"""
    return os.environ.get("DUALGUARD_FACE_DETECTOR", "auto").strip().lower()


def yunet_available() -> bool:
    return YUNET_PATH.exists() and not _yunet_failed


def active_backend() -> str:
    """실제로 쓰이는 검출기 이름. 결과 리포트에 남겨 근거를 밝힌다."""
    pref = _backend_pref()
    if pref == "haar":
        return "haar"
    if pref == "yunet":
        return "yunet"
    return "yunet" if yunet_available() else "haar"


def get_cascade() -> "cv2.CascadeClassifier":
    """Haar cascade를 한 번만 로드해 재사용."""
    global _cascade
    if _cascade is None:
        cascade_path = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
        cascade = cv2.CascadeClassifier(str(cascade_path))
        if cascade.empty():
            raise RuntimeError(
                f"Haar cascade 로드 실패: {cascade_path}\n"
                "opencv-python 4.x가 설치돼 있는지 확인하세요 (5.0에는 CascadeClassifier가 없음)."
            )
        _cascade = cascade
    return _cascade


def _get_yunet(width: int, height: int):
    """
    YuNet은 입력 크기를 미리 알려줘야 한다. 프레임 크기가 바뀔 때만 다시 설정한다
    (매 프레임 setInputSize를 부르면 내부 버퍼를 다시 잡아 느려진다).
    """
    global _yunet, _yunet_size, _yunet_failed
    if _yunet_failed:
        return None
    if _yunet is None:
        try:
            _yunet = cv2.FaceDetectorYN.create(
                str(YUNET_PATH), "", (width, height),
                YUNET_SCORE_THRESHOLD, YUNET_NMS_THRESHOLD, YUNET_TOP_K,
            )
            _yunet_size = (width, height)
        except cv2.error:
            # 가중치 파일이 깨졌거나(LFS 포인터를 받은 경우 등) opencv가 못 읽는 경우.
            # 여기서 죽이지 않고 Haar로 내려간다.
            _yunet_failed = True
            return None
    elif _yunet_size != (width, height):
        _yunet.setInputSize((width, height))
        _yunet_size = (width, height)
    return _yunet


def _detect_yunet(frame_bgr: np.ndarray) -> Optional[Tuple[int, int, int, int]]:
    height, width = frame_bgr.shape[:2]
    det = _get_yunet(width, height)
    if det is None:
        return None
    _, faces = det.detect(frame_bgr)
    if faces is None or len(faces) == 0:
        return None
    # faces: [x, y, w, h, 랜드마크 10개, score]
    x, y, w, h = max(faces, key=lambda f: f[2] * f[3])[:4]
    return int(x), int(y), int(w), int(h)


def _detect_haar(frame_bgr: np.ndarray) -> Optional[Tuple[int, int, int, int]]:
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    faces = get_cascade().detectMultiScale(
        gray, scaleFactor=1.1, minNeighbors=5, minSize=(48, 48)
    )
    if len(faces) == 0:
        return None
    x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
    return int(x), int(y), int(w), int(h)


def detect_largest_face(frame_bgr: np.ndarray) -> Optional[Tuple[int, int, int, int]]:
    """
    가장 큰 얼굴의 (x, y, w, h)를 반환. 못 찾으면 None.

    YuNet을 먼저 쓰고, 못 찾으면 Haar로 한 번 더 본다. 두 검출기가 놓치는 패턴이
    달라서(YuNet은 아주 작은 얼굴, Haar는 측면) 이어 붙이면 검출률이 올라간다.
    """
    pref = _backend_pref()

    if pref != "haar":
        box = _detect_yunet(frame_bgr)
        if box is not None:
            return box
        if pref == "yunet":
            return None

    return _detect_haar(frame_bgr)


def crop_face(frame_bgr: np.ndarray, margin: float = 0.25) -> Optional[np.ndarray]:
    """가장 큰 얼굴을 여백 포함 사각형으로 크롭 (ViT 등 일반 분류기용)."""
    box = detect_largest_face(frame_bgr)
    if box is None:
        return None
    x, y, w, h = box
    pad_x, pad_y = int(w * margin), int(h * margin)
    height, width = frame_bgr.shape[:2]
    x0, y0 = max(0, x - pad_x), max(0, y - pad_y)
    x1, y1 = min(width, x + w + pad_x), min(height, y + h + pad_y)
    crop = frame_bgr[y0:y1, x0:x1]
    return crop if crop.size > 0 else None


def crop_face_square(frame_bgr: np.ndarray, scale: float = 1.3) -> Optional[np.ndarray]:
    """
    정사각형 크롭. FaceForensics 원본의 get_boundingbox()와 같은 규칙을 따른다
    (얼굴 박스의 긴 변 * scale을 한 변으로, 얼굴 중심 기준, 경계 클리핑).
    FF++ Xception은 이 전처리로 학습됐으므로 규칙을 바꾸면 정확도가 떨어진다.
    """
    box = detect_largest_face(frame_bgr)
    if box is None:
        return None
    x, y, w, h = box
    height, width = frame_bgr.shape[:2]

    size = int(max(w, h) * scale)
    center_x, center_y = x + w // 2, y + h // 2
    x0 = max(center_x - size // 2, 0)
    y0 = max(center_y - size // 2, 0)
    size = min(width - x0, size)
    size = min(height - y0, size)

    crop = frame_bgr[y0:y0 + size, x0:x0 + size]
    return crop if crop.size > 0 else None
