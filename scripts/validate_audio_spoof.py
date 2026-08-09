"""
음성 스푸핑 탐지(AASIST) 성능 실측
담당: 이상원

기획서 [품질 검증 계획]의 음성 쪽 대응. 영상(validate_detector.py)과 같은 지표를 쓴다.

data/asvspoof_samples/ 의 라벨링된 ASVspoof2019 LA 음성으로 측정한다.
샘플은 scripts/fetch_asvspoof_samples.py 로 먼저 받아둘 것.

파일명 규칙:
    bonafide__<id>.flac  -> 진짜 사람 음성
    spoof__<id>.flac     -> 합성/변조 음성

## --simulate-telephone

AASIST는 16kHz로 학습됐는데 **실제 통화는 전화망 8kHz**다. 이 차이가 성능에
얼마나 영향을 주는지 몰라서 "알려진 한계"에 문장으로만 적어뒀었다.
이 옵션은 검증 음성을 8kHz로 낮췄다가 되돌려(= 전화망을 통과시킨 셈) 다시 측정한다.
막연한 우려 대신 **수치**로 한계를 말할 수 있게 하는 것이 목적이다.

같이 비교하는 것:
    --resample poly    폴리페이즈(기본) — 저역통과 필터 포함
    --resample linear  선형 보간 — 예전 구현. 에일리어싱이 생긴다

실행:
    .venv\\Scripts\\python.exe scripts/validate_audio_spoof.py
    .venv\\Scripts\\python.exe scripts/validate_audio_spoof.py --simulate-telephone
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


TELEPHONE_RATE = 8000  # G.711 등 전화망 표준 샘플레이트


def to_telephone(src: Path, dst_dir: Path, method: str) -> Path:
    """
    16kHz 음성을 8kHz로 낮췄다가 다시 16kHz로 올린다 (전화망 통과 흉내).

    되돌리는 이유: AASIST 입력은 16kHz 고정이라 어차피 올려야 한다. 즉 실제
    통화에서 벌어지는 일과 같다 — 8kHz로 잃은 고주파는 돌아오지 않는다.
    그 손실이 판정에 얼마나 영향을 주는지가 우리가 알고 싶은 값이다.
    """
    import numpy as np
    import soundfile as sf

    from media_detection.audio_spoof_detector import _resample, _resample_linear

    fn = _resample_linear if method == "linear" else _resample

    data, sr = sf.read(str(src))
    if data.ndim > 1:
        data = data.mean(axis=1)
    narrow = fn(np.asarray(data, dtype=np.float64), sr, TELEPHONE_RATE)
    wide = fn(narrow, TELEPHONE_RATE, 16000)

    dst = dst_dir / f"{src.stem}.wav"
    sf.write(str(dst), wide.astype("float32"), 16000, subtype="PCM_16")
    return dst


def main():
    parser = argparse.ArgumentParser(description="AASIST 음성 스푸핑 탐지 성능 실측")
    parser.add_argument("--sample-dir", default=str(SAMPLE_DIR))
    parser.add_argument("--max-windows", type=int, default=8,
                        help="영상/음성 하나당 분석할 4초 창 수")
    parser.add_argument("--aggregation", default=FrameAggregation.TOPK_MEAN.value,
                        choices=[a.value for a in FrameAggregation])
    parser.add_argument("--simulate-telephone", action="store_true",
                        help="8kHz 전화망을 통과시킨 뒤 측정 (도메인 갭 수치화)")
    parser.add_argument("--resample", default="poly", choices=["poly", "linear"],
                        help="전화망 흉내에 쓸 리샘플링 방식")
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
    print(f"창 {args.max_windows}개/파일(각 4.04초), 집계 {args.aggregation}")

    tmpdir = None
    if args.simulate_telephone:
        import tempfile

        tmpdir = tempfile.TemporaryDirectory()
        out_dir = Path(tmpdir.name)
        print(f"전화망 흉내: 16kHz -> {TELEPHONE_RATE}Hz -> 16kHz "
              f"(리샘플링 {args.resample})")
        for s in samples:
            s["path"] = to_telephone(s["path"], out_dir, args.resample)
    print()

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
        "simulate_telephone": args.simulate_telephone,
        "resample": args.resample if args.simulate_telephone else None,
        "model": "AASIST (clovaai)",
        "elapsed_sec": round(elapsed, 1),
        "separation": sep,
        "at_threshold_50": at50,
        "best_threshold": best,
        "results": results,
    }
    out = Path(args.out)
    if args.simulate_telephone:
        # 기본 리포트를 덮어쓰면 발표용 수치가 전화망 조건으로 바뀌어버린다. 따로 남긴다.
        out = out.with_name(f"{out.stem}_telephone_{args.resample}{out.suffix}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n상세 결과 저장: {out}")

    if tmpdir is not None:
        tmpdir.cleanup()


if __name__ == "__main__":
    main()
