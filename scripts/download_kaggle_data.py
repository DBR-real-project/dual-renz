"""
Kaggle 데이터셋 다운로드 (DFDC / FF++ 미러 / ASVspoof)
담당: 이상원

기획서 [데이터 확보 방안]에 적힌 출처들을 받는 스크립트.

    [영상] DFDC sample subset, FaceForensics++ 비공식 Kaggle 미러(xdxd003/ff-c23)
    [음성] ASVspoof 2019/2021 (HuggingFace LanceaKing/asvspoof2019, Kaggle 미러)

⚠ Kaggle은 계정 인증이 필요하다. 이건 브라우저에서 사람이 직접 해야 하는 절차라
  자동화할 수 없다. 아래 안내대로 kaggle.json을 한 번 배치하면 그 다음부터는 자동이다.

  참고: FF++ 검증 샘플은 Kaggle 없이도 이미 확보돼 있다.
        scripts/fetch_ff_samples.py 가 HuggingFace 미러에서 인증 없이 받아온다.
        이 스크립트는 **DFDC 크로스도메인 검증**과 **AASIST용 음성 데이터**를 위해 필요하다.

실행:
    .venv\\Scripts\\python.exe scripts/download_kaggle_data.py --check
    .venv\\Scripts\\python.exe scripts/download_kaggle_data.py --dataset dfdc
    .venv\\Scripts\\python.exe scripts/download_kaggle_data.py --dataset all
"""

import argparse
import os
import sys
from pathlib import Path

from _console import setup_console  # noqa: E402

setup_console()  # cp949 콘솔에서 유니코드 출력 중 죽는 문제 방지 (_console.py)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"

# kind: "competition"은 대회 규칙 동의가 추가로 필요하다 (사이트에서 Join 버튼)
TARGETS = {
    "dfdc": {
        "kind": "competition",
        "ref": "deepfake-detection-challenge",
        "file": "train_sample_videos.zip",
        "out": DATA_DIR / "dfdc",
        "size": "약 4GB",
        "why": "크로스도메인 검증. FF++로만 검증한 성능이 다른 분포에서도 유지되는지 확인",
        "note": "대회 페이지에서 'Late Submission' 또는 규칙 동의(Join Competition)를 "
                "먼저 눌러야 다운로드가 열린다.",
    },
    "ffpp": {
        "kind": "dataset",
        "ref": "xdxd003/ff-c23",
        "file": None,
        "out": DATA_DIR / "ffpp_kaggle",
        "size": "수십 GB (전체)",
        "why": "기획서에 적힌 FF++ 비공식 미러",
        "note": "이미 HuggingFace 미러로 검증 샘플을 확보했으므로 우선순위 낮음. "
                "scripts/fetch_ff_samples.py 를 먼저 볼 것.",
    },
    "asvspoof": {
        "kind": "dataset",
        "ref": "awsaf49/asvpoof-2019-dataset",
        "file": None,
        "out": DATA_DIR / "asvspoof2019",
        "size": "약 25GB",
        "why": "AASIST 음성 안티스푸핑 연동 및 검증용",
        "note": "HuggingFace LanceaKing/asvspoof2019 쪽이 인증 없이 받아질 수도 있으니 "
                "그쪽을 먼저 확인하는 것을 권장.",
    },
}

CRED_GUIDE = """
Kaggle 자격증명이 없습니다. 아래 순서로 한 번만 설정하면 됩니다.

  1. https://www.kaggle.com/settings/account 접속 (로그인 상태에서)
  2. 'API' 항목에서 [Create New Token] 클릭 -> kaggle.json 파일이 다운로드됨
  3. 그 파일을 아래 경로에 그대로 복사

         {path}

  4. 이 스크립트를 다시 실행

  DFDC(대회 데이터)는 추가로 대회 규칙 동의가 필요합니다:
    https://www.kaggle.com/c/deepfake-detection-challenge/rules
    페이지에서 규칙에 동의(Join/Accept)해야 다운로드가 열립니다.
"""


def cred_path() -> Path:
    return Path(os.environ.get("KAGGLE_CONFIG_DIR", Path.home() / ".kaggle")) / "kaggle.json"


