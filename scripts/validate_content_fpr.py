"""
콘텐츠 분류기의 **도메인 밖 오탐률** 실측
담당: 이상원

## 왜 필요한가

콘텐츠 분류 성능(정확도 90.5%)은 팀이 직접 만든 시나리오 21건에서 잰 값이다.
"우리가 만든 문제를 우리가 푼 것 아니냐"는 지적에 답하려면, **우리가 만들지 않은
한국어 문장**에 넣었을 때 얼마나 헛경보를 내는지를 봐야 한다.

영상 쪽에서 DFDC로 크로스도메인을 잰 것과 같은 취지다.

## 무엇을 쓰는가

Zeroth-Korean(CC BY 4.0) test 스플릿 457문장. 뉴스·책 낭독이라 **사기와는 무관**하고,
우리가 손대지 않은 문장이다. 즉 여기서 임계값을 넘는 건 전부 오탐이다.

한 가지 유의점: 뉴스에는 금융·수사 어휘가 자주 나온다("대출금리", "검찰이 수사",
"투자금을 송금"). 이건 분류기에 불리한 조건이지만, **실제 통화에도 그런 말은 나온다.**
정상 은행 상담이나 뉴스를 보며 나누는 대화가 사기로 잡히면 그게 바로 헛경보다.
그래서 일부러 걸러내지 않고 전부 넣는다.

실행:
    .venv\\Scripts\\python.exe scripts/validate_content_fpr.py
    .venv\\Scripts\\python.exe scripts/validate_content_fpr.py --limit 200
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parent / "src"))

from _console import setup_console  # noqa: E402

setup_console()  # cp949 콘솔에서 유니코드 출력 중 죽는 문제 방지 (_console.py)

from _http_range import HttpRangeFile  # noqa: E402

PROJECT_ROOT = SCRIPT_DIR.parent
CACHE = PROJECT_ROOT / "data" / "zeroth_test.parquet"
OUT = PROJECT_ROOT / "docs" / "validation_report_content_fpr.json"

PARQUET_URL = (
    "https://huggingface.co/datasets/kresnik/zeroth_korean/resolve/"
    "refs%2Fconvert%2Fparquet/default/test/0000.parquet"
)

THRESHOLDS = (30.0, 50.0, 70.0)   # 신호등 중간/판정/높음 근처


def load_texts(limit: int) -> list:
    """text 컬럼만 읽는다. parquet은 컬럼별 저장이라 오디오는 안 받는다."""
    import pyarrow.parquet as pq

    if CACHE.exists():
        pf = pq.ParquetFile(str(CACHE))
        fetched = 0.0
    else:
        remote = HttpRangeFile(PARQUET_URL, chunk_size=1 << 22)
        pf = pq.ParquetFile(remote)
        fetched = 0.0
        texts = []
        for gi in range(pf.metadata.num_row_groups):
            texts += pf.read_row_group(gi, columns=["text"]).column("text").to_pylist()
        fetched = remote.bytes_fetched / 1024 ** 2
        print(f"  원격에서 {fetched:.1f} MB 수신 (오디오 제외, 텍스트 컬럼만)")
        return [t for t in texts if t][:limit or None]

    texts = []
    for gi in range(pf.metadata.num_row_groups):
        texts += pf.read_row_group(gi, columns=["text"]).column("text").to_pylist()
    return [t for t in texts if t][:limit or None]


def main():
    parser = argparse.ArgumentParser(description="콘텐츠 분류기 도메인 밖 오탐률 실측")
    parser.add_argument("--limit", type=int, default=0, help="문장 수 제한 (0=전부)")
    args = parser.parse_args()

    from content_analysis.content_risk import classify_offline

    print("도메인 밖 한국어 문장으로 콘텐츠 분류기 오탐률 측정")
    print("  출처: Zeroth-Korean test (CC BY 4.0). 사기와 무관한 낭독 문장.\n")

    texts = load_texts(args.limit)
    print(f"  문장 {len(texts)}개\n")

    rows = []
    for i, text in enumerate(texts, 1):
        br = classify_offline(text)
        rows.append({
            "text": text[:120],
            "risk": round(br.content_risk, 2),
            "top": br.top_category,
            "top_score": round(br.top_score, 2),
        })
        if i % 100 == 0:
            print(f"  {i}/{len(texts)}", flush=True)

    print("\n--- 오탐률 (이 문장들은 전부 정상이므로 임계값을 넘으면 오탐) ---")
    fpr = {}
    for th in THRESHOLDS:
        hits = [r for r in rows if r["risk"] >= th]
        fpr[str(th)] = {
            "count": len(hits),
            "rate": round(100.0 * len(hits) / len(rows), 2),
        }
        print(f"  임계값 {th:>4.0f}:  {len(hits):>4}/{len(rows)}건 = {fpr[str(th)]['rate']:>5.2f}%")

    worst = sorted(rows, key=lambda r: -r["risk"])[:8]
    print("\n--- 가장 높게 잡힌 문장 (오탐 원인 파악용) ---")
    for r in worst:
        print(f"  [{r['risk']:5.1f}] {r['top'] or '-':<15} {r['text'][:64]}")

    cats = Counter(r["top"] for r in rows if r["risk"] >= 50.0 and r["top"])
    if cats:
        print("\n--- 오탐을 유발한 카테고리 (임계값 50 기준) ---")
        for cat, n in cats.most_common():
            print(f"  {cat:<18} {n}건")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "_설명": "우리가 만들지 않은 한국어 문장에서의 오탐률. "
                 "자체 시나리오 21건 성능과 함께 읽어야 한다.",
        "source": "Zeroth-Korean test split (CC BY 4.0)",
        "n_sentences": len(rows),
        "false_positive_rate": fpr,
        "top_offenders": worst,
        "categories_at_50": dict(cats),
        "results": rows,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n상세 저장: {OUT}")
    print("\n※ 뉴스 낭독이라 금융·수사 어휘가 자주 나온다. 분류기에 불리한 조건이지만")
    print("  실제 통화에도 그런 말은 나오므로 걸러내지 않고 측정했다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
