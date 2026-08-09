"""
딥페이크 탐지 성능 실측 하네스
담당: 이상원

기획서 [품질 검증 계획] 대응:
  "데모 데이터셋(진짜·가짜 통화 각 15~20건 수준)을 구성해 정탐률(Recall)과
   오탐률(False Positive Rate)을 실측하고, 발표 시 수치로 제시합니다."

data/ff_samples/ 의 라벨링된 FF++ C23 영상으로 백엔드별 성능을 측정한다.
샘플은 scripts/fetch_ff_samples.py 로 먼저 받아둘 것.

파일명 규칙 (fetch_ff_samples.py가 붙여준다):
    real__<id>.mp4              -> 진짜
    fake_<조작기법>__<id>.mp4    -> 가짜

실행:
    .venv\\Scripts\\python.exe scripts/validate_detector.py
    .venv\\Scripts\\python.exe scripts/validate_detector.py --backend vit
    .venv\\Scripts\\python.exe scripts/validate_detector.py --backend both --max-frames 16
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from _console import setup_console  # noqa: E402

setup_console()  # cp949 콘솔에서 유니코드 출력 중 죽는 문제 방지 (_console.py)

from _metrics import (  # noqa: E402,F401  (하위 스크립트가 여기서 import 한다)
    best_threshold,
    confusion,
    fmt_pct,
    separation,
)
from media_detection.deepfake_detector import FrameAggregation  # noqa: E402
from media_detection.media_risk import analyze_video  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DIR = PROJECT_ROOT / "data" / "ff_samples"
REPORT_DIR = PROJECT_ROOT / "docs"


def load_samples(sample_dir: Path) -> list:
    """파일명에서 라벨과 조작기법을 뽑아 목록을 만든다."""
    samples = []
    for p in sorted(sample_dir.glob("*.mp4")):
        name = p.name
        if name.startswith("real__"):
            samples.append({"path": p, "label": "real", "method": "-"})
        elif name.startswith("fake_"):
            method = name[len("fake_"):].split("__", 1)[0]
            samples.append({"path": p, "label": "fake", "method": method})
    return samples


# 지표 함수는 scripts/_metrics.py 로 옮겼다 (콘텐츠 검증이 cv2 없이 쓰려면 필요).
# 기존 import 경로(`from validate_detector import confusion, ...`)를 유지하려고
# 여기서 그대로 재노출한다.


def run_backend(backend: str, samples: list, max_frames: int, aggregation: str) -> dict:
    print(f"\n{'=' * 62}")
    print(f" 백엔드: {backend}")
    print(f"{'=' * 62}")

    results = []
    t0 = time.time()
    for i, s in enumerate(samples, 1):
        print(f"  [{i:>2}/{len(samples)}] {s['path'].name[:46]:46}", end=" ", flush=True)
        try:
            r = analyze_video(
                str(s["path"]),
                max_frames=max_frames,
                aggregation=FrameAggregation(aggregation),
                backend=backend,
            )
            d = r.as_dict()
            results.append({
                "file": s["path"].name,
                "label": s["label"],
                "method": s["method"],
                "score": d["deepfake_score"],
                # 재척도 전 원점수도 같이 남긴다. calibrate_deepfake.py가 이 리포트로
                # 파라미터를 다시 적합하는데, 변환된 점수로 적합하면 **이중 적용**이 된다.
                # score_raw가 있으면 그쪽을 쓰도록 돼 있다.
                "score_raw": d.get("deepfake_score_raw"),
                "face_detector": d.get("face_detector"),
                "frames": d["frames_analyzed"],
                "face_rate": d["face_detection_rate"],
            })
            print(f"{d['deepfake_score']:6.2f}  (얼굴 {d['face_detection_rate']:.0%})")
        except Exception as exc:
            print(f"실패: {type(exc).__name__}")
            results.append({
                "file": s["path"].name, "label": s["label"], "method": s["method"],
                "score": None, "error": f"{type(exc).__name__}: {exc}",
            })

    elapsed = time.time() - t0
    scored = [r for r in results if r.get("score") is not None]
    failed = [r for r in results if r.get("score") is None]

    print(f"\n  분석 완료: {len(scored)}/{len(samples)}개 성공, {elapsed:.0f}초 "
          f"({elapsed / max(1, len(samples)):.1f}초/영상)")
    if failed:
        print(f"  실패 {len(failed)}개:")
        for f in failed:
            print(f"    {f['file']}: {f.get('error')}")

    if not scored:
        return {"backend": backend, "results": results, "note": "분석 성공한 샘플 없음"}

    sep = separation(scored)
    print("\n  --- 점수 분포 ---")
    if sep:
        print(f"    진짜: 최소 {sep['real_min']:6.2f}  평균 {sep['real_mean']:6.2f}  최대 {sep['real_max']:6.2f}")
        print(f"    가짜: 최소 {sep['fake_min']:6.2f}  평균 {sep['fake_mean']:6.2f}  최대 {sep['fake_max']:6.2f}")
        if sep["gap"] > 0:
            print(f"    => 완전 분리 (가짜 최소 - 진짜 최대 = +{sep['gap']:.2f})")
        else:
            print(f"    => 분포 겹침 (겹치는 폭 {-sep['gap']:.2f}) - threshold만으로는 완전 분리 불가")

    at50 = confusion(scored, 50.0)
    best = best_threshold(scored)

    print("\n  --- 성능 지표 ---")
    for label, c in (("threshold 50 (기본)", at50), (f"threshold {best['threshold']:.2f} (최적)", best)):
        print(f"    [{label}]")
        print(f"      정탐률(Recall)   {fmt_pct(c['recall'])}   가짜 {c['tp']}/{c['tp'] + c['fn']}개 탐지")
        print(f"      오탐률(FPR)      {fmt_pct(c['fpr'])}   진짜 {c['fp']}/{c['fp'] + c['tn']}개 오판")
        print(f"      정밀도(Precision){fmt_pct(c['precision'])}")
        print(f"      정확도(Accuracy) {fmt_pct(c['accuracy'])}")

    # 조작 기법별 성능 — 어떤 수법에 약한지가 실전에서 중요하다
    methods = sorted({r["method"] for r in scored if r["label"] == "fake"})
    if methods:
        print("\n  --- 조작 기법별 (threshold 50) ---")
        for m in methods:
            rows = [r for r in scored if r["method"] == m]
            hit = sum(1 for r in rows if r["score"] >= 50)
            avg = sum(r["score"] for r in rows) / len(rows)
            print(f"    {m:22} 탐지 {hit}/{len(rows)}   평균점수 {avg:6.2f}")

    return {
        "backend": backend,
        "n_samples": len(samples),
        "n_scored": len(scored),
        "elapsed_sec": round(elapsed, 1),
        "separation": sep,
        "at_threshold_50": at50,
        "best_threshold": best,
        "results": results,
    }




def main():
    parser = argparse.ArgumentParser(description="딥페이크 탐지 성능 실측")
    parser.add_argument("--backend", default="both", choices=["ff", "vit", "both"])
    parser.add_argument("--max-frames", type=int, default=12)
    parser.add_argument("--aggregation", default=FrameAggregation.TOPK_MEAN.value,
                        choices=[a.value for a in FrameAggregation])
    parser.add_argument("--sample-dir", default=str(SAMPLE_DIR))
    parser.add_argument("--out", default=str(REPORT_DIR / "validation_report.json"))
    args = parser.parse_args()

    sample_dir = Path(args.sample_dir)
    samples = load_samples(sample_dir)
    if not samples:
        print(f"[에러] 샘플이 없습니다: {sample_dir}", file=sys.stderr)
        print("      먼저 실행하세요: "
              ".venv\\Scripts\\python.exe scripts\\fetch_ff_samples.py", file=sys.stderr)
        sys.exit(1)

    n_real = sum(1 for s in samples if s["label"] == "real")
    print(f"샘플 {len(samples)}개 (진짜 {n_real}, 가짜 {len(samples) - n_real})")
    print(f"프레임 {args.max_frames}장/영상, 집계 {args.aggregation}")

    backends = ["ff", "vit"] if args.backend == "both" else [args.backend]
    report = {
        "sample_dir": str(sample_dir),
        "n_samples": len(samples),
        "n_real": n_real,
        "n_fake": len(samples) - n_real,
        "max_frames": args.max_frames,
        "aggregation": args.aggregation,
        "backends": {},
    }
    for b in backends:
        report["backends"][b] = run_backend(b, samples, args.max_frames, args.aggregation)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n상세 결과 저장: {out}")

    if len(backends) > 1:
        print(f"\n{'=' * 62}")
        print(" 백엔드 비교 (threshold 50)")
        print(f"{'=' * 62}")
        print(f"  {'백엔드':<8} {'정탐률':>10} {'오탐률':>10} {'정확도':>10}")
        for b in backends:
            c = report["backends"][b].get("at_threshold_50")
            if c:
                print(f"  {b:<8} {fmt_pct(c['recall']):>10} {fmt_pct(c['fpr']):>10} {fmt_pct(c['accuracy']):>10}")


if __name__ == "__main__":
    main()
