"""
콘텐츠 위험도(content_risk) 산출
담당: 이상원 (통합 스코어링 로직) / 강동연 (LLM 분류)

역할 경계 (기획서 팀원 역할 기준):
  - 강동연: STT 연동 + LLM 화법분석 프롬프트로 **8대 카테고리별 점수를 매기는 것**
  - 이상원: 그 카테고리별 점수를 **하나의 content_risk로 합치고 Fraud Risk Score에
            통합하는 것**

이 모듈은 후자다. 강동연 파트가 완성되면 `classify_with_llm()` 자리에 실제 LLM 호출을
끼우면 되고, 그 아래(집계 공식, 통합)는 그대로 쓸 수 있다.

집계 공식 (상세 기획서 DOCX):
    content_risk = 0.5 × 최고위험카테고리점수 + 0.5 × 상위3개카테고리평균

  왜 이렇게 설계됐는지: 최고점만 쓰면 한 카테고리 오탐에 전체가 끌려가고,
  전체 평균을 쓰면 8개 중 1~2개만 강하게 걸리는 전형적 사기 화법이 희석된다.
  두 개를 반반 섞어 "가장 위험한 신호"와 "위험 신호의 두께"를 함께 본다.

LLM 연동 전까지는 `classify_by_keywords()`가 대역으로 동작한다. 규칙 기반이라
실제 성능은 LLM에 못 미치지만, 해시 기반 난수 더미와 달리 **왜 그 점수가 나왔는지
근거가 남아서** 통합 로직과 대시보드를 제대로 테스트할 수 있다.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple, Union


class SocialEngineeringCategory(str, Enum):
    """기획서 3장 [핵심 기술]의 8대 사회공학 기법."""
    URGENCY = "urgency"                    # ① 긴급성 조성
    AUTHORITY = "authority"                # ② 권위 사칭 (검찰·경찰·금융감독원 등)
    MONEY_TRANSFER = "money_transfer"      # ③ 금전 이체 요구
    CREDENTIALS = "credentials"            # ④ 개인정보·OTP 요구
    SECRECY = "secrecy"                    # ⑤ 비밀 유지 강요
    EMOTIONAL_PRESSURE = "emotional"       # ⑥ 감정적 압박 (가족 위험 암시)
    TRUST_BUILDING = "trust_building"      # ⑦ 사전 정보 언급을 통한 신뢰 구축
    ISOLATION = "isolation"                # ⑧ 제3자 확인 회피 유도


CATEGORY_LABELS_KO: Dict[SocialEngineeringCategory, str] = {
    SocialEngineeringCategory.URGENCY: "긴급성 조성",
    SocialEngineeringCategory.AUTHORITY: "권위 사칭",
    SocialEngineeringCategory.MONEY_TRANSFER: "금전 이체 요구",
    SocialEngineeringCategory.CREDENTIALS: "개인정보·OTP 요구",
    SocialEngineeringCategory.SECRECY: "비밀 유지 강요",
    SocialEngineeringCategory.EMOTIONAL_PRESSURE: "감정적 압박",
    SocialEngineeringCategory.TRUST_BUILDING: "신뢰 구축 화법",
    SocialEngineeringCategory.ISOLATION: "제3자 확인 회피 유도",
}


class ScoreBand(str, Enum):
    """
    카테고리 점수를 4단계로 뭉뚱그린 것. 시나리오별 "기대 점수대"를 사람이
    미리 매길 때 정확한 숫자(예: 63점)를 고르라고 하면 사람마다 기준이 다르고
    무의미한 정밀도가 생긴다. 대신 이 4단계 중 하나만 고르면 된다.

    llm_classifier.SYSTEM_PROMPT의 채점 기준과 **정확히 같은 척도**를 쓴다.
      0    NONE        해당 기법의 징후가 전혀 없음
      1~30 WEAK        약한 암시가 있으나 정상 대화로도 설명 가능
      31~70 CLEAR      해당 기법으로 볼 만한 표현이 분명히 있음
      71~100 DEFINITIVE 해당 기법의 전형적인 수법이 명확히 드러남

    두 곳의 척도가 어긋나면 "기대 점수대"와 "LLM이 실제로 채점하는 기준"이
    달라져서 채점표 자체가 무의미해진다. 한쪽을 바꾸면 반드시 다른 쪽도
    맞출 것 (llm_classifier.py의 채점 기준 문단).
    """
    NONE = "none"
    WEAK = "weak"
    CLEAR = "clear"
    DEFINITIVE = "definitive"


SCORE_BAND_RANGES: Dict[ScoreBand, Tuple[int, int]] = {
    ScoreBand.NONE: (0, 0),
    ScoreBand.WEAK: (1, 30),
    ScoreBand.CLEAR: (31, 70),
    ScoreBand.DEFINITIVE: (71, 100),
}


def score_in_band(score: float, band: Union[ScoreBand, str]) -> bool:
    """점수가 기대 밴드 범위 안에 있는지. band는 ScoreBand든 문자열("weak" 등)이든 받는다."""
    b = band if isinstance(band, ScoreBand) else ScoreBand(band)
    lo, hi = SCORE_BAND_RANGES[b]
    return lo <= score <= hi


def band_range_label(band: Union[ScoreBand, str]) -> str:
    """리포트에 찍을 표시용 문자열. 예: 'clear(31~70)'."""
    b = band if isinstance(band, ScoreBand) else ScoreBand(band)
    lo, hi = SCORE_BAND_RANGES[b]
    return f"{b.value}({lo}~{hi})"


# 규칙 기반 대역용 키워드.
# 주의: 이건 LLM 연동 전까지의 임시 대역이다. 실제 사기 화법은 이 단어들을 안 쓰고도
#       성립하므로, 이 키워드 목록의 재현율을 성능 근거로 제시하면 안 된다.
_KEYWORDS: Dict[SocialEngineeringCategory, List[str]] = {
    SocialEngineeringCategory.URGENCY: [
        "지금 즉시", "즉시", "당장", "빨리", "서둘러", "오늘 안에", "마감", "늦으면",
        "동결", "정지됩니다", "시간이 없", "급하", "바로",
    ],
    SocialEngineeringCategory.AUTHORITY: [
        "금융감독원", "검찰", "경찰", "수사관", "검사", "국세청", "법원", "공무원",
        "은행 직원", "본부", "센터장", "담당 수사", "사건번호",
        "범죄에 연루", "사건에 연루", "명의도용", "대포통장", "피의자", "조사가 필요",
    ],
    SocialEngineeringCategory.MONEY_TRANSFER: [
        "이체", "송금", "입금", "계좌로", "안전계좌", "보증금", "수수료", "예치",
        "투자", "코인", "환전", "출금",
    ],
    SocialEngineeringCategory.CREDENTIALS: [
        "otp", "인증번호", "비밀번호", "보안카드", "주민등록번호", "주민번호",
        "카드번호", "cvc", "공인인증", "계좌번호 알려", "신분증",
    ],
    SocialEngineeringCategory.SECRECY: [
        "알리지 마", "말하지 마", "혼자", "아무에게도", "기밀",
        "발설", "누설", "얘기하면 안",
        # "비밀" 단독은 넣지 않는다. "비밀번호"의 부분 문자열로 걸려서
        # credentials가 아니라 secrecy를 잘못 띄운다 (실측: grade_content_rubric.py의
        # normal_05 — "비밀번호를 절대 요구하지 않으니" 경고문에서 오탐).
        # 대신 실제 "비밀 유지" 의미로만 쓰이는 구체적인 표현을 넣는다.
        "비밀로 해", "비밀 지켜", "우리만의 비밀", "비밀 프로젝트", "이건 비밀",
    ],
    SocialEngineeringCategory.EMOTIONAL_PRESSURE: [
        "가족", "아들", "딸", "자녀", "다칩니다", "위험", "체포", "구속", "처벌",
        "피해자가 됩니다", "책임", "불이익", "사랑", "믿어",
        "연루", "범죄", "벌금", "전과", "신용불량",
    ],
    SocialEngineeringCategory.TRUST_BUILDING: [
        "고객님 성함", "생년월일 확인", "주소가", "다니시는", "알고 있습니다",
        "확인해보니", "조회 결과", "저희 기록",
    ],
    SocialEngineeringCategory.ISOLATION: [
        "직접 확인", "전화 끊지", "끊지 마", "다른 곳에 문의", "은행에 가지",
        "112에 신고하면", "상담원 연결", "확인하실 필요",
    ],
}


@dataclass
class ContentRiskBreakdown:
    """대시보드 '왜 위험한가' 근거 표시용."""
    content_risk: float
    category_scores: Dict[str, float]
    top_category: Optional[str]
    top_score: float
    top3_mean: float
    matched_terms: Dict[str, List[str]] = field(default_factory=dict)
    is_llm: bool = False

    def as_dict(self) -> dict:
        ranked = sorted(self.category_scores.items(), key=lambda kv: -kv[1])
        return {
            "content_risk": round(self.content_risk, 2),
            "top_category": self.top_category,
            "top_category_label": CATEGORY_LABELS_KO.get(
                SocialEngineeringCategory(self.top_category), self.top_category
            ) if self.top_category else None,
            "top_score": round(self.top_score, 2),
            "top3_mean": round(self.top3_mean, 2),
            "category_scores": {k: round(v, 2) for k, v in ranked},
            "matched_terms": self.matched_terms,
            "is_llm": self.is_llm,
        }


def compute_content_risk(
    category_scores: Dict[str, float],
    matched_terms: Optional[Dict[str, List[str]]] = None,
    is_llm: bool = False,
) -> ContentRiskBreakdown:
    """
    8대 카테고리별 점수(0~100)를 받아 content_risk를 산출한다.

        content_risk = 0.5 × 최고점 + 0.5 × 상위3개평균

    강동연 파트의 LLM 분류 결과를 이 함수에 그대로 넣으면 된다.
    누락된 카테고리는 0으로 채운다.
    """
    scores = {c.value: 0.0 for c in SocialEngineeringCategory}
    for k, v in (category_scores or {}).items():
        key = k.value if isinstance(k, SocialEngineeringCategory) else str(k)
        if key not in scores:
            raise ValueError(
                f"알 수 없는 카테고리: {key}. "
                f"허용: {[c.value for c in SocialEngineeringCategory]}"
            )
        if not (0 <= v <= 100):
            raise ValueError(f"{key} 점수가 0~100 범위를 벗어남: {v}")
        scores[key] = float(v)

    ranked = sorted(scores.values(), reverse=True)
    top_score = ranked[0]
    top3_mean = sum(ranked[:3]) / 3.0
    content_risk = 0.5 * top_score + 0.5 * top3_mean

    top_category = None
    if top_score > 0:
        top_category = max(scores.items(), key=lambda kv: kv[1])[0]

    return ContentRiskBreakdown(
        content_risk=round(content_risk, 2),
        category_scores=scores,
        top_category=top_category,
        top_score=top_score,
        top3_mean=top3_mean,
        matched_terms=matched_terms or {},
        is_llm=is_llm,
    )


def _normalize(text: str) -> str:
    """
    공백을 제거한 소문자 문자열.

    STT 출력은 한국어 띄어쓰기가 원문과 다르게 나오는 경우가 많다
    (실측: "대포통장" -> "대포 통장", "안전계좌" -> "안전 계좌", "지금 즉시" -> "지금즉시").
    공백을 무시하고 비교하면 이 흔들림에 걸리지 않는다.
    """
    return "".join((text or "").split()).lower()


def classify_by_keywords(transcript: str) -> ContentRiskBreakdown:
    """
    [LLM 연동 전 대역] 키워드 규칙으로 8대 카테고리 점수를 매긴다.

    점수화: 한 카테고리에서 걸린 키워드 1개당 40점, 최대 100점.
    (1개 걸리면 40, 2개면 80, 3개 이상이면 100 — 여러 표현이 겹칠수록 확신을 높인다)
    """
    raw = (transcript or "").lower()
    normalized = _normalize(transcript)
    scores: Dict[str, float] = {}
    matched: Dict[str, List[str]] = {}

    for cat, words in _KEYWORDS.items():
        hits = [w for w in words
                if w.lower() in raw or _normalize(w) in normalized]
        if hits:
            matched[cat.value] = hits
        scores[cat.value] = min(100.0, 40.0 * len(hits))

    return compute_content_risk(scores, matched_terms=matched, is_llm=False)


def classify_with_llm(
    transcript: str,
    context_before: str = "",
    context_after: str = "",
    fallback: bool = True,
) -> ContentRiskBreakdown:
    """
    LLM으로 8대 카테고리를 분류한다. 구현은 llm_classifier.py에 있다.

    API 키가 없거나 호출이 실패하면 fallback=True일 때 키워드 규칙으로 넘어간다.
    반환값의 is_llm으로 어느 쪽이 쓰였는지 구분할 수 있다.

    (지연 import: llm_classifier가 이 모듈을 import하므로 순환 참조를 피한다)
    """
    from .llm_classifier import classify_segment

    return classify_segment(transcript, context_before, context_after, fallback=fallback)


def get_content_risk(transcript: str, use_llm: bool = False) -> float:
    """content_risk 값(0~100)만 필요할 때 쓰는 간편 함수."""
    if use_llm:
        return classify_with_llm(transcript).content_risk
    return classify_by_keywords(transcript).content_risk
