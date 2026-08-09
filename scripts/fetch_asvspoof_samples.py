"""
ASVspoof 2019 LA 검증용 샘플 확보 (parquet 부분 읽기)
담당: 이상원

기획서 [데이터 확보 방안]의 [음성] 항목 대응. 공식 ASVspoof는 등록이 필요하고
Kaggle 미러도 계정이 필요한데, HuggingFace의
`SpeechAntiSpoofingBenchmarks/ASVspoof2019_LA` 는 게이트가 없다.

다만 오디오가 shard당 약 460MB인 parquet 9개로 나뉘어 있다(총 4GB). 전부 받을 필요는
없어서, parquet의 row group 단위 읽기 + HTTP Range를 조합해 필요한 만큼만 가져온다.
(FF++ 영상 확보와 같은 방식 - scripts/_http_range.py)

라벨: **0 = bonafide(진짜), 1 = spoof(합성)**
  labels.parquet의 분포가 0:7355 / 1:63882 으로, ASVspoof2019 LA eval의 공식 수치
  (bonafide 7355, spoof 63882)와 정확히 일치해 확인했다.

실행:
    .venv\\Scripts\\python.exe scripts/fetch_asvspoof_samples.py --bonafide 20 --spoof 30
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _console import setup_console  # noqa: E402

setup_console()  # cp949 콘솔에서 유니코드 출력 중 죽는 문제 방지 (_console.py)

from _http_range import HttpRangeFile  # noqa: E402

BASE = ("https://huggingface.co/datasets/SpeechAntiSpoofingBenchmarks/"
        "ASVspoof2019_LA/resolve/main/")
SHARD = "data/test-00000-of-00009.parquet"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = PROJECT_ROOT / "data" / "asvspoof_samples"

LABEL_BONAFIDE = 0
LABEL_SPOOF = 1


def main():
    parser = argparse.ArgumentParser(description="ASVspoof2019 LA 검증 샘플 부분 추출")
    parser.add_argument("--bonafide", type=int, default=20, help="받을 진짜 음성 수")
    parser.add_argument("--spoof", type=int, default=30, help="받을 합성 음성 수")
    parser.add_argument("--max-row-groups", type=int, default=40,
                        help="훑어볼 row group 수 상한 (하나당 약 6MB)")
    args = parser.parse_args()

    import pyarrow.parquet as pq

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    remote = HttpRangeFile(BASE + SHARD)
    pf = pq.ParquetFile(remote)
    print(f"shard: {SHARD}")
    print(f"  전체 {remote.size / 1024**2:.0f} MB, "
          f"{pf.metadata.num_rows}행, row group {pf.metadata.num_row_groups}개")

    want = {LABEL_BONAFIDE: args.bonafide, LABEL_SPOOF: args.spoof}
    got = {LABEL_BONAFIDE: 0, LABEL_SPOOF: 0}
    saved = []

    n_groups = min(args.max_row_groups, pf.metadata.num_row_groups)
    for gi in range(n_groups):
        if all(got[k] >= want[k] for k in want):
            break
        table = pf.read_row_group(gi, columns=["path", "audio", "label"])
        rows = table.to_pylist()
        for row in rows:
            label = int(row["label"])
            if label not in want or got[label] >= want[label]:
                continue
            audio = row["audio"]
            data = audio["bytes"] if isinstance(audio, dict) else None
            if not data:
                continue
            src_name = Path(audio.get("path") or row["path"] or f"{gi}.flac").name
            prefix = "bonafide" if label == LABEL_BONAFIDE else "spoof"
            dst = OUT_DIR / f"{prefix}__{src_name}"
            dst.write_bytes(data)
            saved.append(dst)
            got[label] += 1
        print(f"  row group {gi:>3}: 누적 bonafide {got[LABEL_BONAFIDE]}, "
              f"spoof {got[LABEL_SPOOF]}   ({remote.bytes_fetched / 1024**2:.0f} MB 받음)")

    print(f"\n저장: {OUT_DIR}")
    print(f"  진짜(bonafide) {got[LABEL_BONAFIDE]}개, 합성(spoof) {got[LABEL_SPOOF]}개")
    print(f"  {remote.summary()}")

    if got[LABEL_BONAFIDE] < want[LABEL_BONAFIDE] or got[LABEL_SPOOF] < want[LABEL_SPOOF]:
        print("\n[주의] 목표 개수를 못 채웠습니다. --max-row-groups 를 늘려보세요.")
        print("       ASVspoof는 spoof가 bonafide보다 9배 많아 bonafide가 늦게 모입니다.")

    print("\n다음 단계:")
    print("  .venv\\Scripts\\python.exe scripts\\validate_audio_spoof.py")


if __name__ == "__main__":
    main()
