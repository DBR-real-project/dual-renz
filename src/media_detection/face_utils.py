"""
얼굴 검출 유틸 (dlib 대체)
담당: 이상원

원본 FaceForensics 코드와 대부분의 딥페이크 탐지 레포는 dlib으로 얼굴을 찾는다.
그런데 Windows + Python 3.9에서 dlib은 CMake/Visual Studio 빌드툴이 필요해
해커톤 일정에서 리스크가 크다. 여기서는 OpenCV 내장 Haar cascade로 대체한다.

  트레이드오프: Haar cascade는 dlib HOG/CNN이나 DSFD보다 검출률이 낮고
  정면 얼굴에 편향돼 있다. 통화 영상은 대체로 정면이라 실용적으로는 버틸 만하지만,
  얼굴 검출률(face_detection_rate)을 항상 결과에 같이 실어서
  "검출을 못 해서 점수가 낮은 것"과 "진짜 위조가 아닌 것"을 구분할 수 있게 한다.

주의: opencv-python 5.0에는 cv2.CascadeClassifier가 없다. requirements.txt에서
      4.11.x로 고정한 이유다.
"""

from pathlib import Path
from typing import Optional, Tuple

import cv2
import numpy as np

_cascade: Optional["cv2.CascadeClassifier"] = None


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


def detect_largest_face(frame_bgr: np.ndarray) -> Optional[Tuple[int, int, int, int]]:
    """가장 큰 얼굴의 (x, y, w, h)를 반환. 못 찾으면 None."""
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    faces = get_cascade().detectMultiScale(
        gray, scaleFactor=1.1, minNeighbors=5, minSize=(48, 48)
    )
    if len(faces) == 0:
        return None
    x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
    return int(x), int(y), int(w), int(h)


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
