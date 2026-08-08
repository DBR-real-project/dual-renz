"""
LLM 기반 8대 사회공학 기법 분류
담당: 이상원 (원래 강동연 파트지만 전체 통합을 위해 구현)

기획서: *"LLM이 8대 사회공학 기법을 분류합니다"*, *"Claude API 또는 Gemini API"*

Claude Messages API의 **구조화 출력(structured outputs)** 을 쓴다. JSON 스키마를 주면
응답이 그 형태로 보장되므로, 프롬프트에 "JSON만 출력하세요"라고 빌고 파싱 실패를
재시도로 때우는 코드가 필요 없다.

  주의: 구조화 출력 스키마는 minimum/maximum 같은 수치 제약을 지원하지 않는다.
        0~100 범위 검증은 여기(파이썬)에서 한다.

호출 단위:
  통화 전체가 아니라 **STT 세그먼트 단위**로 부른다. 기획서 결과 대시보드가 구간별
  타임라인을 요구하기 때문이다. 다만 세그먼트 하나만 떼어 보면 맥락이 없어서
  ("네 알겠습니다"만 보면 판단 불가) 앞뒤 세그먼트를 함께 넘긴다.

비용/지연:
  세그먼트마다 API를 부르므로 통화 하나에 수십 번 호출된다. effort를 low로 두고
  시스템 프롬프트에 캐시 breakpoint를 걸어 반복 비용을 줄인다.

API 키가 없으면:
  예외를 던지지 않고 content_risk.classify_by_keywords()로 폴백한다.
  (해커톤 데모 중 키가 없거나 네트워크가 막혀도 파이프라인이 죽지 않게)
"""

import os
from typing import Dict, List, Optional

from .content_risk import (
    CATEGORY_LABELS_KO,
    ContentRiskBreakdown,
    SocialEngineeringCategory,
    classify_by_keywords,
    compute_content_risk,
)

DEFAULT_MODEL = "claude-opus-5"

# 분류는 짧은 발화 하나를 8개 축으로 점수 매기는 단순 작업이라 low로 충분하다.
# 판정이 뭉툭하다고 느껴지면 medium으로 올릴 것 (비용/지연은 올라간다).
DEFAULT_EFFORT = "low"

_CATEGORY_DESCRIPTIONS = {
    SocialEngineeringCategory.URGENCY:
        "긴급성 조성. 즉시 행동하지 않으면 불이익이 생긴다는 압박, 시한 설정.",
    SocialEngineeringCategory.AUTHORITY:
        "권위 사칭. 검찰·경찰·금융감독원·은행 등 공신력 있는 기관이나 직위를 사칭.",
    SocialEngineeringCategory.MONEY_TRANSFER:
        "금전 이체 요구. 송금·입금·안전계좌 예치·투자금 납입 등 돈을 옮기라는 요구.",
    SocialEngineeringCategory.CREDENTIALS:
        "개인정보·OTP 요구. 인증번호, 보안카드, 계좌번호, 주민번호 등 인증수단 요구.",
    SocialEngineeringCategory.SECRECY:
        "비밀 유지 강요. 가족·지인에게 알리지 말라는 요구, 발설 시 불이익 경고.",
    SocialEngineeringCategory.EMOTIONAL_PRESSURE:
        "감정적 압박. 가족의 위험 암시, 체포·구속·처벌 위협, 죄책감이나 공포 유발.",
    SocialEngineeringCategory.TRUST_BUILDING:
        "신뢰 구축 화법. 이름·생년월일·주소 등 이미 아는 정보를 언급해 신뢰를 얻으려는 시도.",
    SocialEngineeringCategory.ISOLATION:
        "제3자 확인 회피 유도. 전화를 끊지 말라, 은행·경찰에 직접 확인하지 말라는 요구.",
}

SYSTEM_PROMPT = """당신은 한국어 보이스피싱·로맨스 스캠 통화를 분석하는 보안 분류기입니다.

통화 발화 한 구간을 받아, 아래 8가지 사회공학 기법이 각각 얼마나 강하게 나타나는지
0~100 점수로 매기십시오.

{categories}

채점 기준:
- 0    해당 기법의 징후가 전혀 없음
- 1~30 약한 암시가 있으나 정상 대화로도 설명 가능
- 31~70 해당 기법으로 볼 만한 표현이 분명히 있음
- 71~100 해당 기법의 전형적인 수법이 명확히 드러남

중요한 판단 원칙:
- 정상적인 업무·일상 대화는 모든 항목이 0에 가까워야 합니다. 사기가 아닌 대화에
  높은 점수를 주면 사용자가 헛경보를 받게 되고, 서비스 신뢰가 무너집니다.
- 은행이나 기관이 실제로 할 수 있는 정상 안내와, 사칭 수법을 구분하십시오.
  예를 들어 "본인 확인을 위해 성함을 말씀해 주세요"는 정상이지만
  "OTP 번호를 불러주세요"는 정상 기관이 절대 요구하지 않습니다.
- 앞뒤 맥락이 주어지면 함께 고려하되, **점수는 target 구간에 대해서만** 매기십시오.
- 판단 근거가 된 표현을 evidence에 원문 그대로 인용하십시오. 없으면 빈 배열."""

