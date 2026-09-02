"""
한국어 **자연 대화체** 음성 확보 — 낭독체의 한계를 넘기 위해
담당: 이상원

## 왜 필요한가 (멘토링 피드백 반영)

지금까지 "정상 음성" 검증에 쓴 Zeroth-Korean은 **낭독체**다. 뉴스나 책을 또박또박
읽은 것이라 실제 통화와 말투·잡음·호흡이 전혀 다르다. 그래서
"실제 통화에서 헛경보가 나는가"를 제대로 못 재고 있었다.

KsponSpeech(한국어 자유대화 음성)는 **실제 사람들이 자유롭게 나눈 대화**다:

    "그니까 착한데 막 그런 거 있어 그 둘이 보띠오를 가잖아. 종훈이하고 정준이하고…"

낭독이 아니라 대화라서, 우리가 필요한 **정상 통화의 대체재**로 훨씬 낫다.

## ⚠ 데이터 이용에 관하여 (중요)

- 원본은 **AI Hub(한국지능정보사회진흥원)** 의 「한국어 음성」 데이터셋이고
  **이용 약관이 있다.** 여기서 받는 건 HuggingFace 공개 미러이며,
  미러 저장소에는 라이선스가 명시돼 있지 않다.
- 그래서 이 스크립트는 **성능 검증 목적의 소량만** 내려받고,
  받은 파일은 `data/`(gitignore 대상)에만 두며 **저장소에 커밋하지 않는다.**
- **재배포하지 않는다.** 발표·문서에는 측정 결과(수치)만 인용하고 출처를 밝힌다.
- 사업화나 정식 학습에 쓰려면 **AI Hub에서 직접 신청**해야 한다
  (https://aihub.or.kr — 회원가입 후 데이터 이용 신청).
  자세한 로드맵은 `docs/data_roadmap.md` 참고.

## 부분 추출

test shard 하나가 426MB다. 전부 받을 필요가 없어 FF++/ASVspoof 때와 같은
HTTP Range 방식으로 **필요한 row group만** 읽는다.

실행:
    .venv\\Scripts\\python.exe scripts/fetch_korean_conversation.py
    .venv\\Scripts\\python.exe scripts/fetch_korean_conversation.py --count 40 --min-sec 4
"""

import argparse
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _console import setup_console  # noqa: E402

setup_console()  # cp949 콘솔에서 유니코드 출력 중 죽는 문제 방지 (_console.py)

from _http_range import HttpRangeFile  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = PROJECT_ROOT / "data" / "korean_conversation"

URL = ("https://huggingface.co/datasets/DragonLine/ksponspeech/resolve/main/"
       "data/test-00000-of-00014.parquet")

SOURCE_NOTE = """한국어 자유대화 음성 (검증 전용)

출처: KsponSpeech — AI Hub(한국지능정보사회진흥원) 「한국어 음성」 데이터셋
      HuggingFace 미러 DragonLine/ksponspeech 에서 일부만 내려받음
      https://aihub.or.kr

용도: DualGuard 탐지 성능 검증(오탐률 측정)에만 사용합니다.

지켜야 할 것:
- 저장소에 커밋하지 않습니다 (data/ 는 .gitignore 대상)
- 재배포하지 않습니다
- 발표·문서에는 측정 결과(수치)만 인용하고 출처를 밝힙니다
- 사업화나 모델 학습에 쓰려면 AI Hub에서 정식으로 이용 신청해야 합니다

담긴 파일:
"""


def main():
    parser = argparse.ArgumentParser(description="한국어 자연 대화체 음성 확보(검증용)")
    parser.add_argument("--count", type=int, default=30, help="받을 발화 수")
    parser.add_argument("--min-sec", type=float, default=4.0, help="최소 길이(초)")
    parser.add_argument("--max-sec", type=float, default=20.0, help="최대 길이(초)")
    parser.add_argument("--out", default=str(OUT_DIR))
    args = parser.parse_args()

    import pyarrow.parquet as pq
    import soundfile as sf

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    print("한국어 자유대화 음성 (KsponSpeech, AI Hub 원본의 공개 미러)")
    print("  ⚠ 검증 목적 소량만 받습니다. 재배포 금지 — 자세한 건 스크립트 docstring 참고.\n")

    remote = HttpRangeFile(URL, chunk_size=1 << 22)
    pf = pq.ParquetFile(remote)
    print(f"원격 parquet {remote.size / 1024**2:.0f} MB, "
          f"{pf.metadata.num_rows}행, row group {pf.metadata.num_row_groups}개")

    saved = []
    for gi in range(pf.metadata.num_row_groups):
        if len(saved) >= args.count:
            break
        table = pf.read_row_group(gi, columns=["audio", "transcripts"])
        for row in table.to_pylist():
            if len(saved) >= args.count:
                break
            blob = (row.get("audio") or {}).get("bytes")
            text = row.get("transcripts") or ""
            if not blob:
                continue
            try:
                info = sf.info(io.BytesIO(blob))
            except Exception:
                continue
            if not (args.min_sec <= info.duration <= args.max_sec):
                continue
            idx = len(saved)
            dst = out_dir / f"conv_{idx:03d}.wav"
            dst.write_bytes(blob)
            saved.append((dst, round(info.duration, 1), text))
        print(f"  row group {gi:>2}: 누적 {len(saved)}개  "
              f"({remote.bytes_fetched / 1024**2:.0f} MB 수신)")

    if not saved:
        print("\n아무것도 받지 못했습니다. --min-sec / --max-sec 를 넓혀 보세요.")
        return 1

    (out_dir / "_출처.txt").write_text(
        SOURCE_NOTE + "".join(f"  {p.name}  {d}초  {t[:60]}\n" for p, d, t in saved),
        encoding="utf-8")

    total = sum(d for _, d, _ in saved)
    print(f"\n저장: {out_dir}  ({len(saved)}개, 합계 {total:.0f}초)")
    print(f"  {remote.summary()}")
    print("\n담긴 대화 예시:")
    for p, d, t in saved[:4]:
        print(f"  [{d:5.1f}s] {t[:66]}")

    print("\n다음 단계:")
    print("  .venv\\Scripts\\python.exe scripts\\validate_normal_calls.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
