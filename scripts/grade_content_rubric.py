"""
콘텐츠 분류기 카테고리별 정밀 채점 (기대 점수대 vs 실제 점수)
담당: 강동연

validate_content_risk.py는 "대화 전체가 사기냐 아니냐"만 본다(label 기반,
정탐률/오탐률). 그 아래의 "기법별 회수율" 표도 techniques(사람이 붙인 이진
참고 라벨 — 있다/없다)에 있는 기법만 훑는다.

이 스크립트는 한 단계 더 들어간다. 시나리오마다 **8개 카테고리 전부**에 대해
사람이 미리 매겨둔 기대 점수대(data_seed/content_test_scenarios.json의
expected_scores)와 실제 채점기 출력을 비교한다. 두 방향의 실수를 모두 잡는다:

  - 과소 채점(under): 있어야 할 신호를 놓침 — 사기를 정상으로 오판하는 원인
  - 과다 채점(over):  없어야 할 신호에 헛불 — 정상을 사기로 오판하는 원인
    (normal_05가 정확히 이 사례다: "비밀번호를 절대 요구하지 않는다"는
     부정문에서 "비밀번호"라는 단어만 보고 credentials를 높게 채점하면 오탐)

밴드(none/weak/clear/definitive)는 content_risk.ScoreBand — llm_classifier.py의
SYSTEM_PROMPT 채점 기준과 같은 척도다. 그래서 이 스크립트 결과는 "LLM이 프롬프트가
지시한 대로 채점하는가"를 직접 확인하는 시험이 된다.

실행:
    .venv\\Scripts\\python.exe scripts\\grade_content_rubric.py
    .venv\\Scripts\\python.exe scripts\\grade_content_rubric.py --backend llm
"""

import argparse
import json
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parent / "src"))

from content_analysis import llm_classifier  # noqa: E402
from content_analysis.content_risk import (  # noqa: E402
    CATEGORY_LABELS_KO,
    SocialEngineeringCategory,
    band_range_label,
    classify_by_keywords,
    score_in_band,
)

PROJECT_ROOT = SCRIPT_DIR.parent
DATASET_PATH = PROJECT_ROOT / "data_seed" / "content_test_scenarios.json"
REPORT_PATH = PROJECT_ROOT / "docs" / "content_rubric_report.json"

CATEGORIES = [c.value for c in SocialEngineeringCategory]


def load_scenarios(path: Path) -> list:
    data = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for s in data["scenarios"]:
        if "expected_scores" not in s:
            # 채점표가 없는 시나리오는 건너뛴다(경고만). 새 시나리오를 추가했는데
            # expected_scores를 깜빡했을 때 조용히 스킵되지 않도록 여기서 알린다.
            print(f"[경고] {s['id']}: expected_scores가 없어 건너뜁니다.", file=sys.stderr)
            continue
        out.append({
            "id": s["id"],
            "title": s.get("title", ""),
            "label": s["label"],
            "expected": s["expected_scores"],
            "text": "\n".join(f"{t['speaker']}: {t['text']}" for t in s["turns"]),
        })
    return out


def score_scenario(backend: str, text: str) -> dict:
    """카테고리별 실제 점수(0~100)를 dict로 반환."""
    if backend == "keyword":
        br = classify_by_keywords(text)
    else:
        br = llm_classifier.classify_segment(text, fallback=False)
    return br.category_scores


