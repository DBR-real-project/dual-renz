"""
의미 기반 8대 사회공학 기법 분류 (임베딩, API 키 불필요)
담당: 이상원

왜 만들었나
-----------
키워드 규칙만으로는 한계가 명확했다. 팀 검증셋 21건 실측:

    정탐률 63.6% · 오탐률 30.0% · 정확도 66.7%
    기법별 회수율: 권위 사칭 0/6, 신뢰 구축 0/5, 제3자 확인 회피 0/5, 감정적 압박 0/4

두 가지 원인이 있고 **둘 다 어휘 목록을 늘려서는 못 고친다.**

1. **어휘 변이** — 사기범은 매번 다른 표현을 쓴다. "착수금을 먼저 내셔야",
   "계약금 150만 원만 먼저 넣어두자"는 명백한 금전 요구인데 목록에 없으면 0점이다.
2. **부정문** — 정상 은행이 "저희는 비밀번호를 절대 요구하지 않습니다"라고 **경고**하는
   문장에서 규칙은 "비밀번호"만 보고 80점을 준다. 오탐 3건 중 2건이 이 패턴이었다.

이 모듈은 둘 다 다룬다:
  - 어휘 변이 → 문장 임베딩 유사도로 의미를 본다 (프로토타입: data_seed/category_prototypes.json)
  - 부정문 → 문장 단위로 쪼갠 뒤, 면책·경고 문장은 해당 카테고리 점수를 억제한다
  - 정상 대조군 → 정상 문장과의 유사도가 더 높으면 위험 점수를 깎는다

임베딩 모델은 RAG와 같은 `jhgan/ko-sroberta-multitask`를 쓴다. 이미 받아둔 모델이라
추가 다운로드가 없고, 다국어 모델은 한국어에서 무관한 문장을 동일 문장보다 가깝게
판정한다(근거: rag.py 상단 실측표).

LLM 분류가 가능하면 그쪽이 더 정확하다. 이 모듈은 **키가 없을 때의 기본값**이고,
LLM과 경쟁하는 게 아니라 "키 없이도 쓸 만한 수준"을 만드는 것이 목표다.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from .content_risk import SocialEngineeringCategory

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROTOTYPE_PATH = PROJECT_ROOT / "data_seed" / "category_prototypes.json"
SCAM_CASES_PATH = PROJECT_ROOT / "data_seed" / "scam_cases.json"
EMBED_MODEL = "jhgan/ko-sroberta-multitask"

# 점수는 **마진**(위험 프로토타입 유사도 − 정상 프로토타입 유사도)으로 매긴다.
#
#   왜 유사도 절댓값이 아닌가. scripts/calibrate_semantic.py로 검증셋 156문장의
#   분포를 재보니 최고 카테고리 유사도가 사기 p75=0.635 / 정상 p75=0.605로
#   **거의 겹친다.** 즉 "위험 표현과 얼마나 닮았나"만 봐서는 못 가른다.
#   정상 통화도 은행·경찰 어휘를 실제로 쓰기 때문이다.
#
#   반면 마진은 사기 중앙 +0.125 / 정상 중앙 −0.019로 부호가 갈린다.
#   "위험 쪽에 더 가까운가, 정상 쪽에 더 가까운가"가 판별력 있는 질문이다.
MARGIN_LOW = 0.08     # 이 아래는 0점 (정상 쪽이거나 구분 불가)
MARGIN_HIGH = 0.30    # 이 위는 100점

# 짧은 문장은 점수를 매기지 않는다.
#
#   실측에서 노이즈의 대부분이 짧은 맞장구였다. "알겠습니다, 앱으로 신청할게요"가
#   개인정보 요구에 0.701, "저도 그래요, 대화하는 게 편하네요"가 금전 요구에 0.695로
#   걸렸다. 의미 내용이 거의 없는 문장은 임베딩 공간에서 아무 데나 가깝다.
#   사기 쪽 미탐도 마찬가지로 짧은 문장("괜찮아?", "잘됐다!")이라 양쪽 다 정리된다.
MIN_SENT_CHARS = 14

_SENT_SPLIT = re.compile(r"(?<=[.!?？！。])\s+|\n+")


@dataclass
class SentenceHit:
    """어느 문장이 어느 프로토타입 때문에 걸렸는지 — 대시보드 근거 표시용."""
    sentence: str
    category: str
    score: float
    prototype: str
    suppressed_by: Optional[str] = None


def split_sentences(text: str) -> List[str]:
    """
    문장 단위로 쪼갠다.

    통화 전체를 한 덩어리로 임베딩하면 안 된다. 8문장짜리 통화에서 위험 문장 하나가
    나머지 일곱 문장에 희석돼 유사도가 0.3 밑으로 떨어진다. 반대로 면책 문장도
    묻혀버려서 부정문 억제가 작동하지 않는다.
    """
    out: List[str] = []
    for raw in _SENT_SPLIT.split(text or ""):
        s = raw.strip()
        if not s:
            continue
        # "발신자: 안녕하세요" 처럼 화자 표기가 붙어 있으면 떼어낸다.
        s = re.sub(r"^[^:：]{1,20}[:：]\s*", "", s)
        if len(s) >= 4:
            out.append(s)
    return out or ([text.strip()] if (text or "").strip() else [])


class SemanticClassifier:
    def __init__(self, model_name: str = EMBED_MODEL):
        self.model_name = model_name
        self._model = None
        self._cat_vecs: Dict[str, np.ndarray] = {}
        self._cat_texts: Dict[str, List[str]] = {}
        self._benign_vecs: Optional[np.ndarray] = None
        self._benign_texts: List[str] = []
        self._disclaimer_res: List[re.Pattern] = []
        self._disclaimer_cats: List[str] = []

    # --- 로딩 -------------------------------------------------------

    def _load_prototypes(self) -> None:
        if not PROTOTYPE_PATH.exists():
            raise FileNotFoundError(f"프로토타입 파일이 없습니다: {PROTOTYPE_PATH}")
        d = json.loads(PROTOTYPE_PATH.read_text(encoding="utf-8"))

        texts: Dict[str, List[str]] = {c.value: list(d["categories"].get(c.value, []))
                                       for c in SocialEngineeringCategory}

        # RAG 사례 DB의 실제 화법 예시도 프로토타입으로 함께 쓴다.
        # 사례를 추가하면 분류기도 같이 좋아지는 구조라, 데이터를 한 군데서 관리할 수 있다.
        benign_extra: List[str] = []
        if SCAM_CASES_PATH.exists():
            cases = json.loads(SCAM_CASES_PATH.read_text(encoding="utf-8"))["cases"]
            for case in cases:
                examples = case.get("script_examples", [])
                if str(case.get("type", "")).startswith("정상"):
                    benign_extra.extend(examples)
                    continue
                for tech in case.get("techniques", []):
                    if tech in texts:
                        texts[tech].extend(examples)

        self._cat_texts = {k: sorted(set(v)) for k, v in texts.items()}
        self._benign_texts = sorted(set(d["benign"]["prototypes"]) | set(benign_extra))

        dp = d.get("disclaimer_patterns", {})
        self._disclaimer_res = [re.compile(p) for p in dp.get("negated_request", [])]
        self._disclaimer_cats = list(dp.get("affected_categories", []))

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError(
                "sentence-transformers가 필요합니다: pip install sentence-transformers"
            ) from exc

        self._load_prototypes()
        self._model = SentenceTransformer(self.model_name)

        for cat, texts in self._cat_texts.items():
            self._cat_vecs[cat] = (
                self._model.encode(texts, normalize_embeddings=True)
                if texts else np.zeros((0, 768), dtype=np.float32)
            )
        self._benign_vecs = self._model.encode(
            self._benign_texts, normalize_embeddings=True
        ) if self._benign_texts else None

    # --- 판정 -------------------------------------------------------

    def _is_disclaimer(self, sentence: str) -> bool:
        """'저희는 ~하지 않습니다' 류의 면책·경고 문장인가."""
        return any(r.search(sentence) for r in self._disclaimer_res)

    @staticmethod
    def _to_score(margin: float) -> float:
        if margin <= MARGIN_LOW:
            return 0.0
        return float(min(100.0, (margin - MARGIN_LOW) / (MARGIN_HIGH - MARGIN_LOW) * 100.0))

    def classify(self, text: str) -> Tuple[Dict[str, float], List[SentenceHit]]:
        """
        발화를 8대 카테고리 점수(0~100)와 근거 목록으로 바꾼다.
        카테고리 점수는 문장별 최고점을 취한다(한 문장만 명백해도 잡아야 하므로).
        """
        self._ensure_loaded()
        sentences = [s for s in split_sentences(text) if len(s) >= MIN_SENT_CHARS]
        scores = {c.value: 0.0 for c in SocialEngineeringCategory}
        hits: List[SentenceHit] = []
        if not sentences:
            return scores, hits

        sent_vecs = self._model.encode(sentences, normalize_embeddings=True)

        for si, sent in enumerate(sentences):
            v = sent_vecs[si]

            benign_sim = 0.0
            if self._benign_vecs is not None and len(self._benign_vecs):
                benign_sim = float(np.max(self._benign_vecs @ v))

            disclaimer = self._is_disclaimer(sent)

            for cat, mat in self._cat_vecs.items():
                if not len(mat):
                    continue
                sims = mat @ v
                idx = int(np.argmax(sims))
                score = self._to_score(float(sims[idx]) - benign_sim)

                # 면책·경고 문장이면 '요구' 성격의 카테고리를 억제한다.
                # "비밀번호를 절대 요구하지 않습니다"는 요구가 아니라 경고다.
                if disclaimer and cat in self._disclaimer_cats:
                    if score > 0:
                        hits.append(SentenceHit(sent, cat, 0.0, self._cat_texts[cat][idx],
                                                "면책·경고 문장 (요구가 아님)"))
                    continue

                if score > 0:
                    scores[cat] = max(scores[cat], score)
                    hits.append(SentenceHit(sent, cat, round(score, 1),
                                            self._cat_texts[cat][idx]))

        return scores, hits

    def legitimacy_evidence(self, text: str) -> List[str]:
        """
        '정상 기관·정상 대화'라는 적극적 근거를 찾는다.

        왜 필요한가: 진짜 경찰·은행 통화도 권위·긴급 어휘를 실제로 쓴다. 기법 점수만
        보면 사칭과 구분이 안 된다(실측: 진짜 경찰 통화가 97.9점). 사람이 이 둘을
        가르는 근거는 기법이 아니라 **정상 절차의 흔적**이다 — 공식 채널로 확인하라고
        먼저 안내하거나, 우편·앱 같은 정식 경로를 제시하거나, 요구하지 않는다고 명시하거나.
        사기범은 이런 말을 하지 않는다. 오히려 반대로 말한다.
        """
        self._ensure_loaded()
        found: List[str] = []
        for sent in split_sentences(text):
            if self._is_disclaimer(sent):
                found.append(sent[:70])
                continue
            if len(sent) < MIN_SENT_CHARS or self._benign_vecs is None:
                continue
            v = self._model.encode([sent], normalize_embeddings=True)[0]
            ben = float(np.max(self._benign_vecs @ v))
            best = max((float(np.max(m @ v)) for m in self._cat_vecs.values() if len(m)),
                       default=0.0)
            # 정상 표현과 확실히 닮았고, 위험 표현보다 뚜렷하게 가까울 때만 근거로 센다.
            if ben >= 0.62 and ben - best >= 0.10:
                found.append(sent[:70])
        return found


_shared: Optional[SemanticClassifier] = None


def get_shared_classifier() -> SemanticClassifier:
    global _shared
    if _shared is None:
        _shared = SemanticClassifier()
    return _shared


def release_model() -> None:
    """메모리 회수. 파이프라인이 단계 사이에서 호출한다."""
    global _shared
    if _shared is not None:
        _shared._model = None
        _shared._cat_vecs = {}
        _shared._benign_vecs = None
        _shared = None
    import gc
    gc.collect()


def is_available() -> bool:
    try:
        import sentence_transformers  # noqa: F401
    except ImportError:
        return False
    return PROTOTYPE_PATH.exists()


def classify(text: str) -> Tuple[Dict[str, float], List[SentenceHit]]:
    return get_shared_classifier().classify(text)
