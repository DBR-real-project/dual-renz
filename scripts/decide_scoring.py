"""
통합 스코어링 설정을 실측으로 결정한다
담당: 이상원

README의 "팀이 정해야 할 것"에 남아 있던 항목들을 감이 아니라 데이터로 정하기 위한
스크립트다. 결정 대상:

  1. 통합 공식        — 기획서 PDF 버전(threshold_bonus) vs DOCX 버전(multiplicative_bonus)
  2. 콘텐츠/미디어 가중치 — 현재 0.5 / 0.5
  3. 신호등 경계       — 현재 높음 70 / 중간 40

## 합동 검증셋을 어떻게 만들었나

콘텐츠와 미디어가 **동시에** 라벨링된 통화 데이터는 없다(있으면 그게 제일 좋다).
대신 각각 실측된 점수를 교차해 만든다:

    콘텐츠 21건 (사기 11 / 정상 10)   docs/validation_report_content.json
      ×
    음성  50건 (진짜 20 / 합성 30)    docs/validation_report_audio.json
      = 1,050쌍

**중요: 이건 실제 통화 1,050건이 아니다.** 실측 점수 두 축을 교차한 합성 격자이고,
콘텐츠와 매체 위조가 독립이라고 가정한 것이다. 절대적인 정확도 수치를 발표에
쓰면 안 되고, **설정 A와 B 중 무엇이 나은지 고르는 용도**로만 쓴다.

## 정답 라벨 (기획서 교차검증 논리)

    콘텐츠 사기                  -> 높음   (화법이 사기면 목소리가 진짜여도 위험)
    콘텐츠 정상 + 매체 합성       -> 중간   (내용은 정상인데 목소리가 합성)
    콘텐츠 정상 + 매체 진짜       -> 낮음

## 무엇을 최소화하는가

정확도만 보면 안 된다. 두 오류의 비용이 전혀 다르다:

    놓침(사기를 '낮음'으로)  — 사용자가 그대로 당한다. 제품 실패.
    헛경보(정상을 '높음'으로) — 사용자가 제품을 버린다. 역시 제품 실패.

그래서 **놓침을 먼저 0에 가깝게 만들고, 그다음 헛경보를 줄이고, 마지막에 정확도**
순으로 고른다.

실행:
    .venv\\Scripts\\python.exe scripts/decide_scoring.py
    .venv\\Scripts\\python.exe scripts/decide_scoring.py --top 15
"""

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parent / "src"))

from _console import setup_console  # noqa: E402

setup_console()  # cp949 콘솔에서 유니코드 출력 중 죽는 문제 방지 (_console.py)

from scoring.fraud_risk_score import ScoringStrategy, compute_fraud_risk_score  # noqa: E402

PROJECT_ROOT = SCRIPT_DIR.parent
CONTENT_REPORT = PROJECT_ROOT / "docs" / "validation_report_content.json"
AUDIO_REPORT = PROJECT_ROOT / "docs" / "validation_report_audio.json"
OUT = PROJECT_ROOT / "docs" / "scoring_decision.json"

LEVELS = ("낮음", "중간", "높음")


def load_content():
    if not CONTENT_REPORT.exists():
        raise SystemExit(f"없음: {CONTENT_REPORT}\n"
                         "  .venv\\Scripts\\python.exe scripts\\validate_content_risk.py")
    d = json.loads(CONTENT_REPORT.read_text(encoding="utf-8"))
    backend = d["backends"].get("offline") or list(d["backends"].values())[0]
    return [(float(r["score"]), r["label"] == "fake")
            for r in backend["results"] if r.get("score") is not None]


def load_media():
    if not AUDIO_REPORT.exists():
        raise SystemExit(f"없음: {AUDIO_REPORT}\n"
                         "  .venv\\Scripts\\python.exe scripts\\validate_audio_spoof.py")
    d = json.loads(AUDIO_REPORT.read_text(encoding="utf-8"))
    return [(float(r["score"]), r["label"] == "fake")
            for r in d["results"] if r.get("score") is not None]


def expected_level(content_is_fraud: bool, media_is_fake: bool) -> str:
    if content_is_fraud:
        return "높음"
    return "중간" if media_is_fake else "낮음"


def level_of(score: float, high: float, mid: float) -> str:
    if score >= high:
        return "높음"
    if score >= mid:
        return "중간"
    return "낮음"


def evaluate(pairs, strategy, weight, high, mid):
    """
    pairs: [(content, media, expected)]
    반환: 놓침 / 헛경보 / 정확도 / 등급 혼동
    """
    correct = 0
    missed = 0        # 사기인데 '낮음'
    under = 0         # 사기인데 '중간' (경고는 했지만 등급이 낮음)
    false_alarm = 0   # 정상+진짜인데 '높음'
    over = 0          # 정상+진짜인데 '중간'

    for content, media, exp in pairs:
        s = compute_fraud_risk_score(
            content, media, strategy=strategy,
            content_weight=weight, media_weight=1.0 - weight,
        ).fraud_risk_score
        got = level_of(s, high, mid)
        if got == exp:
            correct += 1
        if exp == "높음":
            if got == "낮음":
                missed += 1
            elif got == "중간":
                under += 1
        elif exp == "낮음":
            if got == "높음":
                false_alarm += 1
            elif got == "중간":
                over += 1

    n = len(pairs)
    return {
        "strategy": strategy.value,
        "content_weight": round(weight, 2),
        "high": high,
        "mid": mid,
        "accuracy": round(100.0 * correct / n, 2),
        "missed": missed,
        "under": under,
        "false_alarm": false_alarm,
        "over": over,
    }