_SCHEMA = {
    "type": "object",
    "properties": {
        **{
            cat.value: {
                "type": "integer",
                "description": f"{CATEGORY_LABELS_KO[cat]} 점수 (0~100)",
            }
            for cat in SocialEngineeringCategory
        },
        "evidence": {
            "type": "array",
            "description": "판단 근거가 된 발화 원문 인용 (최대 5개)",
            "items": {"type": "string"},
        },
        "summary": {
            "type": "string",
            "description": "이 구간이 왜 위험한지(또는 왜 정상인지) 한 문장",
        },
    },
    "required": [c.value for c in SocialEngineeringCategory] + ["evidence", "summary"],
    "additionalProperties": False,
}


def _build_system() -> str:
    lines = [
        f"{i}. {cat.value} — {CATEGORY_LABELS_KO[cat]}: {_CATEGORY_DESCRIPTIONS[cat]}"
        for i, cat in enumerate(SocialEngineeringCategory, 1)
    ]
    return SYSTEM_PROMPT.format(categories="\n".join(lines))


class LLMClassifier:
    """Anthropic 클라이언트를 한 번만 만들어 재사용한다."""

    def __init__(self, model: str = DEFAULT_MODEL, effort: str = DEFAULT_EFFORT):
        self.model = model
        self.effort = effort
        self._client = None
        self._system = _build_system()

    @property
    def available(self) -> bool:
        """API 키/자격증명이 있고 SDK가 설치돼 있는지."""
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return False
        if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
            return True
        # `ant auth login` 프로필도 SDK가 자동으로 찾는다.
        from pathlib import Path
        return (Path.home() / ".config" / "anthropic" / "credentials").exists()

    def _ensure_client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic()
        return self._client

    def classify(
        self,
        text: str,
        context_before: str = "",
        context_after: str = "",
    ) -> ContentRiskBreakdown:
        """
        발화 한 구간을 8대 카테고리로 분류한다.
        context_before/after는 판단용 맥락일 뿐, 점수는 text에 대해서만 매겨진다.
        """
        client = self._ensure_client()

        parts = []
        if context_before:
            parts.append(f"[앞 구간]\n{context_before}")
        parts.append(f"[target — 이 구간을 채점하십시오]\n{text}")
        if context_after:
            parts.append(f"[뒤 구간]\n{context_after}")

        response = client.messages.create(
            model=self.model,
            max_tokens=2000,
            system=[{
                "type": "text",
                "text": self._system,
                # 세그먼트마다 같은 시스템 프롬프트를 다시 보내므로 캐시한다.
                "cache_control": {"type": "ephemeral"},
            }],
            output_config={
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": _SCHEMA},
            },
            messages=[{"role": "user", "content": "\n\n".join(parts)}],
        )

        if response.stop_reason == "refusal":
            raise RuntimeError(
                "모델이 요청을 거부했습니다. 분류 대상 텍스트를 확인하세요. "
                f"(category={getattr(response.stop_details, 'category', None)})"
            )

        import json
        raw = next(b.text for b in response.content if b.type == "text")
        data = json.loads(raw)

        scores: Dict[str, float] = {}
        for cat in SocialEngineeringCategory:
            v = float(data.get(cat.value, 0))
            # 스키마가 수치 범위를 강제하지 못하므로 여기서 자른다.
            scores[cat.value] = max(0.0, min(100.0, v))

        evidence = [str(e) for e in (data.get("evidence") or [])][:5]
        breakdown = compute_content_risk(
            scores,
            matched_terms={"llm_evidence": evidence} if evidence else {},
            is_llm=True,
        )
        # 대시보드에 그대로 띄울 한 줄 설명
        breakdown.matched_terms.setdefault("llm_summary", [])
        if data.get("summary"):
            breakdown.matched_terms["llm_summary"] = [str(data["summary"])]
        return breakdown


_shared: Optional[LLMClassifier] = None


def get_shared_classifier(model: str = DEFAULT_MODEL) -> LLMClassifier:
    global _shared
    if _shared is None or _shared.model != model:
        _shared = LLMClassifier(model=model)
    return _shared


def is_available() -> bool:
    return get_shared_classifier().available


def classify_segment(
    text: str,
    context_before: str = "",
    context_after: str = "",
    fallback: bool = True,
) -> ContentRiskBreakdown:
    """
    LLM으로 분류하되, 쓸 수 없거나 실패하면 키워드 규칙으로 폴백한다.
    반환값의 is_llm으로 어느 쪽이 쓰였는지 알 수 있다.
    """
    clf = get_shared_classifier()
    if clf.available:
        try:
            return clf.classify(text, context_before, context_after)
        except Exception:
            if not fallback:
                raise
    elif not fallback:
        raise RuntimeError(
            "ANTHROPIC_API_KEY가 없습니다. 환경변수로 설정하거나 `ant auth login`을 실행하세요."
        )
    return classify_by_keywords(text)


def classify_transcript(segments: List, use_context: bool = True) -> List[ContentRiskBreakdown]:
    """
    STT 세그먼트 리스트를 순서대로 분류한다.
    segments: content_analysis.stt.TranscriptSegment 리스트 (또는 .text를 가진 객체)
    """
    texts = [getattr(s, "text", str(s)).strip() for s in segments]
    out = []
    for i, t in enumerate(texts):
        before = texts[i - 1] if (use_context and i > 0) else ""
        after = texts[i + 1] if (use_context and i + 1 < len(texts)) else ""
        out.append(classify_segment(t, before, after))
    return out
