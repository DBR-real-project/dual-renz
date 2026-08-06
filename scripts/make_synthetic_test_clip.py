"""
프레임 추출 파이프라인을 실제 DFDC/FF++ 데이터 없이도 먼저 검증할 수 있도록
움직이는 도형 + 타임스탬프 텍스트가 찍힌 짧은 합성 테스트 영상을 생성한다.
(저작권 걱정 없는 스모크 테스트용. 실제 딥페이크 판별 정확도 검증용이 아님!)

실행:
    python scripts/make_synthetic_test_clip.py
"""

import cv2
import numpy as np
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "test_clips"
OUT_PATH = OUT_DIR / "synthetic_test_clip.mp4"

WIDTH, HEIGHT = 480, 360
FPS = 25
DURATION_SEC = 6


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(OUT_PATH), fourcc, FPS, (WIDTH, HEIGHT))

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
    print(f"합성 테스트 영상 생성 완료: {OUT_PATH} ({total_frames} frames, {FPS}fps, {DURATION_SEC}s)")


if __name__ == "__main__":
    main()