def main():
    parser = argparse.ArgumentParser(description="통합 스코어링 설정 실측 결정")
    parser.add_argument("--top", type=int, default=10, help="상위 몇 개를 보여줄지")
    args = parser.parse_args()

    content = load_content()
    media = load_media()

    pairs = [(c, m, expected_level(cf, mf))
             for c, cf in content for m, mf in media]
    n_high = sum(1 for *_, e in pairs if e == "높음")
    n_mid = sum(1 for *_, e in pairs if e == "중간")
    n_low = sum(1 for *_, e in pairs if e == "낮음")

    print(f"합동 격자 {len(pairs)}쌍 = 콘텐츠 {len(content)}건 × 미디어 {len(media)}건")
    print(f"  기대 등급  높음 {n_high} / 중간 {n_mid} / 낮음 {n_low}")
    print("  ※ 실제 통화가 아니라 실측 점수 두 축의 교차 격자다. "
          "설정 비교용이지 절대 성능이 아니다.\n")

    rows = []
    for strategy in ScoringStrategy:
        for w10 in range(30, 75, 5):
            weight = w10 / 100.0
            for high in range(55, 90, 5):
                for mid in range(25, min(high, 55), 5):
                    rows.append(evaluate(pairs, strategy, weight, float(high), float(mid)))

    # 놓침 -> 헛경보 -> 정확도 순으로 고른다 (docstring "무엇을 최소화하는가" 참고)
    rows.sort(key=lambda r: (r["missed"], r["false_alarm"], -r["accuracy"],
                             r["under"], r["over"]))

    print(f"--- 상위 {args.top}개 ({len(rows)}개 조합 중) ---")
    print(f"{'공식':<22}{'콘텐츠가중':>10}{'높음':>6}{'중간':>6}"
          f"{'정확도':>9}{'놓침':>7}{'헛경보':>8}")
    print("-" * 70)
    for r in rows[:args.top]:
        print(f"{r['strategy']:<22}{r['content_weight']:>10.2f}{r['high']:>6.0f}"
              f"{r['mid']:>6.0f}{r['accuracy']:>8.1f}%{r['missed']:>7}{r['false_alarm']:>8}")

    best = rows[0]

    # 현재 기본값과 비교
    current = evaluate(pairs, ScoringStrategy.MULTIPLICATIVE_BONUS, 0.5, 70.0, 40.0)
    print("\n--- 현재 기본값 vs 실측 최적 ---")
    print(f"{'':12}{'공식':<22}{'가중':>6}{'높음':>6}{'중간':>6}{'정확도':>9}{'놓침':>7}{'헛경보':>8}")
    for label, r in (("현재", current), ("최적", best)):
        print(f"{label:12}{r['strategy']:<22}{r['content_weight']:>6.2f}{r['high']:>6.0f}"
              f"{r['mid']:>6.0f}{r['accuracy']:>8.1f}%{r['missed']:>7}{r['false_alarm']:>8}")

    # 공식만 따로 비교 (다른 조건 고정)
    print("\n--- 공식만 비교 (가중 0.5, 경계는 각 공식의 최적) ---")
    for strategy in ScoringStrategy:
        sub = [r for r in rows if r["strategy"] == strategy.value
               and abs(r["content_weight"] - 0.5) < 1e-9]
        b = sub[0]
        print(f"  {strategy.value:<22} 높음{b['high']:>4.0f} 중간{b['mid']:>4.0f}  "
              f"정확도 {b['accuracy']:>5.1f}%  놓침 {b['missed']}  헛경보 {b['false_alarm']}")

    # --- 연속성 점검 ---
    # 정확도만 보고 공식을 고르면 안 된다. threshold_bonus는 두 값이 모두 임계값을
    # 넘는 순간 +15를 한 번에 얹는다. 입력이 0.2 움직였는데 점수가 15점 튀면
    # 기획서의 "확률적 표현으로 과신 방지" 원칙과 정면으로 충돌하고,
    # 구간 타임라인 그래프에도 설명 못 할 계단이 생긴다.
    print("\n--- 연속성 점검 (입력을 아주 조금 바꿨을 때 점수가 튀는가) ---")
    jumps = {}
    for strategy in ScoringStrategy:
        lo = compute_fraud_risk_score(69.9, 69.9, strategy=strategy,
                                      content_weight=0.65, media_weight=0.35)
        hi = compute_fraud_risk_score(70.1, 70.1, strategy=strategy,
                                      content_weight=0.65, media_weight=0.35)
        jump = hi.fraud_risk_score - lo.fraud_risk_score
        jumps[strategy.value] = round(jump, 2)
        verdict = "계단 발생" if jump > 1.0 else "매끄러움"
        print(f"  {strategy.value:<22} 69.9/69.9 -> {lo.fraud_risk_score:6.2f}, "
              f"70.1/70.1 -> {hi.fraud_risk_score:6.2f}   차이 {jump:+6.2f}  {verdict}")

    OUT.write_text(json.dumps({
        "_설명": "실측 점수 교차 격자로 통합 스코어링 설정을 고른 기록. "
                 "절대 성능이 아니라 설정 비교용. scripts/decide_scoring.py 참고.",
        "n_pairs": len(pairs),
        "n_content": len(content),
        "n_media": len(media),
        "expected_counts": {"높음": n_high, "중간": n_mid, "낮음": n_low},
        "current_default": current,
        "best": best,
        "continuity_jump_at_threshold": jumps,
        "top": rows[:args.top],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n저장: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