def has_credentials() -> bool:
    if os.environ.get("KAGGLE_USERNAME") and os.environ.get("KAGGLE_KEY"):
        return True
    return cred_path().exists()


def get_api():
    try:
        from kaggle.api.kaggle_api_extended import KaggleApi
    except ImportError:
        print("[에러] kaggle 패키지가 없습니다. 설치:", file=sys.stderr)
        print("       .venv\\Scripts\\python.exe -m pip install kaggle", file=sys.stderr)
        sys.exit(1)
    api = KaggleApi()
    api.authenticate()
    return api


def show_status() -> None:
    print("=== Kaggle 자격증명 ===")
    if has_credentials():
        print(f"  ✓ 있음: {cred_path()}")
    else:
        print(f"  ✗ 없음: {cred_path()}")
        print(CRED_GUIDE.format(path=cred_path()))

    print("=== 받을 대상 ===")
    for key, t in TARGETS.items():
        exists = t["out"].exists() and any(t["out"].iterdir()) if t["out"].exists() else False
        mark = "받음" if exists else "미확보"
        print(f"\n  [{key}] {t['ref']}  ({t['size']})  -> {mark}")
        print(f"        용도: {t['why']}")
        print(f"        경로: {t['out']}")
        if t.get("note"):
            print(f"        참고: {t['note']}")


def download(key: str) -> bool:
    t = TARGETS[key]
    out = t["out"]
    out.mkdir(parents=True, exist_ok=True)
    api = get_api()

    print(f"\n=== {key}: {t['ref']} ({t['size']}) ===")
    print(f"    저장 경로: {out}")
    try:
        if t["kind"] == "competition":
            if t["file"]:
                print(f"    파일 하나만 받습니다: {t['file']}")
                api.competition_download_file(t["ref"], t["file"], path=str(out), quiet=False)
            else:
                api.competition_download_files(t["ref"], path=str(out), quiet=False)
        else:
            api.dataset_download_files(t["ref"], path=str(out), unzip=False, quiet=False)
    except Exception as exc:
        msg = str(exc)
        print(f"    [실패] {type(exc).__name__}: {msg}", file=sys.stderr)
        if "403" in msg or "Forbidden" in msg:
            print("    -> 대회/데이터셋 규칙 동의가 안 된 상태일 가능성이 큽니다.", file=sys.stderr)
            print(f"       https://www.kaggle.com/c/{t['ref']}/rules 에서 동의 후 재시도.",
                  file=sys.stderr)
        elif "404" in msg:
            print("    -> ref가 바뀌었을 수 있습니다. Kaggle에서 실제 경로를 확인하세요.",
                  file=sys.stderr)
        return False

    files = list(out.rglob("*"))
    total = sum(f.stat().st_size for f in files if f.is_file())
    print(f"    완료: 파일 {sum(1 for f in files if f.is_file())}개, {total / 1024**3:.2f} GB")
    return True


def main():
    parser = argparse.ArgumentParser(description="Kaggle 데이터셋 다운로드")
    parser.add_argument("--check", action="store_true", help="자격증명과 확보 상태만 확인")
    parser.add_argument("--dataset", choices=list(TARGETS) + ["all"], default=None)
    args = parser.parse_args()

    if args.check or not args.dataset:
        show_status()
        if not args.dataset:
            print("\n받으려면: --dataset dfdc  (또는 ffpp / asvspoof / all)")
        return

    if not has_credentials():
        print(CRED_GUIDE.format(path=cred_path()), file=sys.stderr)
        sys.exit(1)

    keys = list(TARGETS) if args.dataset == "all" else [args.dataset]
    ok = [k for k in keys if download(k)]
    print(f"\n성공 {len(ok)}/{len(keys)}개")
    if "dfdc" in ok:
        print("\n다음 단계 (DFDC 크로스도메인 검증):")
        print("  1. train_sample_videos.zip 압축 해제")
        print("  2. metadata.json의 label(REAL/FAKE)로 파일명을 real__/fake__ 로 정리")
        print("  3. .venv\\Scripts\\python.exe scripts\\validate_detector.py "
              "--sample-dir data\\dfdc\\train_sample_videos")


if __name__ == "__main__":
    main()
