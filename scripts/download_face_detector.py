"""
YuNet 얼굴 검출기 가중치 내려받기
담당: 이상원

`face_utils.py`가 1순위로 쓰는 검출기다. 232KB짜리 ONNX 파일 하나이고,
opencv 4.x에 `cv2.FaceDetectorYN`으로 런타임이 들어 있어 파이썬 패키지 추가 설치는
필요 없다. 없으면 Haar cascade로 자동 폴백하므로 필수는 아니지만,
**얼굴 검출률이 눈에 띄게 올라간다** (docs/validation_report.md 4-5절).

주의: opencv_zoo는 가중치를 git-lfs로 올려둬서 raw.githubusercontent.com 주소로 받으면
      131바이트짜리 LFS 포인터 텍스트가 내려온다. media.githubusercontent.com/media/
      경로를 써야 실제 파일이 온다. (실제로 이걸로 한 번 헛발질했다)

실행:
    .venv\\Scripts\\python.exe scripts/download_face_detector.py
"""

import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _console import setup_console  # noqa: E402

setup_console()  # cp949 콘솔에서 유니코드 출력 중 죽는 문제 방지 (_console.py)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUT = PROJECT_ROOT / "models" / "face_detection_yunet_2023mar.onnx"

URL = ("https://media.githubusercontent.com/media/opencv/opencv_zoo/main/"
       "models/face_detection_yunet/face_detection_yunet_2023mar.onnx")

MIN_BYTES = 100_000  # LFS 포인터(약 131B)를 받아온 경우를 걸러내기 위한 하한


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)

    if OUT.exists() and OUT.stat().st_size >= MIN_BYTES:
        print(f"이미 있습니다: {OUT} ({OUT.stat().st_size / 1024:.0f} KB)")
        return 0

    print(f"내려받는 중: {URL}")
    try:
        with urllib.request.urlopen(URL, timeout=120) as r:
            data = r.read()
    except Exception as exc:
        print(f"[실패] {type(exc).__name__}: {exc}")
        print("       네트워크가 막혀 있어도 Haar cascade로 동작합니다 (검출률만 낮아짐).")
        return 1

    if len(data) < MIN_BYTES:
        print(f"[실패] 받은 파일이 너무 작습니다({len(data)}B). LFS 포인터일 수 있습니다.")
        return 1

    OUT.write_bytes(data)
    print(f"저장: {OUT} ({len(data) / 1024:.0f} KB)")

    import cv2

    try:
        cv2.FaceDetectorYN.create(str(OUT), "", (320, 320))
    except cv2.error as exc:
        print(f"[실패] opencv가 모델을 읽지 못했습니다: {exc}")
        return 1

    print("검증 OK — face_utils가 자동으로 YuNet을 씁니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
