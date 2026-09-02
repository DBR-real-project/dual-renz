"""
RAG 사례 색인 재생성
담당: 이상원

## 왜 별도 스크립트인가

`data_seed/scam_cases.json`을 고친 뒤 색인을 시드와 맞추는 방법은 두 가지다.

1. **사례를 추가만 한 경우** — 아무것도 안 해도 된다. `ScamCaseRetriever`가
   시작할 때 색인에 없는 id를 찾아 자동으로 넣는다(`rag.ScamCaseRetriever._sync`).
2. **기존 사례의 문장을 고치거나 지운 경우** — id가 그대로라 자동 동기화가
   못 잡는다. **이때 이 스크립트로 통째로 다시 만든다.**

통짜 재색인을 라이브러리 안에서 못 하는 이유가 있다. 실측(2026-09-02):
같은 프로세스에서 `chromadb.PersistentClient`를 한 번 만든 뒤 DB 디렉터리를
`rmtree`하고 다시 만들어도 `count()`가 그대로 나온다. rust 바인딩이 컬렉션을
메모리에 들고 있어서 `SharedSystemClient.clear_system_cache()`를 불러도 같다.
즉 **재색인은 새 프로세스에서만 유효**하고, 그게 이 스크립트다.

(그리고 `delete_collection()` 후 같은 이름으로 다시 만드는 방법은 쓰면 안 된다 —
인덱스가 어긋나 모든 거리가 0.09~0.11에 몰린 적이 있다. `rag.py` 주석 참고.)

## 실행

    .venv\\Scripts\\python.exe scripts\\build_rag_index.py
    .venv\\Scripts\\python.exe scripts\\build_rag_index.py --check   # 재색인 없이 상태만
"""

import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parent / "src"))

from _console import setup_console  # noqa: E402

setup_console()

from content_analysis.rag import ScamCaseRetriever, load_seed  # noqa: E402


# 재색인이 제대로 됐는지 눈으로 확인하는 표본. 답을 맞히려는 게 아니라
# "질의가 엉뚱한 사례로 가지 않는가"를 보는 용도다.
PROBES = [
    "검찰인데요 본인 명의 대포통장에 연루되셨습니다",
    "매달 배당 확실히 나갑니다 소액부터 시작하세요",
    "엄마 나 폰 액정 깨져서 급하게 돈 좀 보내줘",
    "오늘 점심 뭐 먹을까 회의는 3시로 미뤘어",
]


def main():
    parser = argparse.ArgumentParser(description="RAG 사례 색인 재생성")
    parser.add_argument("--check", action="store_true",
                        help="재색인하지 않고 현재 색인 상태만 본다")
    args = parser.parse_args()

    cases = load_seed()
    retriever = ScamCaseRetriever()
    expected = sum(1 for _ in _iter_docs(cases))

    print(f"시드 사례 {len(cases)}건 -> 문서 {expected}개 예상")

    if args.check:
        collection = retriever._ensure_collection()
        print(f"현재 색인 문서 {collection.count()}개")
    else:
        print("색인을 통째로 다시 만드는 중... (임베딩 모델 로딩에 시간이 걸린다)")
        n = retriever.rebuild()
        print(f"재색인 완료: 문서 {n}개")

    print("\n표본 질의")
    for q in PROBES:
        hits = retriever.search(q, top_k=2)
        print(f"  {q}")
        if not hits:
            print("     (채택 임계값을 넘는 사례 없음)")
        for h in hits:
            print(f"     - {h.case_id:12} {h.title[:38]:38} sim={h.similarity:.3f}")


def _iter_docs(cases):
    """문서 수를 세기 위한 헬퍼. rag 내부 함수를 그대로 쓴다."""
    from content_analysis.rag import _documents_from_case
    for case in cases:
        for d in _documents_from_case(case):
            yield d


if __name__ == "__main__":
    main()
