"""
실제 보이스피싱 통화 녹취에서의 탐지 성능 실측
담당: 이상원

## 이 측정이 특별한 이유

다른 검증은 전부 우리가 만든 자료였다:
  - `scam_call.wav` 등 데모 샘플 → 우리가 대본을 쓰고 TTS로 읽혔다
  - `content_test_scenarios.json` 21건 → 우리가 대화를 지어냈다

여기서 쓰는 건 **금융감독원이 공개한 진짜 사기범 통화 녹취**다
(`scripts/fetch_real_call_samples.py`로 받는다). 우리가 만들지 않았고,
STT 품질도 실제 통화 그대로다 — 전화망 대역, 잡음, 끊김이 다 들어 있다.

## 무엇을 재고 무엇을 못 재나

**잴 수 있는 것: 정탐률.** 이 파일들은 전부 사기 통화이므로, 위험도가 높게 나오지
않으면 놓친 것이다.

**못 재는 것: 오탐률.** 정상 통화 녹취가 없다. 오탐은 별도로 잰다 —
`scripts/validate_content_fpr.py`가 사기와 무관한 한국어 457문장에서 1.53%를 냈다.
두 수치를 함께 봐야 그림이 완성된다.

**미디어 위험도는 낮게 나오는 게 정상이다.** 실제 사기범은 사람이 직접 말한다
(합성 음성이 아니다). 즉 이 데이터는 **"음성은 진짜인데 화법이 사기"** 인
교차검증의 반대 방향 사례이기도 하다 — 미디어 엔진만 있는 솔루션은 전부 놓친다.

실행:
    .venv\\Scripts\\python.exe scripts/validate_real_calls.py
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
SAMPLE_DIR = PROJECT_ROOT / "data" / "real_calls"
REPORT = PROJECT_ROOT / "docs" / "validation_report_real_calls.json"

AUDIO_SUFFIXES = {".wav", ".mp3", ".m4a", ".flac"}


def main():
    parser = argparse.ArgumentParser(description="실제 보이스피싱 녹취 탐지 성능")
    parser.add_argument("--sample-dir", default=str(SAMPLE_DIR))
    parser.add_argument("--out", default=str(REPORT))
    args = parser.parse_args()

    from orchestration.pipeline import LEVEL_THRESHOLDS, analyze

    sample_dir = Path(args.sample_dir)
    files = sorted(p for p in sample_dir.glob("*")
                   if p.suffix.lower() in AUDIO_SUFFIXES)
    if not files:
        print(f"샘플이 없습니다: {sample_dir}")
        print("  .venv\\Scripts\\python.exe scripts\\fetch_real_call_samples.py")
        return 1

    print(f"실제 보이스피싱 녹취 {len(files)}건 (금융감독원 「그놈 목소리」)")
    print(f"신호등 경계: 높음 {LEVEL_THRESHOLDS['높음']:.0f} / "
          f"중간 {LEVEL_THRESHOLDS['중간']:.0f}\n")

    rows = []
    t0 = time.time()
    for i, path in enumerate(files, 1):
        print(f"[{i}/{len(files)}] {path.name[:52]}", flush=True)
        try:
            rep = analyze(str(path), progress=None).as_dict()
        except Exception as exc:
            print(f"    실패: {type(exc).__name__}: {exc}")
            rows.append({"file": path.name, "error": f"{type(exc).__name__}: {exc}"})
            continue

        top = rep["top_categories"][:3]
        rows.append({
            "file": path.name,
            "duration": rep["duration"],
            "overall_score": rep["overall_score"],
            "overall_level": rep["overall_level"],
            "content_risk": rep["content_risk"],
            "media_risk": rep["media_risk"],
            "n_segments": len(rep["segments"]),
            "top_categories": top,
            "transcript_head": " ".join(
                s["transcript"] for s in rep["segments"][:3])[:160],
        })
        print(f"    {rep['overall_score']:5.1f} {rep['overall_level']}  "
              f"(콘텐츠 {rep['content_risk']:.1f} / 미디어 {rep['media_risk']:.1f}, "
              f"{len(rep['segments'])}구간)")
        if top:
            marks = ", ".join(f"{c['label']} {round(c['score'])}" for c in top)
            print(f"    기법: {marks}")
        if rows[-1]["transcript_head"]:
            print(f"    전사: {rows[-1]['transcript_head'][:70]}")

    ok = [r for r in rows if "error" not in r]
    if not ok:
        print("\n분석에 성공한 파일이 없습니다.")
        return 1

    elapsed = time.time() - t0
    high = [r for r in ok if r["overall_level"] == "높음"]
    warn = [r for r in ok if r["overall_level"] in ("높음", "중간")]
    missed = [r for r in ok if r["overall_level"] == "낮음"]

    print(f"\n--- 결과 ({len(ok)}건, {elapsed:.0f}초) ---")
    print(f"  '높음'으로 경고        {len(high)}/{len(ok)} = "
          f"{100.0 * len(high) / len(ok):.1f}%")
    print(f"  '중간' 이상으로 경고    {len(warn)}/{len(ok)} = "
          f"{100.0 * len(warn) / len(ok):.1f}%")
    print(f"  놓침('낮음')           {len(missed)}/{len(ok)}")
    for r in missed:
        print(f"    - {r['file'][:60]}  {r['overall_score']:.1f}")

    mean_content = sum(r["content_risk"] for r in ok) / len(ok)
    mean_media = sum(r["media_risk"] for r in ok) / len(ok)
    print(f"\n  평균 콘텐츠 위험도  {mean_content:5.1f}")
    print(f"  평균 미디어 위험도  {mean_media:5.1f}   "
          f"(사람이 직접 말한 통화이므로 낮은 게 정상)")

    cats = {}
    for r in ok:
        for c in r["top_categories"]:
            cats[c["label"]] = cats.get(c["label"], 0) + 1
    if cats:
        print("\n  탐지된 기법 (상위 3개 기준)")
        for label, n in sorted(cats.items(), key=lambda kv: -kv[1]):
            print(f"    {label:<18} {n}건")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps({
        "_설명": "금융감독원 공개 실제 보이스피싱 녹취에서의 탐지 성능. "
                 "전부 사기 통화이므로 정탐률만 잴 수 있다. "
                 "오탐률은 validate_content_fpr.py 참고.",
        "source": "금융감독원 보이스피싱지킴이 「그놈 목소리」",
        "n_files": len(ok),
        "elapsed_sec": round(elapsed, 1),
        "level_thresholds": LEVEL_THRESHOLDS,
        "warned_high": len(high),
        "warned_any": len(warn),
        "missed": len(missed),
        "mean_content_risk": round(mean_content, 2),
        "mean_media_risk": round(mean_media, 2),
        "results": rows,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n상세 저장: {args.out}")
    print("\n※ 오탐률은 여기서 잴 수 없다(전부 사기 통화). "
          "정상 문장 오탐은 validate_content_fpr.py 에서 1.53%.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
