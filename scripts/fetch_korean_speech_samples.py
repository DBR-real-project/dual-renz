"""
한국어 '진짜 사람 목소리' 정상 통화 샘플 확보
담당: 이상원

## 왜 필요한가

시연에서 초록(낮음) 등급을 보여줄 샘플이 없었다. 저장소의 `normal_call.wav`는
Windows TTS로 만든 것이라 **음성 자체가 합성**이다. AASIST가 이걸 정확히
합성으로 잡아 미디어 위험도 100 → 결과가 '중간'으로 나온다.
판정은 맞는데 초록을 못 보여주는 상황이었다.

해결하려면 진짜 사람이 녹음한 한국어 음성이 있어야 한다. 사람이 직접 녹음하는
방법은 `record_call_sample.py`에 있고, 이 스크립트는 **사람 없이** 같은 목적을
달성하는 경로다: 공개 한국어 음성 코퍼스에서 실제 사람 발화를 가져온다.

## 무엇을 쓰는가

**Zeroth-Korean** (openslr.org/40, CC BY 4.0, 저작자표시하면 재배포 가능).
HuggingFace `kresnik/zeroth_korean`의 test 스플릿을 쓴다. 실제 사람 105명이
녹음한 한국어 읽기 음성이고, test는 화자 10명 / 발화 457개다.

화자 하나(118번)의 발화 5개를 이어 붙여 약 40초짜리 '정상 통화'를 만든다.
**한 화자로 고정하는 이유**는 여러 화자를 섞으면 통화 한 통으로 안 들리기 때문이다.

## 발화를 고른 기준 (실측으로 정했다)

1. **오프라인 분류기 점수가 8개 카테고리 전부 0점** — 정상인데 기법이 뜨면
   시연에서 설명할 거리만 늘어난다. `classify_offline()`으로 90개를 전수 채점해서 골랐다.
2. **STT가 잘 받아쓰는 일상 어휘** — 처음 고른 조합은 "청결한→창렬한",
   "수세미→수쌤"처럼 STT가 틀리면서 엉뚱하게 감정적 압박 32.1점이 떴다.
   뉴스·전문 용어가 든 문장을 빼고 다시 골랐더니 콘텐츠 위험도가 0.0이 됐다.
3. **정치·사건·종교 어휘 제외** — 코퍼스가 뉴스 낭독이라 그대로 쓰면
   "이게 통화예요?" 소리를 듣는다.

실측 결과 (이 스크립트가 만드는 파일):
    AASIST(음성)     8.5 ~ 26.7   → 진짜 목소리로 판정
    콘텐츠 위험도     0.0          → 걸린 기법 없음
    Fraud Risk Score 13.3 / 낮음  → **초록**
비교: 같은 파이프라인에서 TTS `normal_call.wav`는 미디어 100 → 중간(노랑),
`scam_call.wav`는 100 → 높음(빨강).

실행:
    .venv\\Scripts\\python.exe scripts/fetch_korean_speech_samples.py
    .venv\\Scripts\\python.exe scripts/fetch_korean_speech_samples.py --verify
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
OUT_DIR = PROJECT_ROOT / "data" / "korean_calls"
OUT_NAME = "real_normal_call.wav"

# HF가 자동 변환해 두는 parquet. 원본 저장소의 오디오 폴더보다 다루기 쉽다.
PARQUET_URL = (
    "https://huggingface.co/datasets/kresnik/zeroth_korean/resolve/"
    "refs%2Fconvert%2Fparquet/default/test/0000.parquet"
)

DATASET_NAME = "Zeroth-Korean (kresnik/zeroth_korean, test split)"
DATASET_LICENSE = "CC BY 4.0"
DATASET_HOME = "https://www.openslr.org/40/"

# 화자 118번의 발화 5개. 순서는 이어 들었을 때 어색하지 않은 순서로 배치했다.
# 위 docstring의 "발화를 고른 기준" 참고 — 임의로 바꾸면 콘텐츠 0.0이 깨질 수 있다.
UTTERANCES = [
    ("118_003_1105", "가족 이야기 — STT가 100% 정확히 받아쓴다"),
    ("118_003_0285", "동네 이야기 — 구어체('그랬어요')라 통화처럼 들린다"),
    ("118_003_2737", "학교 규정 이야기 — 일상 어휘"),
    ("118_003_1098", "휴대폰 보조금 이야기 — 일상 어휘"),
    ("118_003_1091", "부부 사이 이야기 — 구어체 마무리"),
]

GAP_SEC = 0.4  # 발화 사이 간격. 너무 짧으면 한 문장으로 붙어 STT 구간이 뭉친다


def build(gap_sec: float = GAP_SEC) -> Path:
    import numpy as np
    import pyarrow.parquet as pq
    import soundfile as sf

    wanted = {uid for uid, _ in UTTERANCES}

    remote = HttpRangeFile(PARQUET_URL, chunk_size=1 << 22)
    pf = pq.ParquetFile(remote)
    print(f"원격 parquet {remote.size / 1024**2:.0f} MB, "
          f"{pf.metadata.num_rows}행, row group {pf.metadata.num_row_groups}개")

    # 오디오 컬럼이 무거우므로, 먼저 id 컬럼만 훑어 필요한 row group을 특정한다.
    groups = []
    for gi in range(pf.metadata.num_row_groups):
        ids = set(pf.read_row_group(gi, columns=["id"]).column("id").to_pylist())
        if ids & wanted:
            groups.append(gi)
    print(f"필요한 row group: {groups}")

    found = {}
    for gi in groups:
        table = pf.read_row_group(gi, columns=["id", "text", "audio"])
        for row in table.to_pylist():
            if row["id"] in wanted:
                found[row["id"]] = row

    missing = wanted - set(found)
    if missing:
        raise SystemExit(
            f"발화를 찾지 못했습니다: {sorted(missing)}\n"
            "  데이터셋이 갱신돼 id가 바뀌었을 수 있습니다. UTTERANCES를 다시 고르세요."
        )

    chunks, sr, texts = [], None, []
    for uid, why in UTTERANCES:
        row = found[uid]
        data, s = sf.read(io.BytesIO(row["audio"]["bytes"]), dtype="float32")
        if data.ndim > 1:  # 혹시 스테레오면 모노로
            data = data.mean(axis=1)
        sr = s
        chunks.append(data)
        chunks.append(np.zeros(int(gap_sec * s), dtype="float32"))
        texts.append((uid, why, row["text"], len(data) / s))

    wav = np.concatenate(chunks[:-1])  # 마지막 간격은 버린다

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / OUT_NAME
    sf.write(out_path, wav, sr, subtype="PCM_16")

    print(f"\n생성: {out_path}")
    print(f"  {len(wav) / sr:.1f}초, {sr}Hz 모노, {out_path.stat().st_size / 1024:.0f} KB")
    print(f"  {remote.summary()}")
    print("\n담긴 발화:")
    for uid, why, text, sec in texts:
        print(f"  [{sec:4.1f}s] {uid}  {why}")
        print(f"          {text}")

    # CC BY 4.0은 저작자 표시가 조건이다. 파일만 돌아다녀도 출처를 알 수 있게 남긴다.
    (OUT_DIR / (Path(OUT_NAME).stem + "_출처.txt")).write_text(
        f"{OUT_NAME}\n\n"
        f"출처: {DATASET_NAME}\n"
        f"라이선스: {DATASET_LICENSE}\n"
        f"홈페이지: {DATASET_HOME}\n\n"
        "실제 사람이 녹음한 한국어 음성에서 한 화자(118번)의 발화 "
        f"{len(UTTERANCES)}개를 이어 붙여 만든 파일입니다.\n"
        "DualGuard 시연에서 '진짜 목소리 + 정상 대화 = 낮음(초록)'을 보여주는 용도입니다.\n"
        "실제 통화 녹취가 아니므로 통화 사례로 인용하지 마십시오.\n\n"
        "담긴 발화:\n"
        + "".join(f"  {uid}  {text}\n" for uid, _, text, _ in texts),
        encoding="utf-8",
    )
    return out_path


def verify(path: Path) -> None:
    """만든 파일이 실제로 '초록'으로 판정되는지 확인한다."""
    sys.path.insert(0, str(PROJECT_ROOT / "src"))
    from content_analysis.content_risk import classify_offline
    from media_detection.media_risk import get_audio_spoof_score

    print("\n--- 검증 ---")
    spoof = get_audio_spoof_score(str(path))
    verdict = "진짜 목소리" if spoof < 50 else "합성으로 판정됨 (!)"
    print(f"AASIST 음성 스푸핑 점수: {spoof:.2f} / 100  → {verdict}")

    # 원문 기준 콘텐츠 위험도(STT 오차를 뺀 값). 실제 파이프라인 값은 analyze_call.py로.
    from pathlib import Path as P
    note = OUT_DIR / (P(OUT_NAME).stem + "_출처.txt")
    texts = [ln.split("  ", 2)[-1].strip()
             for ln in note.read_text(encoding="utf-8").splitlines()
             if ln.startswith("  118_")]
    br = classify_offline(" ".join(texts))
    top = max(br.category_scores.values()) if br.category_scores else 0.0
    print(f"콘텐츠 위험도(원문 기준): {br.content_risk:.1f} / 100, 최고 카테고리 {top:.1f}")

    if spoof >= 50 or top > 10:
        print("\n[주의] 초록이 안 나올 수 있습니다. UTTERANCES를 다시 고르세요.")
    else:
        print("\n초록(낮음) 조건 충족.")

    print("\n전체 파이프라인으로 최종 확인:")
    print(f"  .venv\\Scripts\\python.exe scripts\\analyze_call.py --input {path}")


def main():
    parser = argparse.ArgumentParser(
        description="공개 코퍼스에서 진짜 사람 목소리 정상 통화 샘플 만들기")
    parser.add_argument("--gap", type=float, default=GAP_SEC,
                        help="발화 사이 간격(초)")
    parser.add_argument("--verify", action="store_true",
                        help="만든 뒤 AASIST·콘텐츠 점수까지 확인 (모델 로드 때문에 느리다)")
    args = parser.parse_args()

    path = build(args.gap)
    if args.verify:
        verify(path)


if __name__ == "__main__":
    main()
