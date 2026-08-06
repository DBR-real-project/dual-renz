"""
영상 딥페이크 위험도 추론 CLI
담당: 이상원

두 경로를 모두 지원한다:
  --backend vit  : HuggingFace ViT (모델 자동 다운로드, 얼굴 없어도 동작)
  --backend ff   : FaceForensics++ Xception (가중치 수동 준비 필요, 얼굴 필수)

실행:
    .venv\\Scripts\\python.exe scripts/detect_deepfake.py --input data/test_clips/xxx.mp4
    .venv\\Scripts\\python.exe scripts/detect_deepfake.py --input xxx.mp4 --backend ff
    .venv\\Scripts\\python.exe scripts/detect_deepfake.py --image data/sanity/real_face_lena.jpg
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import cv2  # noqa: E402

from media_detection.deepfake_detector import FrameAggregation  # noqa: E402
from media_detection import deepfake_detector as vit_backend  # noqa: E402
from media_detection import faceforensics_detector as ff_backend  # noqa: E402


def run_image(backend_name: str, image_path: str) -> None:
    """단일 이미지 판정 (모델이 실제로 판별하는지 빠르게 확인할 때)."""
    image = cv2.imread(image_path)
    if image is None:
        print(f"[에러] 이미지를 읽을 수 없습니다: {image_path}", file=sys.stderr)
        sys.exit(1)

    if backend_name == "vit":
        detector = vit_backend.get_shared_detector()
        print(f"모델 라벨: {detector.label_map}")
        face = detector.crop_face(image)
    else:
        detector = ff_backend.get_shared_detector()
        print(f"가중치: {detector.weights_path}")
        face = ff_backend.crop_face_square(image)

    if face is not None:
        print(f"얼굴 크롭 위조 확률: {detector.score_frames([face])[0]:.2f}   <- 이 값을 볼 것")
    elif backend_name == "ff":
        # FF++ face_detection 계열은 얼굴 크롭 전용이다. 전체 이미지를 넣으면
        # 분포 밖 입력이라 아무 값이나 나온다 (실측: 축구 사진 전체를 넣으니 98.07).
        print("얼굴 검출 실패 - FF++는 얼굴 크롭 전용이라 판정할 수 없습니다.")
        return
    else:
        print("얼굴 검출 실패 - 전체 이미지로 판정합니다 (참고용).")

    full_score = detector.score_frames([image])[0]
    print(f"전체 이미지 위조 확률: {full_score:.2f}"
          + ("   (참고용, FF++는 크롭 기준으로 판단할 것)" if backend_name == "ff" else ""))


def main():
    parser = argparse.ArgumentParser(description="영상/이미지 딥페이크 위험도 추론")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--input", help="입력 영상 경로")
    src.add_argument("--image", help="입력 이미지 경로 (단일 프레임 판정)")
    parser.add_argument("--backend", default="vit", choices=["vit", "ff"])
    parser.add_argument("--fps", type=float, default=1.0, help="초당 분석할 프레임 수")
    parser.add_argument("--max-frames", type=int, default=32)
    parser.add_argument("--no-face-crop", action="store_true", help="vit 백엔드 전용")
    parser.add_argument(
        "--aggregation", default=FrameAggregation.TOPK_MEAN.value,
        choices=[a.value for a in FrameAggregation],
    )
    args = parser.parse_args()

    if args.image:
        run_image(args.backend, args.image)
        return

    if args.backend == "vit":
        detector = vit_backend.get_shared_detector()
        result = detector.score_video(
            args.input,
            target_fps=args.fps,
            max_frames=args.max_frames,
            aggregation=FrameAggregation(args.aggregation),
            use_face_crop=not args.no_face_crop,
        )
        print(f"모델 라벨: {detector.label_map}")
    else:
        if not ff_backend.is_available():
            print("[에러] FF++ 경로를 쓸 수 없습니다. 레포와 가중치를 확인하세요:", file=sys.stderr)
            print(f"       레포: {ff_backend.FF_CLASSIFICATION_DIR}", file=sys.stderr)
            print(f"       가중치: {ff_backend.MODELS_DIR} 아래 *.p", file=sys.stderr)
            sys.exit(1)
        detector = ff_backend.get_shared_detector()
        result = detector.score_video(
            args.input,
            target_fps=args.fps,
            max_frames=args.max_frames,
            aggregation=FrameAggregation(args.aggregation),
        )

    print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
