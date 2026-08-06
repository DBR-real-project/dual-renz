"""
테스트용 합성 영상 생성기.

두 가지 모드가 있다:

  1. (기본) 움직이는 도형 + 타임스탬프 — 프레임 추출 파이프라인 스모크 테스트용.
     얼굴이 없으므로 딥페이크 탐지 성능 검증에는 쓸 수 없다.

  2. --from-image <이미지> — 얼굴 사진 한 장을 흔들며 확대/축소해 짧은 영상으로 만든다.
     얼굴 검출 -> 크롭 -> 딥페이크 추론까지 이어지는 **영상 경로 전체**를 실제 얼굴로
     돌려보기 위한 것. 정지 이미지를 반복한 것이라 판정값은 그 이미지 한 장의 값과
     거의 같고, 딥페이크 탐지 성능의 근거는 아니다.

실행:
    .venv\\Scripts\\python.exe scripts/make_synthetic_test_clip.py
    .venv\\Scripts\\python.exe scripts/make_synthetic_test_clip.py --from-image data/sanity/real_face_lena.jpg
"""

import argparse
from pathlib import Path

import cv2
import numpy as np

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "test_clips"
DEFAULT_OUT = OUT_DIR / "synthetic_test_clip.mp4"

WIDTH, HEIGHT = 480, 360
FPS = 25
DURATION_SEC = 6


def make_shape_clip(out_path: Path) -> int:
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(out_path), fourcc, FPS, (WIDTH, HEIGHT))

    total_frames = FPS * DURATION_SEC
    for i in range(total_frames):
        frame = np.full((HEIGHT, WIDTH, 3), 30, dtype=np.uint8)
        t = i / FPS
        # 움직이는 원 (얼굴 검출/크롭 파이프라인 자리 표시자 역할)
        cx = int(WIDTH / 2 + (WIDTH / 3) * np.sin(t * 1.5))
        cy = HEIGHT // 2
        cv2.circle(frame, (cx, cy), 40, (60, 180, 250), -1)
        cv2.putText(
            frame, f"t={t:5.2f}s frame={i}", (10, 30),
            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA,
        )
        writer.write(frame)

    writer.release()
    return total_frames


def make_face_clip(image_path: Path, out_path: Path) -> int:
    """얼굴 사진을 미세하게 흔들고 확대/축소해 통화 영상 비슷한 클립을 만든다."""
    image = cv2.imread(str(image_path))
    if image is None:
        raise RuntimeError(f"이미지를 읽을 수 없습니다: {image_path}")

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(out_path), fourcc, FPS, (WIDTH, HEIGHT))

    total_frames = FPS * DURATION_SEC
    h, w = image.shape[:2]
    for i in range(total_frames):
        t = i / FPS
        # 확대율 1.0~1.1, 좌우/상하 미세 이동 (통화 중 손떨림 흉내)
        zoom = 1.0 + 0.10 * (0.5 - 0.5 * np.cos(t * 1.2))
        crop_w, crop_h = int(w / zoom), int(h / zoom)
        max_dx, max_dy = w - crop_w, h - crop_h
        x0 = int((max_dx / 2) * (1 + np.sin(t * 2.0)))
        y0 = int((max_dy / 2) * (1 + np.cos(t * 1.7)))
        x0 = max(0, min(max_dx, x0))
        y0 = max(0, min(max_dy, y0))

        crop = image[y0:y0 + crop_h, x0:x0 + crop_w]
        writer.write(cv2.resize(crop, (WIDTH, HEIGHT), interpolation=cv2.INTER_AREA))

    writer.release()
    return total_frames


def main():
    parser = argparse.ArgumentParser(description="테스트용 합성 영상 생성")
    parser.add_argument("--from-image", default=None,
                        help="얼굴 사진 경로. 주면 이 사진으로 영상을 만든다")
    parser.add_argument("--out", default=None, help="출력 경로")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if args.from_image:
        image_path = Path(args.from_image)
        out_path = Path(args.out) if args.out else OUT_DIR / f"face_clip_{image_path.stem}.mp4"
        frames = make_face_clip(image_path, out_path)
        kind = f"얼굴 영상 ({image_path.name} 기반)"
    else:
        out_path = Path(args.out) if args.out else DEFAULT_OUT
        frames = make_shape_clip(out_path)
        kind = "도형 영상 (얼굴 없음)"

    print(f"생성 완료: {out_path}")
    print(f"  종류: {kind}, {frames} frames, {FPS}fps, {DURATION_SEC}s")


if __name__ == "__main__":
    main()
