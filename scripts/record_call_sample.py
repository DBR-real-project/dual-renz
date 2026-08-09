"""
마이크로 통화 샘플 녹음 (ffmpeg dshow)
담당: 이상원

왜 필요한가
-----------
데모에 **초록(낮음) 케이스가 한국어로 없다.** 지금 정상 통화 샘플은 Windows TTS로
만든 것이라 실제로 합성 음성이고, AASIST가 정확히 합성으로 잡아 미디어 위험도가
100이 된다. 판정 자체는 맞지만("합성 음성 + 정상 내용"), 심사장에서 정상 통화가
'중간'으로 뜨면 설명이 길어진다.

**사람이 직접 30초만 녹음하면** 진짜 목소리 + 정상 대화 = 낮음(초록)이 나와서
세 등급(낮음/중간/높음)을 모두 보여줄 수 있다. 실측으로 확인한 값:

    진짜 사람 목소리(ASVspoof bonafide) → 미디어 위험도 0.0~2.2 → 낮음
    합성 음성(TTS)                      → 미디어 위험도 100     → 중간
    합성 음성 + 사기 화법                → 통합 100             → 높음

사용법
------
    # 1. 마이크 장치 이름 확인
    .venv\\Scripts\\python.exe scripts/record_call_sample.py --list

    # 2. 녹음 (기본 30초). 아래 대본을 읽으면 된다.
    .venv\\Scripts\\python.exe scripts/record_call_sample.py --device "마이크 (Realtek...)" --seconds 30

    # 3. 분석
    .venv\\Scripts\\python.exe scripts/analyze_call.py --input data\\korean_calls\\real_normal_call.wav

--script 를 주면 읽을 대본을 골라 화면에 띄운다 (normal | scam).
"""

import argparse
import subprocess
import sys
from pathlib import Path

from _console import setup_console  # noqa: E402

setup_console()  # cp949 콘솔에서 유니코드 출력 중 죽는 문제 방지 (_console.py)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = PROJECT_ROOT / "data" / "korean_calls"

SCRIPTS = {
    "normal": {
        "file": "real_normal_call.wav",
        "desc": "정상 업무 통화 (초록 케이스용)",
        "lines": [
            "여보세요, 어제 보내주신 자료 잘 받았습니다. 감사합니다.",
            "다음 주 화요일 회의는 오후 세 시로 하면 어떨까요? 저는 그때가 편합니다.",
            "아 그리고 점심은 회사 근처 김치찌개 집 어떠세요? 거기 괜찮더라고요.",
            "확인해 보시고 편하실 때 다시 연락 주세요.",
            "네 그럼 그때 뵙겠습니다. 좋은 하루 보내세요.",
        ],
    },
    "scam": {
        "file": "real_scam_call.wav",
        "desc": "기관사칭형 사기 통화 (사람이 읽은 버전 — 음성은 진짜, 화법은 사기)",
        "lines": [
            "안녕하세요, 서울중앙지방검찰청 첨단범죄수사부 김민수 수사관입니다.",
            "고객님 명의 계좌가 대포통장으로 범죄에 연루되어 조사가 필요합니다.",
            "지금 즉시 조치하지 않으면 오늘 안에 모든 계좌가 동결됩니다.",
            "수사 기밀이라 가족을 포함해 아무에게도 알리지 마세요.",
            "지금 전화 끊지 마시고, 은행에 직접 가시면 공범으로 간주됩니다.",
            "고객님 자산 보호를 위해 안전계좌로 예치금을 이체해 주셔야 합니다.",
            "본인 확인을 위해 보안카드 번호와 오티피 번호를 불러주세요.",
        ],
    },
}


def list_devices() -> None:
    """ffmpeg으로 오디오 입력 장치 목록을 뽑는다."""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    # ffmpeg은 장치 목록을 stderr로 낸다 (에러가 아니다)
    lines = (proc.stderr or "").splitlines()
    audio = [ln for ln in lines if "(audio)" in ln]
    if not audio:
        print("오디오 입력 장치를 찾지 못했습니다.", file=sys.stderr)
        print("ffmpeg이 설치돼 있는지, 마이크 권한이 허용돼 있는지 확인하세요.", file=sys.stderr)
        print("\n--- ffmpeg 원본 출력 ---", file=sys.stderr)
        print("\n".join(lines[-25:]), file=sys.stderr)
        sys.exit(1)
    print("사용 가능한 오디오 입력 장치:\n")
    for ln in audio:
        # ffmpeg 출력에서 따옴표 안의 장치명만 뽑는다
        name = ln.split('"')[1] if '"' in ln else ln.strip()
        print(f'  --device "{name}"')
    print("\n위 줄을 그대로 복사해서 쓰면 됩니다.")


def record(device: str, seconds: int, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "dshow", "-i", f"audio={device}",
        "-t", str(seconds),
        # 16kHz 모노 — AASIST와 Whisper가 둘 다 기대하는 형식
        "-ac", "1", "-ar", "16000",
        str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0 or not out.exists():
        print(f"[에러] 녹음 실패\n{(proc.stderr or '').strip()[:500]}", file=sys.stderr)
        print("\n장치 이름이 정확한지 --list 로 확인하세요.", file=sys.stderr)
        sys.exit(1)


def main():
    ap = argparse.ArgumentParser(description="마이크로 통화 샘플 녹음")
    ap.add_argument("--list", action="store_true", help="오디오 입력 장치 목록만 출력")
    ap.add_argument("--device", help='마이크 장치 이름 (--list 로 확인)')
    ap.add_argument("--seconds", type=int, default=30)
    ap.add_argument("--script", default="normal", choices=list(SCRIPTS),
                    help="읽을 대본 (normal=초록 케이스, scam=사람 목소리 사기 케이스)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    if args.list:
        list_devices()
        return

    if not args.device:
        print("[에러] --device 가 필요합니다. 먼저 --list 로 장치 이름을 확인하세요.",
              file=sys.stderr)
        sys.exit(1)

    spec = SCRIPTS[args.script]
    out = Path(args.out) if args.out else OUT_DIR / spec["file"]

    print(f"\n=== 대본: {spec['desc']} ===")
    print("아래를 자연스럽게 읽으세요. 문장 사이에 1초쯤 쉬면 구간이 잘 나뉩니다.\n")
    for i, ln in enumerate(spec["lines"], 1):
        print(f"  {i}. {ln}")
    print(f"\n{args.seconds}초 동안 녹음합니다. 시작하면 바로 읽으세요.")
    input("준비되면 Enter를 누르세요... ")

    print("● 녹음 중...", flush=True)
    record(args.device, args.seconds, out)
    size_sec = out.stat().st_size / (16000 * 2)
    print(f"\n저장 완료: {out}  (약 {size_sec:.0f}초)")
    print("\n분석해 보기:")
    print(f'  .venv\\Scripts\\python.exe scripts\\analyze_call.py --input "{out}"')
    if args.script == "normal":
        print("\n기대 결과: 미디어 위험도 낮음(진짜 사람 목소리) + 콘텐츠 0 → 전체 '낮음'(초록)")
    else:
        print("\n기대 결과: 미디어 위험도 낮음(진짜 목소리)인데 콘텐츠가 높아 → '중간~높음'")
        print("           = 목소리는 진짜여도 화법으로 잡아낸다는 교차검증 시연에 딱 맞다")


if __name__ == "__main__":
    main()