def grade(backend: str, scenarios: list) -> dict:
    print(f"\n{'=' * 70}")
    print(f" 백엔드: {backend}")
    print(f"{'=' * 70}")

    if backend == "llm" and not llm_classifier.is_available():
        print("  [건너뜀] LLM API 키가 없습니다.")
        return {"backend": backend, "note": "API 키 없음 — 실행하지 않음"}
    if backend == "llm":
        print(f"  엔진: {llm_classifier.active_provider_label()}")

    # per_category[cat] = {"hit": n, "total": n, "mismatches": [...]}
    per_category = {c: {"hit": 0, "total": 0} for c in CATEGORIES}
    scenario_rows = []
    t0 = time.time()

    for i, s in enumerate(scenarios, 1):
        print(f"  [{i:>2}/{len(scenarios)}] {s['id']:10} {s['title'][:26]:26}", end=" ")
        try:
            actual = score_scenario(backend, s["text"])
        except Exception as exc:
            print(f"실패: {type(exc).__name__}: {exc}")
            scenario_rows.append({"id": s["id"], "error": f"{type(exc).__name__}: {exc}"})
            continue

        mismatches = []
        for cat in CATEGORIES:
            expected_band = s["expected"][cat]["band"]
            actual_score = actual.get(cat, 0.0)
            per_category[cat]["total"] += 1
            if score_in_band(actual_score, expected_band):
                per_category[cat]["hit"] += 1
            else:
                mismatches.append({
                    "category": cat,
                    "category_label": CATEGORY_LABELS_KO[SocialEngineeringCategory(cat)],
                    "expected_band": band_range_label(expected_band),
                    "actual_score": round(actual_score, 2),
                    "evidence": s["expected"][cat].get("evidence"),
                })

        n_match = len(CATEGORIES) - len(mismatches)
        print(f"{n_match}/{len(CATEGORIES)} 밴드 일치")
        scenario_rows.append({
            "id": s["id"], "label": s["label"],
            "band_match": n_match, "band_total": len(CATEGORIES),
            "mismatches": mismatches,
        })

    elapsed = time.time() - t0
    n_scored = sum(1 for r in scenario_rows if "error" not in r)
    print(f"\n  분석 완료: {n_scored}/{len(scenarios)}개, {elapsed:.0f}초")

    print("\n  --- 카테고리별 밴드 일치율 ---")
    for cat in CATEGORIES:
        h, t = per_category[cat]["hit"], per_category[cat]["total"]
        rate = h / t if t else 0.0
        label = CATEGORY_LABELS_KO[SocialEngineeringCategory(cat)]
        print(f"    {label:16} {rate:6.1%}   ({h}/{t})")

    total_hit = sum(v["hit"] for v in per_category.values())
    total_all = sum(v["total"] for v in per_category.values())
    overall = total_hit / total_all if total_all else 0.0
    print(f"\n  전체 밴드 일치율: {overall:.1%}  ({total_hit}/{total_all})")

    # 과소/과다 채점 방향 집계 — 어느 쪽으로 실수하는지가 더 중요한 정보다.
    under_count = over_count = 0
    for row in scenario_rows:
        for m in row.get("mismatches", []):
            lo_str, hi_str = m["expected_band"].split("(")[1].rstrip(")").split("~")
            lo, hi = int(lo_str), int(hi_str)
            if m["actual_score"] < lo:
                under_count += 1  # 있어야 할 신호를 놓침 — 사기→정상 오판 원인
            elif m["actual_score"] > hi:
                over_count += 1   # 없어야 할 신호에 헛불 — 정상→사기 오판 원인
    print(f"  과소 채점(놓침, 사기→정상 오판 원인): {under_count}건")
    print(f"  과다 채점(헛불, 정상→사기 오판 원인): {over_count}건")

    return {
        "backend": backend,
        "engine": llm_classifier.active_provider_label() if backend == "llm" else "키워드 규칙",
        "n_scenarios": len(scenarios),
        "n_scored": n_scored,
        "elapsed_sec": round(elapsed, 1),
        "per_category": {
            c: {"hit": v["hit"], "total": v["total"],
                "rate": round(v["hit"] / v["total"], 4) if v["total"] else None}
            for c, v in per_category.items()
        },
        "overall_rate": round(overall, 4),
        "under_scored": under_count,
        "over_scored": over_count,
        "scenarios": scenario_rows,
    }


def main():
    parser = argparse.ArgumentParser(description="콘텐츠 분류기 카테고리별 정밀 채점")
    parser.add_argument("--dataset", default=str(DATASET_PATH))
    parser.add_argument("--backend", default="keyword", choices=["keyword", "llm", "both"])
    parser.add_argument("--out", default=str(REPORT_PATH))
    args = parser.parse_args()

    dataset = Path(args.dataset)
    if not dataset.exists():
        print(f"[에러] 검증셋이 없습니다: {dataset}", file=sys.stderr)
        sys.exit(1)

    scenarios = load_scenarios(dataset)
    print(f"채점표 {len(scenarios)}건 × 8개 카테고리 = {len(scenarios) * 8}개 항목")
    print(f"출처: {dataset}")

    backends = ["keyword", "llm"] if args.backend == "both" else [args.backend]
    report = {
        "dataset": str(dataset),
        "n_scenarios": len(scenarios),
        "backends": {b: grade(b, scenarios) for b in backends},
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n상세 결과 저장: {out}")
    print("\n주의: expected_scores는 사람이 대사를 읽고 매긴 주관적 판단이다.")
    print("      틀렸다고 생각되면 data_seed/content_test_scenarios.json을 직접 고칠 것.")


if __name__ == "__main__":
    main()
