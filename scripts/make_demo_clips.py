"""
교차검증 데모 클립 생성
담당: 이상원

기획서의 핵심 차별점은 *"얼굴은 진짜인데 화법이 사기인 경우"*와
*"화법은 자연스러운데 얼굴이 가짜인 경우"*를 모두 잡는 **교차검증**이다.
그런데 이걸 보여주려면 영상과 음성의 진위를 독립적으로 조합한 샘플이 필요한데,
공개 데이터셋은 영상(FF++)과 음성(ASVspoof)이 따로 있고 FF++ 영상에는 오디오 트랙이 없다.

그래서 둘을 합쳐 4가지 조합을 만든다:

    | 클립            | 영상   | 음성     | 기대 동작                        |
    |-----------------|--------|----------|----------------------------------|
    | both_real       | 진짜   | 진짜     | 미디어 위험도 낮음               |
    | video_fake      | 딥페이크| 진짜     | 영상 엔진만 반응                 |
    | audio_fake      | 진짜   | 합성음성 | 음성 엔진만 반응                 |
    | both_fake       | 딥페이크| 합성음성 | 둘 다 반응 -> 교차 보너스 최대   |

  주의: 인위적으로 합성한 조합이라 "실제 이런 통화가 있었다"는 뜻이 아니다.
        두 엔진이 독립적으로 동작하고 통합 로직이 의도대로 합쳐지는지 보여주는 용도다.

준비:
    scripts/fetch_ff_samples.py       (영상)
    scripts/fetch_asvspoof_samples.py (음성)

실행:
    .venv\\Scripts\\python.exe scripts/make_demo_clips.py
"""

import argparse
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VIDEO_DIR = PROJECT_ROOT / "data" / "ff_samples"
AUDIO_DIR = PROJECT_ROOT / "data" / "asvspoof_samples"
OUT_DIR = PROJECT_ROOT / "data" / "demo_clips"

COMBOS = [
    ("both_real", "real", "bonafide", "진짜 영상 + 진짜 음성"),
    ("video_fake", "fake", "bonafide", "딥페이크 영상 + 진짜 음성"),
    ("audio_fake", "real", "spoof", "진짜 영상 + 합성 음성"),
    ("both_fake", "fake", "spoof", "딥페이크 영상 + 합성 음성"),
]


def pick(directory: Path, prefix: str, suffixes) -> Path:
    """조건에 맞는 첫 파일을 고른다. 조합마다 같은 소스를 써야 비교가 공정하다."""
    for p in sorted(directory.iterdir()):
        if p.suffix.lower() in suffixes and p.name.startswith(prefix):
            return p
    raise FileNotFoundError(f"{directory} 에 '{prefix}' 로 시작하는 파일이 없습니다")


# FF++ Xception(2019)이 학습한 기법. 이 중에서 골라야 데모에서 실제로 탐지된다.
# FaceShifter/DeepFakeDetection은 미학습이라 거의 못 잡는다 (docs/validation_report.md 참고).
DETECTABLE_METHODS = ["Deepfakes", "Face2Face", "FaceSwap"]


def mux(video: Path, audio: Path, out: Path) -> None:
    cmd = [
        "ffmpeg", "-nostdin", "-loglevel", "error", "-y",
        "-stream_loop", "-1", "-i", str(video),   # 영상이 짧으면 반복해서 음성 길이에 맞춘다
        "-i", str(audio),
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "96k",
        "-shortest", str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 실패: {proc.stderr.strip()[:400]}")


def main():
    parser = argparse.ArgumentParser(description="교차검증 데모 클립 생성")
    parser.add_argument(
        "--fake-method", default="Deepfakes",
        help="쓸 조작 기법 (기본 Deepfakes). FF++ Xception이 학습한 기법을 골라야 "
             "데모에서 실제로 탐지된다: " + ", ".join(DETECTABLE_METHODS),
    )
    args = parser.parse_args()

    if args.fake_method not in DETECTABLE_METHODS:
        print(f"[주의] '{args.fake_method}' 는 모델이 학습하지 않은 기법일 수 있습니다. "
              f"데모에서 탐지가 안 될 수 있습니다.", file=sys.stderr)

    for d, hint in ((VIDEO_DIR, "scripts/fetch_ff_samples.py"),
                    (AUDIO_DIR, "scripts/fetch_asvspoof_samples.py")):
        if not d.exists() or not any(d.iterdir()):
            print(f"[에러] {d} 가 비어 있습니다. 먼저 실행하세요:", file=sys.stderr)
            print(f"       .venv\\Scripts\\python.exe {hint}", file=sys.stderr)
            sys.exit(1)

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # 조합 간 비교가 공정하도록 소스는 각각 하나씩만 고정해서 쓴다
    src = {
        ("real",): pick(VIDEO_DIR, "real__", {".mp4"}),
        ("fake",): pick(VIDEO_DIR, f"fake_{args.fake_method}__", {".mp4"}),
        ("bonafide",): pick(AUDIO_DIR, "bonafide__", {".flac", ".wav"}),
        ("spoof",): pick(AUDIO_DIR, "spoof__", {".flac", ".wav"}),
    }
    print("소스 파일:")
    for k, v in src.items():
        print(f"  {k[0]:9} {v.name}")

    print("\n생성:")
    for name, vkind, akind, desc in COMBOS:
        out = OUT_DIR / f"{name}.mp4"
        try:
            mux(src[(vkind,)], src[(akind,)], out)
            size = out.stat().st_size / 1024
            print(f"  {out.name:16} {size:7.0f} KB   {desc}")
        except Exception as exc:
            print(f"  {out.name:16} 실패: {exc}", file=sys.stderr)

    print(f"\n저장 위치: {OUT_DIR}")
    print("\n다음 단계 - 교차검증 동작 확인:")
    for name, _, _, _ in COMBOS:
        print(f"  .venv\\Scripts\\python.exe scripts\\demo_full_pipeline.py "
              f"--input data\\demo_clips\\{name}.mp4")


if __name__ == "__main__":
    main()
