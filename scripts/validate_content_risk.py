"""
콘텐츠 분석(8대 사회공학 기법 분류) 성능 실측
담당: 강동연

영상(validate_detector.py)·음성(validate_audio_spoof.py)과 **같은 지표**를 쓴다.
지금까지 실측이 미디어 두 엔진에만 있었는데, 기획서 [품질 검증 계획]은 콘텐츠
쪽에도 정탐률/오탐률을 요구한다. 이 스크립트가 그 공백을 메운다.

미디어 쪽과 다른 점:
  - 입력이 파일이 아니라 라벨링된 대화(data_seed/content_test_scenarios.json)다.
  - "가짜(fake)"에 해당하는 것이 label == "fraud"다. 지표 함수가 real/fake를
    기대하므로 fraud -> fake, normal -> real 로 매핑해서 넘긴다.

무엇을 재는가:
  대화 전체를 한 덩어리로 넣어 content_risk(0~100)를 뽑고, 임계값을 넘으면
  '사기로 판정'했다고 본다. 파이프라인 본체는 STT 세그먼트 단위로 부르지만,
  여기서는 STT 없이 텍스트만 있으므로 화자 표기를 붙여 통째로 넣는다.
  (분류 로직 자체의 성능을 재는 것이지 파이프라인 전체를 재는 게 아니다)

백엔드:
  --backend keyword  키워드 규칙 (기본값, API 키 불필요 — 항상 재현 가능한 기준선)
  --backend llm      LLM 분류 (ANTHROPIC_API_KEY / GEMINI_API_KEY / OPENAI_API_KEY 중 하나 필요.
                      DUALGUARD_LLM_PROVIDER로 백엔드 고정 가능)
  --backend both     둘 다 돌려 비교. LLM이 규칙 대비 얼마나 나은지가 이 표의 핵심이다.

실행:
    .venv\\Scripts\\python.exe scripts\\validate_content_risk.py
    .venv\\Scripts\\python.exe scripts\\validate_content_risk.py --backend both
"""

import argparse
import json
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parent / "src"))

from _console import setup_console  # noqa: E402

setup_console()  # cp949 콘솔에서 유니코드 출력 중 죽는 문제 방지 (_console.py)

# validate_detector가 아니라 _metrics에서 직접 가져온다.
# validate_detector는 모듈 최상단에서 cv2/torch를 끌어오므로, 콘텐츠 검증만
# 돌리려는 사람에게 미디어 스택 설치를 강요하게 된다.
from _metrics import confusion, best_threshold, separation, fmt_pct  # noqa: E402

from content_analysis import llm_classifier  # noqa: E402
from content_analysis.content_risk import (  # noqa: E402
    CATEGORY_LABELS_KO,
    SocialEngineeringCategory,
    classify_by_keywords,
    classify_offline,
)

PROJECT_ROOT = SCRIPT_DIR.parent
DATASET_PATH = PROJECT_ROOT / "data_seed" / "content_test_scenarios.json"
REPORT_PATH = PROJECT_ROOT / "docs" / "validation_report_content.json"

# 콘텐츠 위험도(content_risk) 이진 판정 기준선. 이 스크립트가 fraud/normal 정확도를
# 재려고 쓰는 값일 뿐, pipeline.py의 LEVEL_THRESHOLDS(55/30, 신호등 3단계)와는
# 무관하다 — 섞지 말 것.
DEFAULT_LLM_THRESHOLD = 50.0

# OpenAI(gpt-4o-mini) 전용 기준선. 실측(2026-09-02, 21건 data_seed/content_test_scenarios.json,
# docs/validation_report_content_openai.json)에서 threshold 50은 recall 100%지만
# FPR 30%(정상 3건 오탐: boundary_normal_01/02/04)로 offline(90.5%)보다 나빴다.
# 62.5에서는 recall 100% 유지하며 FPR 10%/accuracy 95.2%로 offline을 앞선다.
# anthropic/gemini/ollama/offline은 그대로 DEFAULT_LLM_THRESHOLD(50)를 쓴다 —
# 이 값들을 재검증 없이 62.5로 같이 옮기지 말 것.
OPENAI_LLM_THRESHOLD = 62.5


