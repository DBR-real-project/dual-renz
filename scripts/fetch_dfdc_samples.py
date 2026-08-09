"""
DFDC(딥페이크 탐지 챌린지) 크로스도메인 검증 샘플 확보
담당: 이상원

## 왜 필요한가

지금까지의 영상 성능(정탐 73.3% / 오탐 0%)은 전부 **FF++ 안에서** 잰 값이다.
FF++ Xception은 FF++로 학습했으니 같은 도메인에서 잘 나오는 게 당연하다.
"실제 통화 영상에서도 되냐"는 질문에 답하려면 **다른 데이터셋**에서 재야 한다.
기획서 [품질 검증 계획]도 이걸 요구한다.

## Kaggle 없이 받는 법

공식 DFDC는 Kaggle 계정 + 대회 규칙 동의가 필요하다. 그런데 HuggingFace의
`gonnerthetooner/DFDC`가 게이트 없이 미러하고 있다 — 다만 **96.5GB 짜리 zip 하나**다.

전부 받을 필요는 없다. FF++ 때와 같은 방식(`scripts/_http_range.py`)으로
zip 중앙 디렉터리만 읽고 필요한 영상만 뽑는다. **색인 읽는 데 3.4MB면 된다.**
라벨은 zip 안의 `metadata.json`에 REAL/FAKE로 들어 있어 별도 라벨 파일이 필요 없다.

주의: DFDC는 REAL이 훨씬 적다(part 0 기준 REAL 86 / FAKE 1248). 목표 개수를 채우려면
여러 part를 훑어야 해서 `--max-parts`를 넉넉히 준다.

실행:
    .venv\\Scripts\\python.exe scripts/fetch_dfdc_samples.py --real 20 --fake 30
    .venv\\Scripts\\python.exe scripts\\validate_detector.py --backend ff \\
        --sample-dir data\\dfdc_samples --out docs\\validation_report_dfdc.json
"""

import argparse
import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _console import setup_console  # noqa: E402

setup_console()  # cp949 콘솔에서 유니코드 출력 중 죽는 문제 방지 (_console.py)

from _http_range import HttpRangeFile  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = PROJECT_ROOT / "data" / "dfdc_samples"

URL = ("https://huggingface.co/datasets/gonnerthetooner/DFDC/resolve/main/"
       "downloads/dfdc_10/dfdc-10.zip")

# validate_detector.py가 파일명에서 라벨을 읽는 규칙에 맞춘다
# validate_detector.load_samples()가 real__ / fake_<기법>__ 로 라벨을 읽는다.
# real 쪽은 밑줄 두 개여야 매칭된다 — 한 번 틀려서 20개를 다시 이름 바꿨다.
PREFIX = {"REAL": "real__DFDC_", "FAKE": "fake_DFDC__"}

MAX_VIDEO_MB = 12.0   # DFDC 영상은 보통 2~10MB. 이상하게 큰 항목은 건너뛴다


def main():
    parser = argparse.ArgumentParser(description="DFDC 크로스도메인 검증 샘플 부분 추출")
    parser.add_argument("--real", type=int, default=20, help="받을 진짜 영상 수")
    parser.add_argument("--fake", type=int, default=30, help="받을 가짜 영상 수")
    parser.add_argument("--max-parts", type=int, default=8,
                        help="훑어볼 DFDC part 수 (REAL이 적어 여러 part가 필요하다)")
    parser.add_argument("--out", default=str(OUT_DIR))
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    remote = HttpRangeFile(URL, chunk_size=1 << 20)
    print(f"원격 zip {remote.size / 1024**3:.1f} GB — 색인만 읽는다")
    zf = zipfile.ZipFile(remote)
    names = zf.namelist()
    print(f"  항목 {len(names)}개, 색인까지 {remote.bytes_fetched / 1024**2:.1f} MB 수신\n")

    metas = sorted(n for n in names if n.endswith("metadata.json"))
    by_name = {Path(n).name: n for n in names if n.endswith(".mp4")}

    want = {"REAL": args.real, "FAKE": args.fake}
    got = {"REAL": 0, "FAKE": 0}
    saved = []

    for mi, meta_path in enumerate(metas[:args.max_parts]):
        if all(got[k] >= want[k] for k in want):
            break
        try:
            meta = json.loads(zf.read(meta_path).decode("utf-8"))
        except Exception as exc:
            print(f"  [건너뜀] {meta_path}: {type(exc).__name__}")
            continue

        part = meta_path.split("/")[0]
        # REAL이 귀하므로 REAL을 먼저 집는다
        entries = sorted(meta.items(), key=lambda kv: kv[1].get("label") != "REAL")
        picked = 0
        for fname, info in entries:
            label = info.get("label")
            if label not in want or got[label] >= want[label]:
                continue
            arc = by_name.get(fname)
            if not arc:
                continue
            zi = zf.getinfo(arc)
            if zi.file_size > MAX_VIDEO_MB * 1024 * 1024:
                continue
            try:
                data = zf.read(arc)
            except Exception as exc:
                print(f"    [실패] {fname}: {type(exc).__name__}")
                continue
            dst = out_dir / f"{PREFIX[label]}{fname}"
            dst.write_bytes(data)
            saved.append(dst)
            got[label] += 1
            picked += 1

        print(f"  [{mi + 1}/{min(len(metas), args.max_parts)}] {part}: "
              f"{picked}개 추가 → 누적 REAL {got['REAL']} / FAKE {got['FAKE']}  "
              f"({remote.bytes_fetched / 1024**2:.0f} MB 수신)")

    print(f"\n저장: {out_dir}")
    print(f"  진짜 {got['REAL']}개, 가짜 {got['FAKE']}개")
    print(f"  {remote.summary()}")

    if got["REAL"] < want["REAL"] or got["FAKE"] < want["FAKE"]:
        print("\n[주의] 목표 개수를 못 채웠습니다. --max-parts 를 늘려보세요.")
        print("       DFDC는 REAL이 FAKE의 1/15 수준이라 REAL이 늦게 모입니다.")

    print("\n다음 단계 (크로스도메인 성능 측정):")
    print("  .venv\\Scripts\\python.exe scripts\\validate_detector.py --backend ff \\")
    print(f"      --sample-dir {out_dir} --out docs\\validation_report_dfdc.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
