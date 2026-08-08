"""
RAG 기반 실제 사기 사례 참조 (ChromaDB)
담당: 이상원

기획서 [Phase 1-1] *"국내 로맨스 스캠 실제 판례·언론 보도 데이터를 RAG 아키텍처로
실시간 결합하여, LLM 분류의 근거를 실제 사례에 기반해 강화하는 검증 레이어"*

역할이 두 가지다:
  1. **근거 제시** — 대시보드에서 "이 발화는 아래 수법과 유사합니다"를 보여준다.
     점수만 있는 경고보다 실제 수법과 대조해 주는 쪽이 훨씬 설득력 있다.
  2. **분류 보강** — 유사 사례의 기법 태그를 LLM 분류 결과와 대조해,
     LLM이 놓친 카테고리를 잡아낸다.

임베딩 모델: `jhgan/ko-sroberta-multitask` (한국어 전용, 첫 실행 시 자동 다운로드)

  다국어 모델로는 안 된다. 세 모델을 같은 문장쌍으로 실측한 결과:

  | 문장쌍 | multilingual-MiniLM | multilingual-e5-small | **ko-sroberta** |
  |---|---|---|---|
  | 거의 동일한 잡담 | 0.856 | 0.991 | **0.992** |
  | 다른 잡담끼리 | 0.901 | 0.869 | **0.377** |
  | 잡담 vs 사기 화법 | 0.909 | 0.845 | **0.068** |
  | 사기 화법끼리 | 0.955 | 0.952 | **0.829** |
  | 사기 vs 잡담 | 0.468 | 0.816 | **0.026** |

  다국어 모델 둘은 **무관한 문장을 동일 문장보다 가깝게** 본다(0.909 > 0.856).
  이 상태로는 임계값을 어디에 두든 정상 통화가 사기 사례에 걸린다.
  ko-sroberta는 무관한 문장을 0.07 이하로 밀어내 경계가 명확하다.

저장 위치:
  data/chroma/ (git 제외). 없으면 data_seed/scam_cases.json에서 자동으로 만든다.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SEED_PATH = PROJECT_ROOT / "data_seed" / "scam_cases.json"
DB_PATH = PROJECT_ROOT / "data" / "chroma"
COLLECTION = "scam_cases"

EMBED_MODEL = "jhgan/ko-sroberta-multitask"


@dataclass
class CaseMatch:
    case_id: str
    title: str
    type: str
    techniques: List[str]
    summary: str
    matched_text: str
    distance: float
    source: str

    @property
    def similarity(self) -> float:
        """코사인 거리를 0~1 유사도로 뒤집는다 (표시용)."""
        return max(0.0, 1.0 - self.distance)

    def as_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "title": self.title,
            "type": self.type,
            "techniques": self.techniques,
            "summary": self.summary,
            "matched_text": self.matched_text,
            "similarity": round(self.similarity, 3),
            "source": self.source,
        }


def load_seed(path: Path = SEED_PATH) -> List[dict]:
    if not path.exists():
        raise FileNotFoundError(f"사례 데이터가 없습니다: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["cases"]


def _documents_from_case(case: dict) -> List[Dict[str, str]]:
    """
    사례 하나를 여러 검색 단위로 쪼갠다.

    요약문 전체를 한 덩어리로 넣으면 통화의 짧은 한 문장과는 유사도가 잘 안 붙는다.
    실제 화법 예시를 개별 문서로 넣어야 STT 세그먼트와 직접 매칭된다.
    """
    docs = [{"text": case["summary"], "kind": "summary"}]
    for ex in case.get("script_examples", []):
        docs.append({"text": ex, "kind": "script"})
    return docs


class ScamCaseRetriever:
    def __init__(self, db_path: Path = DB_PATH, embed_model: str = EMBED_MODEL):
        self.db_path = Path(db_path)
        self.embed_model = embed_model
        self._collection = None

    def _ensure_collection(self, rebuild: bool = False):
        if self._collection is not None and not rebuild:
            return self._collection

        try:
            import chromadb
            from chromadb.utils import embedding_functions
        except ImportError as exc:
            raise RuntimeError(
                "chromadb / sentence-transformers가 필요합니다: "
                "pip install chromadb sentence-transformers"
            ) from exc

        # 재색인은 DB 디렉터리를 통째로 지우고 다시 만든다.
        #
        # delete_collection() 후 같은 이름으로 재생성하면 인덱스가 어긋나는 경우가 있다.
        # 실측 증상: 재색인 뒤 모든 거리가 0.09~0.11에 몰리고, 시드에 그대로 들어 있는
        # 문장을 질의해도 상위에 나오지 않았다(= 질의 임베딩과 저장 임베딩 불일치).
        # 디렉터리를 지우면 확실하게 초기화된다.
        if rebuild and self.db_path.exists():
            import shutil
            shutil.rmtree(self.db_path, ignore_errors=True)

        self.db_path.mkdir(parents=True, exist_ok=True)
        client = chromadb.PersistentClient(path=str(self.db_path))
        ef = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=self.embed_model
        )

        collection = client.get_or_create_collection(
            name=COLLECTION,
            embedding_function=ef,
            metadata={"hnsw:space": "cosine"},
        )

        if collection.count() == 0:
            self._index(collection)

        self._collection = collection
        return collection

    def _index(self, collection) -> int:
        cases = load_seed()
        ids, docs, metas = [], [], []
        for case in cases:
            for i, d in enumerate(_documents_from_case(case)):
                ids.append(f"{case['id']}::{d['kind']}::{i}")
                docs.append(d["text"])
                metas.append({
                    "case_id": case["id"],
                    "title": case["title"],
                    "type": case["type"],
                    # Chroma 메타데이터는 스칼라만 허용하므로 리스트는 문자열로 접는다
                    "techniques": ",".join(case.get("techniques", [])),
                    "summary": case["summary"],
                    "source": case.get("source", ""),
                    "kind": d["kind"],
                })
        collection.add(ids=ids, documents=docs, metadatas=metas)
        return len(ids)

    def rebuild(self) -> int:
        """시드 파일을 고친 뒤 다시 색인한다."""
        self._collection = None
        collection = self._ensure_collection(rebuild=True)
        return collection.count()

    def search(
        self,
        text: str,
        top_k: int = 3,
        max_distance: float = 0.45,
        exclude_normal: bool = False,
    ) -> List[CaseMatch]:
        """
        발화와 유사한 사례를 찾는다.

        max_distance: 코사인 거리 상한(0.45 = 유사도 0.55 이상만 채택).

          ko-sroberta 기준 실측 분포는 진짜 매칭 0.83~0.99, 무관 0.03~0.07로
          경계가 넓다. 0.55는 그 사이 어디에 둬도 되는 값 중 보수적인 쪽이다.

        exclude_normal: 정상 대조군 사례를 결과에서 뺀다.
        """
        if not (text or "").strip():
            return []
        collection = self._ensure_collection()
        n = min(max(top_k * 3, 5), max(collection.count(), 1))
        res = collection.query(query_texts=[text], n_results=n)

        rows = list(zip(res["documents"][0], res["metadatas"][0], res["distances"][0]))

        # 가장 가까운 이웃이 정상 대조군이면 사기 사례를 내보내지 않는다.
        #
        # 거리 임계값만으로는 부족하다. 실측에서 "점심 뭐 드셨어요 날씨가 참 좋네요"가
        # 로맨스 스캠 사례에 유사도 0.91로 걸렸다 — 사기 통화의 친밀감 형성 화법과
        # 일상 잡담이 임베딩 공간에서 실제로 가깝기 때문이다. 임계값을 더 올리면
        # 진짜 사기 매칭까지 잘려나간다.
        #
        # 그래서 "임계값 넘으면 채택"이 아니라 "가장 가까운 클래스가 무엇인가"로 판단한다.
        # 정상 대조군을 시드에 충분히 넣어두는 것이 이 방식의 전제다.
        if exclude_normal and rows and rows[0][1]["type"].startswith("정상"):
            return []

        matches: List[CaseMatch] = []
        seen = set()
        for doc, meta, dist in rows:
            if dist > max_distance:
                continue
            if exclude_normal and meta["type"].startswith("정상"):
                continue
            if meta["case_id"] in seen:      # 사례당 가장 가까운 문서 하나만
                continue
            seen.add(meta["case_id"])
            matches.append(CaseMatch(
                case_id=meta["case_id"],
                title=meta["title"],
                type=meta["type"],
                techniques=[t for t in meta["techniques"].split(",") if t],
                summary=meta["summary"],
                matched_text=doc,
                distance=float(dist),
                source=meta.get("source", ""),
            ))
            if len(matches) >= top_k:
                break
        return matches

    def technique_hints(self, text: str, top_k: int = 3) -> Dict[str, float]:
        """
        유사 사례들의 기법 태그를 유사도로 가중해 모은다.
        LLM 분류가 놓친 카테고리를 잡아내는 보조 신호로 쓴다.
        """
        hints: Dict[str, float] = {}
        for m in self.search(text, top_k=top_k, exclude_normal=True):
            for tech in m.techniques:
                hints[tech] = max(hints.get(tech, 0.0), m.similarity)
        return hints


_shared: Optional[ScamCaseRetriever] = None


def get_shared_retriever() -> ScamCaseRetriever:
    global _shared
    if _shared is None:
        _shared = ScamCaseRetriever()
    return _shared


def search_cases(text: str, top_k: int = 3, **kwargs) -> List[CaseMatch]:
    return get_shared_retriever().search(text, top_k=top_k, **kwargs)


def is_available() -> bool:
    try:
        import chromadb  # noqa: F401
        import sentence_transformers  # noqa: F401
    except ImportError:
        return False
    return SEED_PATH.exists()