def load_scenarios(path: Path) -> list:
    data = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for s in data["scenarios"]:
        out.append({
            "id": s["id"],
            "title": s.get("title", ""),
            "channel": s.get("channel", "call"),
            # 지표 함수가 real/fake를 기대한다 (미디어 쪽과 용어 통일)
            "label": "fake" if s["label"] == "fraud" else "real",
            "techniques": s.get("techniques", []),
            "text": "\n".join(f"{t['speaker']}: {t['text']}" for t in s["turns"]),
        })
    return out


def run_backend(backend: str, scenarios: list) -> dict:
    print(f"\n{'=' * 62}")
    print(f" 백엔드: {backend}")
    print(f"{'=' * 62}")

    if backend == "llm" and not llm_classifier.is_available():
        print("  [건너뜀] LLM API 키가 없습니다.")
        print("           Claude → ANTHROPIC_API_KEY 또는 `ant auth login`")
        print("           Gemini → GEMINI_API_KEY (+ pip install google-genai)")
        return {"backend": backend, "note": "API 키 없음 — 실행하지 않음"}

    default_threshold = DEFAULT_LLM_THRESHOLD
    if backend == "llm":
        print(f"  엔진: {llm_classifier.active_provider_label()}")
        if llm_classifier.get_shared_classifier().provider == "openai":
            default_threshold = OPENAI_LLM_THRESHOLD

    results = []
    t0 = time.time()
    for i, s in enumerate(scenarios, 1):
        print(f"  [{i:>2}/{len(scenarios)}] {s['id']:10} {s['title'][:28]:28}",
              end=" ", flush=True)
        try:
            if backend == "keyword":
                br = classify_by_keywords(s["text"])
            elif backend == "offline":
                br = classify_offline(s["text"])
            else:
                # fallback=False: 키가 죽어서 키워드로 떨어지면 LLM 성능이 아니라
                # 키워드 성능을 재게 된다. 실패는 실패로 남긴다.
                br = llm_classifier.classify_segment(s["text"], fallback=False)
            d = br.as_dict()
            results.append({
                "id": s["id"],
                "label": s["label"],
                "channel": s["channel"],
                "score": d["content_risk"],
                "top_category": d["top_category"],
                "expected_techniques": s["techniques"],
                "category_scores": d["category_scores"],
            })
            print(f"{d['content_risk']:6.2f}  ({d['top_category_label'] or '-'})")
        except Exception as exc:
            print(f"실패: {type(exc).__name__}")
            results.append({"id": s["id"], "label": s["label"], "score": None,
                            "error": f"{type(exc).__name__}: {exc}"})

    elapsed = time.time() - t0
    scored = [r for r in results if r.get("score") is not None]
    failed = [r for r in results if r.get("score") is None]

    print(f"\n  분석 완료: {len(scored)}/{len(scenarios)}개 성공, {elapsed:.0f}초 "
          f"({elapsed / max(1, len(scenarios)):.2f}초/건)")
    if failed:
        print(f"  실패 {len(failed)}개:")
        for f in failed:
            print(f"    {f['id']}: {f.get('error')}")

    if not scored:
        return {"backend": backend, "results": results, "note": "분석 성공한 건 없음"}

    sep = separation(scored)
    print("\n  --- 점수 분포 (content_risk) ---")
    if sep:
        print(f"    정상: 최소 {sep['real_min']:6.2f}  평균 {sep['real_mean']:6.2f}  최대 {sep['real_max']:6.2f}")
        print(f"    사기: 최소 {sep['fake_min']:6.2f}  평균 {sep['fake_mean']:6.2f}  최대 {sep['fake_max']:6.2f}")
        if sep["gap"] > 0:
            print(f"    => 완전 분리 (사기 최소 - 정상 최대 = +{sep['gap']:.2f})")
        else:
            print(f"    => 분포 겹침 (겹치는 폭 {-sep['gap']:.2f}) - threshold만으로는 완전 분리 불가")

    at_default = confusion(scored, default_threshold)
    best = best_threshold(scored)

    print("\n  --- 성능 지표 ---")
    for label, c in ((f"threshold {default_threshold:.2f} (기본)", at_default),
                     (f"threshold {best['threshold']:.2f} (최적)", best)):
        print(f"    [{label}]")
        print(f"      정탐률(Recall)   {fmt_pct(c['recall'])}   사기 {c['tp']}/{c['tp'] + c['fn']}건 탐지")
        print(f"      오탐률(FPR)      {fmt_pct(c['fpr'])}   정상 {c['fp']}/{c['fp'] + c['tn']}건 오판")
        print(f"      정밀도(Precision){fmt_pct(c['precision'])}")
        print(f"      정확도(Accuracy) {fmt_pct(c['accuracy'])}")

    # 어떤 기법을 못 잡는지 — 미디어 쪽 '조작 기법별 성능'에 대응하는 표.
    # 실전에서는 전체 정확도보다 "어떤 수법에 약한가"가 더 중요하다.
    print("\n  --- 기법별 회수율 (threshold 50, 사기 대화만) ---")
    for cat in SocialEngineeringCategory:
        rows = [r for r in scored
                if r["label"] == "fake" and cat.value in (r.get("expected_techniques") or [])]
        if not rows:
            continue
        # 사람이 붙인 라벨을 모델이 그 카테고리로 실제로 집어냈는가
        hit = sum(1 for r in rows if (r["category_scores"] or {}).get(cat.value, 0) >= 50)
        avg = sum((r["category_scores"] or {}).get(cat.value, 0) for r in rows) / len(rows)
        print(f"    {CATEGORY_LABELS_KO[cat]:16} 포착 {hit}/{len(rows)}   평균점수 {avg:6.2f}")

    return {
        "backend": backend,
        "engine": llm_classifier.active_provider_label() if backend == "llm" else "키워드 규칙",
        "n_scenarios": len(scenarios),
        "n_scored": len(scored),
        "elapsed_sec": round(elapsed, 1),
        "separation": sep,
        "default_threshold": default_threshold,
        "at_default_threshold": at_default,
        "best_threshold": best,
        "results": results,
    }


