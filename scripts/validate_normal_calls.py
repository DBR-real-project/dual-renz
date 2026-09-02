"""
정상 대화에서의 헛경보율 실측 — 마지막 남은 검증 공백
담당: 이상원

## 왜 이게 마지막 조각인가

지금까지 잰 것:
  - 사기 통화 정탐률   → 금감원 실제 녹취 5/5 (validate_real_calls.py)
  - 문장 단위 오탐률   → 공개 코퍼스 457문장 1.53% (validate_content_fpr.py)

못 잰 것:
  - **음성이 붙은 정상 대화 한 통을 통째로 넣었을 때 헛경보가 나는가**

정상 통화 녹취가 공개된 게 없어 비워뒀던 칸이다. 멘토링에서 "공공 데이터를
찾아보라"는 조언을 받고, KsponSpeech(한국어 자유대화 음성)로 대체했다.
낭독체가 아니라 **실제 사람들이 자유롭게 나눈 대화**라 통화에 가깝다.

## 무엇을 재나

각 대화를 전체 파이프라인에 넣고 **'낮음'이 아닌 등급이 나오면 헛경보**로 센다.
이 데이터는 전부 일상 대화이므로 사기 신호가 있을 리 없다.

세 축을 나눠서 본다. 하나로 뭉뚱그리면 어느 엔진이 문제인지 안 보인다:
  - 콘텐츠 위험도 (화법 분류)
  - 미디어 위험도 (음성 위조 판별)
  - 최종 등급

## 한계를 미리 밝힌다

- KsponSpeech는 **전화 통화가 아니라 대면 자유대화**다. 전화망 대역·코덱은 없다.
  그래도 낭독체보다는 실제 통화에 훨씬 가깝다.
- 데이터 이용 조건은 `scripts/fetch_korean_conversation.py` docstring 참고.

실행:
    .venv\\Scripts\\python.exe scripts/fetch_korean_conversation.py
    .venv\\Scripts\\python.exe scripts/validate_normal_calls.py
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

PROJECT_ROOT = SCRIPT_DIR.parent
SAMPLE_DIR = PROJECT_ROOT / "data" / "korean_conversation"
REPORT = PROJECT_ROOT / "docs" / "validation_report_normal_calls.json"


def main():
    parser = argparse.ArgumentParser(description="정상 대화 헛경보율 실측")
    parser.add_argument("--sample-dir", default=str(SAMPLE_DIR))
    parser.add_argument("--limit", type=int, default=0, help="개수 제한 (0=전부)")
    parser.add_argument("--out", default=str(REPORT))
    args = parser.parse_args()

    from orchestration.pipeline import LEVEL_THRESHOLDS, analyze

    sample_dir = Path(args.sample_dir)
    files = sorted(sample_dir.glob("conv_*.wav"))
    if args.limit:
        files = files[:args.limit]
    if not files:
        print(f"샘플이 없습니다: {sample_dir}")
        print("  .venv\\Scripts\\python.exe scripts\\fetch_korean_conversation.py")
        return 1

    print(f"정상 대화 {len(files)}건 (KsponSpeech 한국어 자유대화)")
    print(f"신호등 경계: 높음 {LEVEL_THRESHOLDS['높음']:.0f} / "
          f"중간 {LEVEL_THRESHOLDS['중간']:.0f}")
    print("  이 데이터는 전부 일상 대화다 — '낮음'이 아니면 전부 헛경보다.\n")

    rows = []
    t0 = time.time()
    for i, path in enumerate(files, 1):
        try:
            rep = analyze(str(path), progress=None).as_dict()
        except Exception as exc:
            print(f"[{i:>2}/{len(files)}] {path.name}  실패: {type(exc).__name__}")
            rows.append({"file": path.name, "error": f"{type(exc).__name__}: {exc}"})
            continue
        mark = "" if rep["overall_level"] == "낮음" else "   <- 헛경보"
        rows.append({
            "file": path.name,
            "overall_score": rep["overall_score"],
            "overall_level": rep["overall_level"],
            "content_risk": rep["content_risk"],
            "media_risk": rep["media_risk"],
            "transcript": " ".join(s["transcript"] for s in rep["segments"])[:120],
        })
        print(f"[{i:>2}/{len(files)}] {rep['overall_score']:>5.1f} {rep['overall_level']:<3} "
              f"(콘텐츠 {rep['content_risk']:>5.1f} / 미디어 {rep['media_risk']:>5.1f}){mark}",
              flush=True)

    ok = [r for r in rows if "error" not in r]
    if not ok:
        print("\n분석 성공한 파일이 없습니다.")
        return 1

    elapsed = time.time() - t0
    n = len(ok)
    fp_any = [r for r in ok if r["overall_level"] != "낮음"]
    fp_high = [r for r in ok if r["overall_level"] == "높음"]
    content_fp = [r for r in ok if r["content_risk"] >= 50]
    media_fp = [r for r in ok if r["media_risk"] >= 50]

    print(f"\n--- 결과 ({n}건, {elapsed:.0f}초) ---")
    print(f"  최종 등급 헛경보('중간' 이상)  {len(fp_any)}/{n} = {100.0*len(fp_any)/n:>5.1f}%")
    print(f"  그중 '높음'                   {len(fp_high)}/{n} = {100.0*len(fp_high)/n:>5.1f}%")
    print(f"\n  엔진별로 나눠 보면 (임계값 50)")
    print(f"    콘텐츠(화법)  {len(content_fp)}/{n} = {100.0*len(content_fp)/n:>5.1f}%")
    print(f"    미디어(음성)  {len(media_fp)}/{n} = {100.0*len(media_fp)/n:>5.1f}%")

    mean_c = sum(r["content_risk"] for r in ok) / n
    mean_m = sum(r["media_risk"] for r in ok) / n
    print(f"\n  평균 콘텐츠 {mean_c:>5.1f} / 평균 미디어 {mean_m:>5.1f}")

    if fp_any:
        print("\n  헛경보가 난 대화 (원인 파악용)")
        for r in sorted(fp_any, key=lambda x: -x["overall_score"])[:5]:
            print(f"    {r['overall_score']:>5.1f} {r['overall_level']}  "
                  f"C{r['content_risk']:>5.1f}/M{r['media_risk']:>5.1f}  {r['transcript'][:52]}")

    Path(args.out).write_text(json.dumps({
        "_설명": "정상 대화(KsponSpeech 자유대화)에서의 헛경보율. 전부 일상 대화이므로 "
                 "'낮음'이 아니면 헛경보다. 사기 정탐률은 validate_real_calls.py 참고.",
        "source": "KsponSpeech (AI Hub 「한국어 음성」의 공개 미러). 검증 목적 사용, 재배포 안 함",
        "n": n, "elapsed_sec": round(elapsed, 1),
        "level_thresholds": LEVEL_THRESHOLDS,
        "false_alarm_any": len(fp_any), "false_alarm_high": len(fp_high),
        "content_fp_at50": len(content_fp), "media_fp_at50": len(media_fp),
        "mean_content_risk": round(mean_c, 2), "mean_media_risk": round(mean_m, 2),
        "results": rows,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n상세 저장: {args.out}")
    print("\n※ KsponSpeech는 전화 통화가 아니라 대면 자유대화다. 전화망 대역·코덱은 없다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
