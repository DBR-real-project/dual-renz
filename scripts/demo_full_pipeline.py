"""
전체 파이프라인 end-to-end 데모
담당: 이상원

영상 한 편을 넣으면 아래 흐름 전부를 실제로 돌린다:

    영상 -> 프레임 샘플링 -> (얼굴 크롭) -> ViT 딥페이크 추론
         -> 프레임 점수 집계 -> media_risk
         -> content_risk(강동연 파트 미완성이라 더미)와 통합
         -> Fraud Risk Score

실행:
    .venv\\Scripts\\python.exe scripts/demo_full_pipeline.py
    .venv\\Scripts\\python.exe scripts/demo_full_pipeline.py --input <다른 영상> --transcript "..."

주의: 기본 입력인 synthetic_test_clip.mp4는 움직이는 도형만 있는 스모크 테스트용
      영상이라 얼굴이 없다. 여기서 나오는 딥페이크 점수는 "배선이 살아있다"는
      의미일 뿐, 탐지 성능의 근거가 아니다. 성능 검증은 FF++/DFDC 실제 클립 필요.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from content_analysis.content_risk_stub import get_content_risk_dummy
from media_detection.deepfake_detector import FrameAggregation
from media_detection.media_risk import get_media_risk
from scoring.fraud_risk_score import ScoringStrategy, compute_fraud_risk_score

DEFAULT_VIDEO = "data/test_clips/synthetic_test_clip.mp4"
DEFAULT_TRANSCRIPT = "지금 즉시 이체하지 않으면 계좌가 동결됩니다"


def main():
    parser = argparse.ArgumentParser(description="DualGuard 미디어 분석 전체 파이프라인 데모")
    parser.add_argument("--input", default=DEFAULT_VIDEO, help="입력 영상 경로")
    parser.add_argument("--transcript", default=DEFAULT_TRANSCRIPT, help="STT 결과 대역 텍스트")
    parser.add_argument("--fps", type=float, default=1.0, help="초당 분석할 프레임 수")
    parser.add_argument("--max-frames", type=int, default=16)
    parser.add_argument(
        "--aggregation", default=FrameAggregation.TOPK_MEAN.value,
        choices=[a.value for a in FrameAggregation],
    )
    parser.add_argument("--no-face-crop", action="store_true")
    args = parser.parse_args()

    video_path = Path(args.input)
    if not video_path.exists():
        print(f"[에러] 입력 영상이 없습니다: {video_path}", file=sys.stderr)
        print("      scripts/make_synthetic_test_clip.py 로 테스트 영상을 먼저 생성하세요.", file=sys.stderr)
        sys.exit(1)

    print("=== 1. 미디어 위험도 (실제 딥페이크 모델 추론) ===")
    print("    모델 최초 로딩은 다운로드/초기화로 수십 초 걸릴 수 있습니다...")
    media = get_media_risk(
        video_path=str(video_path),
        target_fps=args.fps,
        max_frames=args.max_frames,
        aggregation=FrameAggregation(args.aggregation),
        use_face_crop=not args.no_face_crop,
    )

    detail = media.pop("video_detail", None)
    print(f"    {json.dumps(media, ensure_ascii=False)}")
    if detail:
        print(f"    분석 프레임 {detail['frames_analyzed']}장, "
              f"얼굴 검출률 {detail['face_detection_rate']:.0%}, "
              f"집계방식 {detail['aggregation']}")
        print(f"    프레임별 점수: {detail['frame_scores']}")
        if detail["faces_detected"] == 0:
            print("    [경고] 얼굴이 하나도 검출되지 않았습니다 -> 전체 프레임으로 추론했습니다.")
            print("           이 영상의 점수는 탐지 성능의 근거로 쓸 수 없습니다.")
    if not media["deepfake_is_real_model"]:
        print(f"    [경고] 실제 모델 추론에 실패해 더미값으로 폴백했습니다: "
              f"{media.get('fallback_reason')}")

    print("\n=== 2. 콘텐츠 위험도 (강동연 파트 미완성 - 더미) ===")
    content_risk = get_content_risk_dummy(args.transcript)
    print(f"    transcript: {args.transcript}")
    print(f"    content_risk: {content_risk}")

    print("\n=== 3. 통합 Fraud Risk Score ===")
    for strategy in ScoringStrategy:
        result = compute_fraud_risk_score(
            content_risk=content_risk,
            media_risk=media["media_risk"],
            strategy=strategy,
            media_risk_is_dummy=not media["deepfake_is_real_model"],
        )
        marker = " (기본값)" if strategy == ScoringStrategy.MULTIPLICATIVE_BONUS else ""
        print(f"    [{strategy.value}]{marker} -> {result.fraud_risk_score:.2f}")

    print("\n=== 요약 ===")
    print(f"    영상 딥페이크 추론: {'실제 모델' if media['deepfake_is_real_model'] else '더미 폴백'}")
    print(f"    음성 스푸핑(AASIST): 미연동 (더미)")
    print(f"    콘텐츠 분석: 미연동 (더미)")


if __name__ == "__main__":
    main()
