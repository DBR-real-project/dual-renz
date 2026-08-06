"""
음성 스푸핑 탐지(AASIST) 성능 실측
담당: 이상원

기획서 [품질 검증 계획]의 음성 쪽 대응. 영상(validate_detector.py)과 같은 지표를 쓴다.

data/asvspoof_samples/ 의 라벨링된 ASVspoof2019 LA 음성으로 측정한다.
샘플은 scripts/fetch_asvspoof_samples.py 로 먼저 받아둘 것.

파일명 규칙:
    bonafide__<id>.flac  -> 진짜 사람 음성
    spoof__<id>.flac     -> 합성/변조 음성

실행:
    .venv\\Scripts\\python.exe scripts/validate_audio_spoof.py
"""

import argparse
import json
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parent / "src"))

from validate_detector import confusion, best_threshold, separation, fmt_pct  # noqa: E402

from media_detection.audio_spoof_detector import get_shared_detector, is_available  # noqa: E402
from media_detection.deepfake_detector import FrameAggregation  # noqa: E402

PROJECT_ROOT = SCRIPT_DIR.parent
SAMPLE_DIR = PROJECT_ROOT / "data" / "asvspoof_samples"
REPORT_PATH = PROJECT_ROOT / "docs" / "validation_report_audio.json"

AUDIO_SUFFIXES = {".flac", ".wav", ".mp3", ".m4a", ".ogg"}


def load_samples(sample_dir: Path) -> list:
    """
    파일명에서 라벨을 뽑는다.
    영상 쪽과 용어를 맞추기 위해 bonafide -> 'real', spoof -> 'fake' 로 매핑한다.
    (지표 함수가 real/fake를 기대한다)
    """
    samples = []
    for p in sorted(sample_dir.iterdir()):
        if p.suffix.lower() not in AUDIO_SUFFIXES:
            continue
        if p.name.startswith("bonafide__"):
            samples.append({"path": p, "label": "real", "method": "-"})
        elif p.name.startswith("spoof__"):
            samples.append({"path": p, "label": "fake", "method": "spoof"})
    return samples


def main():
    parser = argparse.ArgumentParser(description="AASIST 음성 스푸핑 탐지 성능 실측")
    parser.add_argument("--sample-dir", default=str(SAMPLE_DIR))
    parser.add_argument("--max-windows", type=int, default=8,
                        help="영상/음성 하나당 분석할 4초 창 수")
    parser.add_argument("--aggregation", default=FrameAggregation.TOPK_MEAN.value,
                        choices=[a.value for a in FrameAggregation])
    parser.add_argument("--out", default=str(REPORT_PATH))
    args = parser.parse_args()

    if not is_available():
        print("[에러] AASIST를 쓸 수 없습니다. 레포를 먼저 받으세요:", file=sys.stderr)
        print("       git clone --depth 1 https://github.com/clovaai/aasist.git "
              "external/aasist", file=sys.stderr)
        sys.exit(1)

    sample_dir = Path(args.sample_dir)
    samples = load_samples(sample_dir)
    if not samples:
        print(f"[에러] 샘플이 없습니다: {sample_dir}", file=sys.stderr)
        print("      먼저 실행하세요: "
              ".venv\\Scripts\\python.exe scripts\\fetch_asvspoof_samples.py", file=sys.stderr)
        sys.exit(1)

    n_real = sum(1 for s in samples if s["label"] == "real")
    print(f"샘플 {len(samples)}개 (진짜 {n_real}, 합성 {len(samples) - n_real})")
    print(f"창 {args.max_windows}개/파일(각 4.04초), 집계 {args.aggregation}\n")

    detector = get_shared_detector()
    results = []
    t0 = time.time()
    for i, s in enumerate(samples, 1):
        print(f"  [{i:>2}/{len(samples)}] {s['path'].name[:40]:40}", end=" ", flush=True)
        try:
            r = detector.score_audio(
                str(s["path"]),
                aggregation=FrameAggregation(args.aggregation),
                max_windows=args.max_windows,
            )
            d = r.as_dict()
            results.append({
                "file": s["path"].name,
                "label": s["label"],
                "score": d["spoof_score"],
                "windows": d["windows_analyzed"],
                "duration": d["duration_sec"],
            })
            print(f"{d['spoof_score']:6.2f}  ({d['duration_sec']:.1f}초)")
        except Exception as exc:
            print(f"실패: {type(exc).__name__}")
            results.append({"file": s["path"].name, "label": s["label"],
                            "score": None, "error": f"{type(exc).__name__}: {exc}"})

    elapsed = time.time() - t0
    scored = [r for r in results if r.get("score") is not None]
    print(f"\n분석 완료: {len(scored)}/{len(samples)}개, {elapsed:.0f}초 "
          f"({elapsed / max(1, len(samples)):.2f}초/파일)")

    if not scored:
        print("[에러] 분석 성공한 샘플이 없습니다.", file=sys.stderr)
        sys.exit(1)

    sep = separation(scored)
    print("\n--- 점수 분포 (spoof 확률) ---")
    print(f"  진짜: 최소 {sep['real_min']:6.2f}  평균 {sep['real_mean']:6.2f}  최대 {sep['real_max']:6.2f}")
    print(f"  합성: 최소 {sep['fake_min']:6.2f}  평균 {sep['fake_mean']:6.2f}  최대 {sep['fake_max']:6.2f}")
    if sep["gap"] > 0:
        print(f"  => 완전 분리 (합성 최소 - 진짜 최대 = +{sep['gap']:.2f})")
    else:
        print(f"  => 분포 겹침 (겹치는 폭 {-sep['gap']:.2f})")

    at50 = confusion(scored, 50.0)
    best = best_threshold(scored)
    print("\n--- 성능 지표 ---")
    for label, c in (("threshold 50 (기본)", at50),
                     (f"threshold {best['threshold']:.2f} (최적)", best)):
        print(f"  [{label}]")
        print(f"    정탐률(Recall)   {fmt_pct(c['recall'])}   합성 {c['tp']}/{c['tp'] + c['fn']}개 탐지")
        print(f"    오탐률(FPR)      {fmt_pct(c['fpr'])}   진짜 {c['fp']}/{c['fp'] + c['tn']}개 오판")
        print(f"    정밀도(Precision){fmt_pct(c['precision'])}")
        print(f"    정확도(Accuracy) {fmt_pct(c['accuracy'])}")

    report = {
        "sample_dir": str(sample_dir),
        "n_samples": len(samples),
        "n_bonafide": n_real,
        "n_spoof": len(samples) - n_real,
        "max_windows": args.max_windows,
        "aggregation": args.aggregation,
        "model": "AASIST (clovaai)",
        "elapsed_sec": round(elapsed, 1),
        "separation": sep,
        "at_threshold_50": at50,
        "best_threshold": best,
        "results": results,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n상세 결과 저장: {out}")


if __name__ == "__main__":
    main()
