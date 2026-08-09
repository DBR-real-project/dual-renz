"""
얼굴 검출기 비교 실측 — Haar cascade vs YuNet
담당: 이상원

`face_utils.py`가 검출기를 YuNet으로 바꾼 근거를 만드는 스크립트.
바꾼 뒤에도 검증 데이터가 바뀌면 다시 돌려서 수치를 갱신할 것.

왜 검출률이 중요한가:
  FF++ Xception은 **얼굴 크롭 전용 모델**이라 얼굴을 못 찾은 프레임은 통째로 버린다.
  한 프레임도 못 찾으면 영상 판정 자체가 불가능해 예외가 난다.
  즉 검출률은 "딥페이크를 얼마나 잡느냐" 이전에 "판정을 할 수 있느냐"의 문제다.

실행:
    .venv\\Scripts\\python.exe scripts/benchmark_face_detector.py
    .venv\\Scripts\\python.exe scripts/benchmark_face_detector.py --dir data/ff_samples --frames 16
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _console import setup_console  # noqa: E402

setup_console()  # cp949 콘솔에서 유니코드 출력 중 죽는 문제 방지 (_console.py)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import cv2  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DIR = PROJECT_ROOT / "data" / "ff_samples"
REPORT = PROJECT_ROOT / "docs" / "face_detector_benchmark.json"

VIDEO_SUFFIXES = {".mp4", ".avi", ".mov", ".mkv", ".webm"}


def rotate(frame, degrees: float):
    """
    프레임을 회전해 '고개를 돌린 통화 화면'을 흉내 낸다.

    FF++ 검증 영상은 전부 정면 얼굴이라 Haar도 98% 넘게 잡는다. 그런데 실제 통화는
    고개가 돌아가는 구간이 많고, Haar cascade는 정면 학습이라 여기서 급격히 무너진다.
    회전은 그 상황을 데이터 추가 없이 재현하는 가장 단순한 방법이다.
    (완전한 대체는 아니다 — 실제 옆얼굴은 형태 자체가 달라진다. 하한선으로 본다.)
    """
    h, w = frame.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), degrees, 1.0)
    return cv2.warpAffine(frame, m, (w, h), borderMode=cv2.BORDER_REPLICATE)


def sample_frames(path: Path, n_frames: int, target_fps: float = 1.0):
    """1fps로 최대 n_frames장 추출. 검출기 비교를 위해 같은 프레임을 쓴다."""
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return []
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    interval = max(1, round(src_fps / target_fps))
    frames, idx = [], 0
    while len(frames) < n_frames:
        ok, frame = cap.read()
        if not ok:
            break
        if idx % interval == 0:
            frames.append(frame)
        idx += 1
    cap.release()
    return frames


def run_backend(backend: str, videos, n_frames: int, degrees: float = 0.0) -> dict:
    """face_utils는 환경변수로 검출기를 고른다. 모듈을 새로 읽어 상태를 초기화한다."""
    os.environ["DUALGUARD_FACE_DETECTOR"] = backend
    for mod in list(sys.modules):
        if mod.endswith("face_utils"):
            del sys.modules[mod]
    from media_detection import face_utils  # noqa: E402

    total_frames = 0
    detected = 0
    videos_with_zero = []
    t0 = time.time()

    for path in videos:
        frames = sample_frames(path, n_frames)
        if degrees:
            frames = [rotate(f, degrees) for f in frames]
        hit = 0
        for f in frames:
            if face_utils.detect_largest_face(f) is not None:
                hit += 1
        total_frames += len(frames)
        detected += hit
        if frames and hit == 0:
            videos_with_zero.append(path.name)

    elapsed = time.time() - t0
    return {
        "backend": backend,
        "actual_backend": face_utils.active_backend(),
        "frames": total_frames,
        "detected": detected,
        "rate": round(100.0 * detected / total_frames, 1) if total_frames else 0.0,
        "videos_undetectable": videos_with_zero,
        "sec_per_frame": round(elapsed / total_frames, 4) if total_frames else 0.0,
        "elapsed_sec": round(elapsed, 1),
    }


def main():
    parser = argparse.ArgumentParser(description="얼굴 검출기 Haar vs YuNet 실측")
    parser.add_argument("--dir", default=str(DEFAULT_DIR), help="영상 디렉터리")
    parser.add_argument("--frames", type=int, default=16, help="영상당 최대 프레임")
    parser.add_argument("--limit", type=int, default=0, help="영상 수 제한 (0=전부)")
    parser.add_argument("--angles", default="0,15,30",
                        help="회전 각도 목록(도). 고개 돌린 통화 화면을 흉내 낸다")
    args = parser.parse_args()

    src = Path(args.dir)
    videos = sorted(p for p in src.glob("*") if p.suffix.lower() in VIDEO_SUFFIXES)
    if args.limit:
        videos = videos[:args.limit]
    if not videos:
        print(f"영상이 없습니다: {src}")
        print("  .venv\\Scripts\\python.exe scripts\\fetch_ff_samples.py --real 20 --fake 30")
        return 1

    angles = [float(a) for a in args.angles.split(",") if a.strip()]
    print(f"영상 {len(videos)}개, 영상당 최대 {args.frames}프레임 (1fps)")
    print(f"회전 각도: {angles} (0 = 원본, 클수록 고개를 돌린 상황)\n")

    print(f"{'각도':>6} {'Haar':>10} {'YuNet':>10} {'차이':>9}   판정 불가 영상 (Haar / YuNet)")
    print("-" * 74)

    results = []
    for deg in angles:
        row = {}
        for backend in ("haar", "yunet"):
            r = run_backend(backend, videos, args.frames, deg)
            if r["actual_backend"] != backend:
                print(f"[주의] {backend}를 요청했지만 실제로는 {r['actual_backend']}가 쓰였습니다.")
                print("       .venv\\Scripts\\python.exe scripts\\download_face_detector.py")
            row[backend] = r
        row["degrees"] = deg
        results.append(row)
        h, y = row["haar"], row["yunet"]
        print(f"{deg:>5.0f}° {h['rate']:>9.1f}% {y['rate']:>9.1f}% "
              f"{y['rate'] - h['rate']:>+8.1f}%p   "
              f"{len(h['videos_undetectable'])} / {len(y['videos_undetectable'])}")

    base = results[0]
    print(f"\n속도: 프레임당 Haar {base['haar']['sec_per_frame'] * 1000:.0f}ms, "
          f"YuNet {base['yunet']['sec_per_frame'] * 1000:.0f}ms")

    worst = max(results, key=lambda r: r["degrees"])
    only_haar_fails = (set(worst["haar"]["videos_undetectable"])
                       - set(worst["yunet"]["videos_undetectable"]))
    only_yunet_fails = (set(worst["yunet"]["videos_undetectable"])
                        - set(worst["haar"]["videos_undetectable"]))
    if only_haar_fails:
        print(f"\n{worst['degrees']:.0f}°에서 YuNet만 살린 영상 {len(only_haar_fails)}개:")
        for n in sorted(only_haar_fails)[:5]:
            print(f"    {n}")
    if only_yunet_fails:
        print(f"\n[주의] {worst['degrees']:.0f}°에서 Haar만 살린 영상 "
              f"{len(only_yunet_fails)}개 — face_utils가 YuNet 실패 시 Haar로 "
              f"한 번 더 보는 이유다:")
        for n in sorted(only_yunet_fails)[:5]:
            print(f"    {n}")

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(
        {"videos": len(videos), "frames_per_video": args.frames,
         "note": "회전은 고개 돌림을 흉내 낸 것이지 실제 옆얼굴이 아니다. 하한선으로 읽을 것.",
         "results": results},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n상세 저장: {REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
