"""
통화 파일 전체 분석 CLI
담당: 이상원

파일 하나를 넣으면 기획서의 전체 흐름을 그대로 돌린다:
  STT -> 8대 사회공학 기법 분류(+RAG) / AASIST 음성 스푸핑 / 딥페이크 탐지
  -> 구간별 통합 Fraud Risk Score -> 신호등 등급 + 3단계 액션 플랜

실행:
    .venv\\Scripts\\python.exe scripts/analyze_call.py --input data/korean_calls/scam_call.wav
    .venv\\Scripts\\python.exe scripts/analyze_call.py --input <영상> --json out.json
    .venv\\Scripts\\python.exe scripts/analyze_call.py --input <파일> --no-llm --no-rag
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from media_detection.deepfake_detector import FrameAggregation  # noqa: E402
from media_detection.media_risk_dummy import MediaCombineMode  # noqa: E402
from orchestration.pipeline import analyze  # noqa: E402
from scoring.fraud_risk_score import ScoringStrategy  # noqa: E402

BAR_WIDTH = 24


def bar(value: float, width: int = BAR_WIDTH) -> str:
    filled = int(round(width * max(0.0, min(100.0, value)) / 100))
    return "█" * filled + "·" * (width - filled)


def main():
    parser = argparse.ArgumentParser(description="통화 파일 사기 위험도 분석")
    parser.add_argument("--input", required=True, help="통화 오디오 또는 화상통화 영상")
    parser.add_argument("--json", default=None, help="결과 JSON 저장 경로")
    parser.add_argument("--stt-model", default="small",
                        choices=["tiny", "base", "small", "medium"])
    parser.add_argument("--no-llm", action="store_true", help="LLM 분류 끄고 키워드 규칙만")
    parser.add_argument("--no-rag", action="store_true", help="사례 검색 끄기")
    parser.add_argument("--strategy", default=ScoringStrategy.MULTIPLICATIVE_BONUS.value,
                        choices=[s.value for s in ScoringStrategy],
                        help="통합 공식 (기획서 2개 버전)")
    parser.add_argument("--combine", default=MediaCombineMode.MAX.value,
                        choices=[m.value for m in MediaCombineMode],
                        help="오디오/영상 결합 방식")
    parser.add_argument("--quiet", action="store_true", help="진행률 숨기기")
    args = parser.parse_args()

    def progress(stage, ratio, message):
        if not args.quiet:
            print(f"  [{ratio:5.0%}] {stage:<8} {message}", flush=True)

    try:
        report = analyze(
            args.input,
            strategy=ScoringStrategy(args.strategy),
            combine_mode=MediaCombineMode(args.combine),
            aggregation=FrameAggregation.TOPK_MEAN,
            use_llm=not args.no_llm,
            use_rag=not args.no_rag,
            stt_model=args.stt_model,
            progress=progress,
        )
    except Exception as exc:
        print(f"[에러] {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)

    d = report.as_dict()

    print()
    print("=" * 72)
    print(f" {d['file_name']}   {d['duration']}초   분석 소요 {d['elapsed_sec']}초")
    print("=" * 72)

    plan = d["action_plan"]
    marker = {"높음": "[!!]", "중간": "[! ]", "낮음": "[ok]"}[d["overall_level"]]
    print(f"\n {marker} Fraud Risk Score  {d['overall_score']:.1f} / 100   "
          f"위험도 {d['overall_level']}")
    print(f"      {bar(d['overall_score'])}")
    print(f"      {plan['headline']}")

    print(f"\n 콘텐츠 위험도 {d['content_risk']:5.1f}  {bar(d['content_risk'], 18)}")
    print(f" 미디어 위험도 {d['media_risk']:5.1f}  {bar(d['media_risk'], 18)}")

    print("\n--- 사용된 엔진 ---")
    for k, v in d["engines"].items():
        print(f"   {k:<8} {v}")

    if d["warnings"]:
        print("\n--- 주의 ---")
        for w in d["warnings"]:
            print(f"   ! {w}")

    if d["top_categories"]:
        print("\n--- 탐지된 사회공학 기법 (통화 전체 최고점) ---")
        for c in d["top_categories"]:
            print(f"   {c['label']:<18} {c['score']:5.1f}  {bar(c['score'], 16)}")

    print("\n--- 구간별 타임라인 ---")
    print(f"   {'시간':<13} {'콘텐츠':>6} {'미디어':>6} {'FRS':>6}  등급  발화")
    for s in d["segments"]:
        t = f"{s['start']:.0f}-{s['end']:.0f}s"
        lv = {"높음": "!!", "중간": "! ", "낮음": "  "}[s["level"]]
        print(f"   {t:<13} {s['content_risk']:6.1f} {s['media_risk']:6.1f} "
              f"{s['fraud_risk_score']:6.1f}  {lv}   {s['transcript'][:44]}")
        detail = s.get("content_detail") or {}
        if detail.get("top_category_label") and s["content_risk"] > 0:
            print(f"                 └ {detail['top_category_label']}", end="")
            terms = detail.get("matched_terms") or {}
            flat = [t for v in terms.values() for t in v][:4]
            if flat:
                print(f" — {', '.join(flat)}", end="")
            print()
        for m in s.get("rag_matches", [])[:1]:
            print(f"                 └ 유사 사례({m['similarity']:.2f}): {m['title']}")

    print("\n--- 권장 조치 ---")
    for i, a in enumerate(plan["actions"], 1):
        print(f"   {i}. {a}")
    for link in plan["links"]:
        print(f"      · {link['label']}  {link['url']}")

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n결과 JSON 저장: {out}")


if __name__ == "__main__":
    main()
