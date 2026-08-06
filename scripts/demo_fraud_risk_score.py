"""
Fraud Risk Score 통합 로직 데모/수동 테스트 스크립트
담당: 이상원

실행:
    python scripts/demo_fraud_risk_score.py

콘텐츠 위험도는 강동연 파트가 완성되기 전까지 content_risk_stub 더미를,
미디어 위험도는 media_risk_dummy 더미를 사용해 "결과 대시보드"에 필요한
구간별(타임라인) Fraud Risk Score 산출까지 전체 흐름이 동작하는지 확인한다.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from scoring.fraud_risk_score import ScoringStrategy, compute_fraud_risk_score, compute_timeline
from media_detection.media_risk_dummy import get_media_risk
from content_analysis.content_risk_stub import get_content_risk_dummy


def demo_single_score():
    print("=== 1. 단일 Fraud Risk Score 계산 (버전 A vs 버전 B 비교) ===")
    content_risk, media_risk = 85.0, 78.0
    for strategy in ScoringStrategy:
        result = compute_fraud_risk_score(
            content_risk=content_risk,
            media_risk=media_risk,
            strategy=strategy,
            media_risk_is_dummy=True,
        )
        print(f"  [{strategy.value}] {result.as_dict()}")


def demo_media_risk_dummy():
    print("\n=== 2. 미디어 위험도 더미 값 (오디오/영상 결합) ===")
    result = get_media_risk(
        video_path="data/test_clips/sample_call.mp4",
        audio_path="data/test_clips/sample_call.wav",
    )
    print(f"  {result}")


def demo_timeline():
    print("\n=== 3. 구간별(타임라인) Fraud Risk Score - 결과 대시보드용 ===")
    # 가상의 30초 통화를 5초 단위 6구간으로 나눈 예시. 실제로는
    # STT 세그먼트(콘텐츠)와 프레임 배치(미디어) 타임스탬프를 정렬해서 채워야 함.
    fake_transcripts = [
        "안녕하세요 고객님", "저는 금융감독원 직원입니다", "계좌가 범죄에 연루됐습니다",
        "지금 즉시 이체하지 않으면 계좌가 동결됩니다", "다른 사람에게 알리지 마세요",
        "OTP 번호를 알려주세요",
    ]
    segments = []
    t = 0.0
    for text in fake_transcripts:
        content_risk = get_content_risk_dummy(text)
        media = get_media_risk(video_path=f"data/frames_output/seg_{int(t)}.mp4")
        segments.append({
            "start": t, "end": t + 5.0,
            "content_risk": content_risk,
            "media_risk": media["media_risk"],
            "transcript": text,
        })
        t += 5.0

    timeline = compute_timeline(segments, media_risk_is_dummy=True)
    for seg in timeline:
        print(
            f"  [{seg['start']:>4.0f}s-{seg['end']:>4.0f}s] "
            f"content={seg['content_risk']:>5.1f} media={seg['media_risk']:>5.1f} "
            f"-> Fraud Risk Score={seg['fraud_risk_score']:>5.1f}  ({seg['transcript']})"
        )


if __name__ == "__main__":
    demo_single_score()
    demo_media_risk_dummy()
    demo_timeline()