def main():
    parser = argparse.ArgumentParser(description="콘텐츠 분석(8대 기법) 성능 실측")
    parser.add_argument("--dataset", default=str(DATASET_PATH))
    parser.add_argument("--backend", default="offline",
                        choices=["keyword", "offline", "llm", "both", "all"])
    parser.add_argument("--out", default=str(REPORT_PATH))
    args = parser.parse_args()

    dataset = Path(args.dataset)
    if not dataset.exists():
        print(f"[에러] 검증셋이 없습니다: {dataset}", file=sys.stderr)
        sys.exit(1)

    scenarios = load_scenarios(dataset)
    n_fake = sum(1 for s in scenarios if s["label"] == "fake")
    print(f"검증셋 {len(scenarios)}건 (사기 {n_fake}, 정상 {len(scenarios) - n_fake})")
    print(f"출처: {dataset}")

    backends = {
        "both": ["offline", "llm"],
        "all": ["keyword", "offline", "llm"],
    }.get(args.backend, [args.backend])
    report = {
        "dataset": str(dataset),
        "n_scenarios": len(scenarios),
        "n_fraud": n_fake,
        "n_normal": len(scenarios) - n_fake,
        "backends": {b: run_backend(b, scenarios) for b in backends},
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n상세 결과 저장: {out}")

    print(f"\n주의: 자체 구성 시나리오 {len(scenarios)}건이라 표본이 작다. 발표에 인용할 때는")
    print("      건수와 '팀이 직접 작성한 데이터셋'임을 반드시 함께 밝힐 것.")


if __name__ == "__main__":
    main()
