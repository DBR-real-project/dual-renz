"""
프레임 추출 스크립트 (OpenCV 방식) - 딥페이크 탐지 모델 인풋 포맷 확인용
담당: 이상원

목적: 통화·화상통화 영상에서 일정 간격으로 프레임을 뽑아, 이후 딥페이크 탐지
모델(FF++ 기반 사전학습 모델)에 넣을 인풋 포맷(해상도/색공간/저장방식)이
맞는지 확인한다. (기획서 데이터 확보 방안: "초당 1프레임, 224~256px 다운스케일")

사용법:
    python scripts/extract_frames.py --input data/test_clips/synthetic_test_clip.mp4 --fps 1 --size 256
    python scripts/extract_frames.py --input <DFDC mp4 경로> --fps 1 --size 256 --out data/frames_output/<video_id>

ffmpeg CLI로 동일한 작업을 하려면 (참고용, 더 빠름):
    ffmpeg -i input.mp4 -vf "fps=1,scale=256:256" data/frames_output/frame_%04d.jpg
"""

import argparse
import sys
from pathlib import Path

import cv2


def extract_frames(input_path: Path, output_dir: Path, target_fps: float, size: int) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)

    cap = cv2.VideoCapture(str(input_path))
    if not cap.isOpened():
        raise RuntimeError(f"영상을 열 수 없습니다: {input_path}")

    source_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frame_interval = max(1, round(source_fps / target_fps))

    saved = 0
    frame_idx = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if frame_idx % frame_interval == 0:
            resized = cv2.resize(frame, (size, size), interpolation=cv2.INTER_AREA)
            out_path = output_dir / f"frame_{saved:04d}.jpg"
            cv2.imwrite(str(out_path), resized)
            saved += 1
        frame_idx += 1
    cap.release()

    return {
        "input": str(input_path),
        "source_fps": source_fps,
        "total_source_frames": total_frames,
        "frame_interval": frame_interval,
        "saved_frames": saved,
        "output_shape": (size, size, 3),
        "output_dir": str(output_dir),
    }


def main():
    parser = argparse.ArgumentParser(description="영상에서 딥페이크 탐지용 프레임 추출")
    parser.add_argument("--input", required=True, help="입력 영상 경로 (mp4 등)")
    parser.add_argument("--out", default=None, help="출력 디렉터리 (기본: data/frames_output/<파일명>)")
    parser.add_argument("--fps", type=float, default=1.0, help="초당 추출할 프레임 수 (기본 1fps)")
    parser.add_argument("--size", type=int, default=256, help="정사각형 리사이즈 크기 (기본 256px)")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"[에러] 입력 파일이 없습니다: {input_path}", file=sys.stderr)
        sys.exit(1)

    out_dir = Path(args.out) if args.out else Path("data/frames_output") / input_path.stem

    result = extract_frames(input_path, out_dir, args.fps, args.size)
    print("=== 프레임 추출 결과 (모델 인풋 포맷 확인) ===")
    for k, v in result.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
