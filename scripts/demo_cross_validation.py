"""
교차검증 동작 시연 - 4가지 조합 비교
담당: 이상원

기획서의 핵심 주장:
  *"기존 서비스가 놓치는 '얼굴은 진짜인데 화법이 사기인 경우'와
    '화법은 자연스러운데 얼굴이 가짜인 경우'를 모두 잡아내는 교차검증형"*

이 스크립트는 그 주장이 실제로 코드에서 성립하는지 한 화면에 보여준다.
영상 진위 × 음성 진위 4가지 조합에 대해 두 엔진이 독립적으로 반응하고,
통합 스코어링이 의도대로 합쳐지는지 확인한다.

준비:
    .venv\\Scripts\\python.exe scripts/fetch_ff_samples.py
    .venv\\Scripts\\python.exe scripts/fetch_asvspoof_samples.py
    .venv\\Scripts\\python.exe scripts/make_demo_clips.py

실행:
    .venv\\Scripts\\python.exe scripts/demo_cross_validation.py
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from _console import setup_console  # noqa: E402

setup_console()  # cp949 콘솔에서 유니코드 출력 중 죽는 문제 방지 (_console.py)

from content_analysis.content_risk import classify_offline  # noqa: E402
from media_detection.deepfake_detector import FrameAggregation  # noqa: E402
from media_detection.media_risk import get_media_risk  # noqa: E402
from scoring.fraud_risk_score import ScoringStrategy, compute_fraud_risk_score  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CLIP_DIR = PROJECT_ROOT / "data" / "demo_clips"

CLIPS = [
    ("both_real", "진짜 영상 + 진짜 음성"),
    ("video_fake", "딥페이크 영상 + 진짜 음성"),
    ("audio_fake", "진짜 영상 + 합성 음성"),
    ("both_fake", "딥페이크 영상 + 합성 음성"),
]

# 콘텐츠 위험도는 강동연 파트 연동 전이므로, 조합별 차이를 보기 위해
# 정상 대화 / 사기 대화 두 가지를 고정 입력으로 쓴다.
NORMAL_TALK = "안녕하세요 오늘 날씨가 좋네요 점심 뭐 드셨어요"
SCAM_TALK = ("저는 금융감독원 직원입니다. 고객님 계좌가 범죄에 연루됐습니다. "
             "지금 즉시 안전계좌로 이체하지 않으면 계좌가 동결됩니다. "
             "아무에게도 알리지 마세요")


def main():
    parser = argparse.ArgumentParser(description="교차검증 4조합 비교")
    parser.add_argument("--max-frames", type=int, default=8)
    args = parser.parse_args()

    if not CLIP_DIR.exists() or not any(CLIP_DIR.glob("*.mp4")):
        print(f"[에러] 데모 클립이 없습니다: {CLIP_DIR}", file=sys.stderr)
        print("      먼저 실행: .venv\\Scripts\\python.exe scripts\\make_demo_clips.py",
              file=sys.stderr)
        sys.exit(1)

    print("미디어 분석 중... (클립당 수 초)\n")
    rows = []
    for name, desc in CLIPS:
        path = CLIP_DIR / f"{name}.mp4"
        if not path.exists():
            print(f"  건너뜀(파일 없음): {path.name}", file=sys.stderr)
            continue
        m = get_media_risk(
            video_path=str(path),
            max_frames=args.max_frames,
            aggregation=FrameAggregation.TOPK_MEAN,
        )
        rows.append({
            "name": name, "desc": desc,
            "video": m["deepfake_score"],
            "audio": m["audio_spoof_score"],
            "media": m["media_risk"],
        })

    print("=" * 78)
    print(" 1. 미디어 분석 - 두 엔진이 독립적으로 반응하는가")
    print("=" * 78)
    print(f"  {'클립':<12} {'구성':<26} {'영상':>8} {'음성':>8} {'미디어':>8}")
    print(f"  {'-'*12} {'-'*26} {'-'*8} {'-'*8} {'-'*8}")
    for r in rows:
        print(f"  {r['name']:<12} {r['desc']:<26} {r['video']:>8.1f} "
              f"{r['audio']:>8.1f} {r['media']:>8.1f}")

    print("\n  읽는 법: 영상만 위조된 클립은 영상 점수만, 음성만 위조된 클립은")
    print("           음성 점수만 올라가야 두 엔진이 서로 독립적이라는 뜻이다.")
    print("           (미디어 위험도는 현재 max 결합 - 둘 중 높은 쪽을 택함)")

    print("\n" + "=" * 78)
    print(" 2. 콘텐츠 x 미디어 교차검증 - Fraud Risk Score")
    print("=" * 78)

    for talk_label, talk in (("정상 대화", NORMAL_TALK), ("사기 화법", SCAM_TALK)):
        cb = classify_offline(talk)
        content = cb.content_risk
        top = cb.as_dict()["top_category_label"] or "-"
        print(f"\n  [{talk_label}] content_risk={content}  (최고 카테고리: {top})")
        print(f"    \"{talk[:52]}...\"" if len(talk) > 52 else f"    \"{talk}\"")
        print(f"    {'클립':<12} {'미디어':>8} {'버전A(PDF)':>12} {'버전B(DOCX)':>12}")
        print(f"    {'-'*12} {'-'*8} {'-'*12} {'-'*12}")
        for r in rows:
            a = compute_fraud_risk_score(content, r["media"],
                                         ScoringStrategy.THRESHOLD_BONUS).fraud_risk_score
            b = compute_fraud_risk_score(content, r["media"],
                                         ScoringStrategy.MULTIPLICATIVE_BONUS).fraud_risk_score
            print(f"    {r['name']:<12} {r['media']:>8.1f} {a:>12.1f} {b:>12.1f}")

    print("\n" + "=" * 78)
    print(" 해석")
    print("=" * 78)
    print("""
  - 정상 대화 + 진짜 미디어  -> 낮은 점수 (경고 없음)
  - 사기 화법 + 진짜 미디어  -> 콘텐츠 엔진만으로 잡힘
                                (\"얼굴은 진짜인데 화법이 사기\" 케이스)
  - 정상 대화 + 위조 미디어  -> 미디어 엔진만으로 잡힘
                                (\"화법은 자연스러운데 얼굴이 가짜\" 케이스)
  - 사기 화법 + 위조 미디어  -> 교차 보너스가 붙어 최고점

  두 버전의 차이: 버전A(PDF)는 둘 다 70 초과일 때만 +15를 한 번에 주고,
  버전B(DOCX)는 두 점수의 곱에 비례해 부드럽게 준다. 경계 근처에서
  버전A는 점수가 계단처럼 뛰고 버전B는 매끄럽게 오른다.
  -> 버전B(DOCX)로 확정했다. 정확도는 사실상 동률인데 버전A는 임계값에서
     점수가 +15.2 계단으로 튀어 "확률적 표현" 원칙과 충돌한다.
     근거: scripts/decide_scoring.py, docs/scoring_decision.json
""")


if __name__ == "__main__":
    main()
