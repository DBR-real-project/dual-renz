"""
LLM 기반 8대 사회공학 기법 분류
담당: 강동연(프롬프트·Gemini 백엔드) / 이상원(Claude 백엔드·통합)

기획서: *"LLM이 8대 사회공학 기법을 분류합니다"*, *"Claude API 또는 Gemini API"*

기획서가 두 API를 모두 허용하므로 **두 백엔드를 다 구현하고 런타임에 고른다.**
어느 쪽이든 같은 시스템 프롬프트·같은 JSON 스키마를 쓰고 ContentRiskBreakdown을
돌려주므로, 파이프라인 입장에서는 구분이 없다.

| 백엔드      | 환경변수                              | 구조화 출력 방식             |
|-------------|---------------------------------------|------------------------------|
| `anthropic` | `ANTHROPIC_API_KEY` / `ant auth login`| Messages API structured outputs |
| `gemini`    | `GEMINI_API_KEY` / `GOOGLE_API_KEY`   | `response_schema` (JSON 강제) |
| `ollama`    | 없음 (로컬 서버 `localhost:11434`)     | `format`에 JSON 스키마 (Ollama 0.5+) |

  ollama는 실제 서비스용이 아니라 **프롬프트 구조를 로컬에서 공짜로 미리 검증**하는
  용도다. Gemini 무료 티어 쿼터가 막혔을 때 특히 쓸모 있다. 그래서 auto 폴백
  체인에는 넣지 않았다 — 개발자 PC에 우연히 Ollama가 떠 있다고 데모 중 조용히
  그쪽으로 넘어가면 안 되므로, `DUALGUARD_LLM_PROVIDER=ollama`로 명시했을 때만 쓴다.

  둘 다 JSON 스키마를 주면 응답 형태가 보장되므로, 프롬프트에 "JSON만 출력하세요"라고
  빌고 파싱 실패를 재시도로 때우는 코드가 필요 없다. (초기 강동연 프로토타입은
  ```json 펜스를 문자열로 벗겨내고 3회 재시도했는데, 스키마를 쓰면 그 코드가 사라진다)

  주의: 두 API 모두 스키마에서 minimum/maximum 같은 수치 제약을 지원하지 않는다.
        0~100 범위 검증은 여기(파이썬)에서 한다.

백엔드 선택 (`DUALGUARD_LLM_PROVIDER`):
  `auto`(기본) → Claude 키가 있으면 Claude, 없으면 Gemini, 둘 다 없으면 키워드 폴백.
  `anthropic` / `gemini` → 해당 백엔드로 고정. 팀원마다 가진 키가 달라서
  "내 환경에서는 되는데"를 없애려면 이 변수를 명시하는 편이 낫다.

호출 단위:
  통화 전체가 아니라 **STT 세그먼트 단위**로 부른다. 기획서 결과 대시보드가 구간별
  타임라인을 요구하기 때문이다. 다만 세그먼트 하나만 떼어 보면 맥락이 없어서
  ("네 알겠습니다"만 보면 판단 불가) 앞뒤 세그먼트를 함께 넘긴다.

비용/지연:
  세그먼트마다 API를 부르므로 통화 하나에 수십 번 호출된다.
  - Claude: effort를 low로 두고 시스템 프롬프트에 캐시 breakpoint를 걸어 반복 비용을 줄인다.
  - Gemini: 무료 티어는 분당 요청 수 제한이 빡빡하다(실측 시 5 RPM). 통화 하나에
    세그먼트가 수십 개면 429가 난다. `GEMINI_MIN_INTERVAL_SEC`으로 호출 간격을
    강제할 수 있고, 실패해도 예외를 던지지 않고 키워드로 폴백한다.

API 키가 없으면:
  예외를 던지지 않고 content_risk.classify_by_keywords()로 폴백한다.
  (해커톤 데모 중 키가 없거나 네트워크가 막혀도 파이프라인이 죽지 않게)
"""

import os
import threading
import time
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

# Gemini 쪽 기본값. flash 계열을 쓰는 이유는 세그먼트마다 부르는 호출이라
# 지연과 무료 티어 쿼터가 정확도보다 먼저 걸리기 때문이다.
GEMINI_DEFAULT_MODEL = "gemini-flash-latest"

# 무료 티어 분당 요청 제한 대응. 0이면 대기 없음.
# 실측(강동연, 2026-08-06): 무료 키로 연속 호출 시 5 RPM에서 429가 났다.
GEMINI_MIN_INTERVAL_SEC = float(os.environ.get("GEMINI_MIN_INTERVAL_SEC", "0"))

# Ollama 로컬 서버 설정. 설치 시 기본으로 이 주소에 뜬다.
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
OLLAMA_DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.1")
# 로컬 CPU 추론은 클라우드보다 훨씬 느릴 수 있어 넉넉하게 잡는다.
OLLAMA_TIMEOUT_SEC = float(os.environ.get("OLLAMA_TIMEOUT_SEC", "120"))

# auto | anthropic | gemini | ollama
PROVIDER_ENV = "DUALGUARD_LLM_PROVIDER"

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


def _build_user_prompt(text: str, context_before: str, context_after: str) -> str:
    """
    앞뒤 맥락을 붙이되 채점 대상이 어디인지 명시한다.
    두 백엔드가 같은 문자열을 쓰게 해서, 백엔드를 바꿔도 판정이 흔들리지 않게 한다.
    """
    parts = []
    if context_before:
        parts.append(f"[앞 구간]\n{context_before}")
    parts.append(f"[target — 이 구간을 채점하십시오]\n{text}")
    if context_after:
        parts.append(f"[뒤 구간]\n{context_after}")
    return "\n\n".join(parts)


def _finalize(data: dict) -> ContentRiskBreakdown:
    """
    모델이 돌려준 dict를 ContentRiskBreakdown으로 만든다.

    스키마가 수치 범위를 강제하지 못하므로 0~100 자르기는 여기서 한다.
    누락된 카테고리는 compute_content_risk()가 0으로 채운다.
    """
    scores: Dict[str, float] = {}
    for cat in SocialEngineeringCategory:
        try:
            v = float(data.get(cat.value, 0) or 0)
        except (TypeError, ValueError):
            v = 0.0
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


class LLMClassifier:
    """Anthropic 클라이언트를 한 번만 만들어 재사용한다."""

    provider = "anthropic"

    def __init__(self, model: str = DEFAULT_MODEL, effort: str = DEFAULT_EFFORT):
        self.model = model
        self.effort = effort
        self._client = None
        self._system = _build_system()

    @property
    def label(self) -> str:
        return f"Claude ({self.model})"

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
            messages=[{
                "role": "user",
                "content": _build_user_prompt(text, context_before, context_after),
            }],
        )

        if response.stop_reason == "refusal":
            raise RuntimeError(
                "모델이 요청을 거부했습니다. 분류 대상 텍스트를 확인하세요. "
                f"(category={getattr(response.stop_details, 'category', None)})"
            )

        import json
        raw = next(b.text for b in response.content if b.type == "text")
        return _finalize(json.loads(raw))


class GeminiClassifier:
    """
    Gemini 백엔드. Claude 백엔드와 인터페이스(available / classify / label)를 맞춘다.

    google-genai SDK의 `response_schema`를 쓴다. Claude의 structured outputs와 같은
    역할이고, 같은 _SCHEMA를 그대로 넘길 수 있다. `additionalProperties`만 빼는데,
    Gemini 스키마에 없는 키라 현재 SDK(2.17.0)는 조용히 무시하지만 버전에 따라
    거부될 수 있어서 애초에 안 보낸다.
    """

    provider = "gemini"

    def __init__(self, model: str = GEMINI_DEFAULT_MODEL,
                 min_interval_sec: float = GEMINI_MIN_INTERVAL_SEC):
        self.model = model
        self.min_interval_sec = min_interval_sec
        self._client = None
        self._system = _build_system()
        self._lock = threading.Lock()
        self._last_call = 0.0

    @property
    def label(self) -> str:
        return f"Gemini ({self.model})"

    @property
    def available(self) -> bool:
        try:
            from google import genai  # noqa: F401
        except ImportError:
            return False
        return bool(self._api_key())

    @staticmethod
    def _api_key() -> Optional[str]:
        # google-genai는 GOOGLE_API_KEY를 자동으로 읽지만, 팀 문서에는
        # GEMINI_API_KEY로 적혀 있어 둘 다 받는다.
        return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")

    def _ensure_client(self):
        if self._client is None:
            from google import genai
            self._client = genai.Client(api_key=self._api_key())
        return self._client

    def _throttle(self) -> None:
        """무료 티어 RPM 제한 대응. min_interval_sec이 0이면 아무것도 안 한다."""
        if self.min_interval_sec <= 0:
            return
        with self._lock:
            wait = self.min_interval_sec - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()

    def classify(
        self,
        text: str,
        context_before: str = "",
        context_after: str = "",
    ) -> ContentRiskBreakdown:
        from google.genai import types

        client = self._ensure_client()
        self._throttle()

        schema = {k: v for k, v in _SCHEMA.items() if k != "additionalProperties"}

        response = client.models.generate_content(
            model=self.model,
            contents=_build_user_prompt(text, context_before, context_after),
            config=types.GenerateContentConfig(
                system_instruction=self._system,
                response_mime_type="application/json",
                response_schema=schema,
                # 분류 작업이라 창의성이 필요 없다. 같은 통화를 두 번 돌렸을 때
                # 점수가 흔들리면 오탐 원인을 추적할 수 없다.
                temperature=0.0,
            ),
        )

        raw = (response.text or "").strip()
        if not raw:
            # 안전 필터 등으로 텍스트가 비는 경우가 있다. 조용히 0점을 주면
            # 사기 통화를 정상으로 판정해버리므로 예외로 올려 폴백시킨다.
            raise RuntimeError(
                "Gemini가 빈 응답을 돌려줬습니다. "
                f"(finish_reason={getattr(response.candidates[0], 'finish_reason', None) if response.candidates else None})"
            )

        import json
        return _finalize(json.loads(raw))


class OllamaClassifier:
    """
    로컬 LLM(Ollama) 백엔드. Claude/Gemini와 인터페이스(available / classify / label)를
    맞췄지만 목적이 다르다 — 클래스 docstring이 아니라 모듈 docstring 표 아래 설명 참고.

    준비:
        ollama pull llama3.1        # 원하는 모델로 교체 가능 (OLLAMA_MODEL 환경변수)
        ollama serve                # 보통 설치 시 자동으로 백그라운드에 뜬다

    구조화 출력은 Ollama 0.5+ 부터 `format`에 JSON 스키마를 직접 넣는 방식을
    지원한다. 그 이하 버전이면 스키마를 무시하고 자유 텍스트를 낼 수 있어
    파싱이 실패할 수 있다 — 이상하면 `ollama --version`부터 확인할 것.
    """

    provider = "ollama"

    def __init__(self, model: str = OLLAMA_DEFAULT_MODEL, host: str = OLLAMA_HOST):
        self.model = model
        self.host = host
        self._system = _build_system()

    @property
    def label(self) -> str:
        return f"Ollama ({self.model} @ {self.host})"

    @property
    def available(self) -> bool:
        """로컬 서버가 떠 있고 지정한 모델이 pull돼 있는지 확인한다."""
        import json
        import urllib.request

        try:
            req = urllib.request.Request(f"{self.host}/api/tags")
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception:
            # 서버가 안 떠 있거나(가장 흔함), 방화벽, 잘못된 OLLAMA_HOST 등.
            # 여기서 이유를 구분해봐야 호출부는 어차피 폴백하므로 조용히 False.
            return False

        # "llama3.1:8b"처럼 태그가 붙어 있어도 매칭되도록 베이스 이름만 비교한다.
        pulled = {m.get("name", "").split(":")[0] for m in data.get("models", [])}
        return self.model.split(":")[0] in pulled

    def classify(
        self,
        text: str,
        context_before: str = "",
        context_after: str = "",
    ) -> ContentRiskBreakdown:
        import json
        import urllib.request

        # additionalProperties는 Claude 전용 확장이라 Ollama가 얹은 모델이
        # 헷갈릴 수 있어 Gemini와 같은 이유로 뺀다.
        schema = {k: v for k, v in _SCHEMA.items() if k != "additionalProperties"}

        body = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "system", "content": self._system},
                {"role": "user",
                "content": _build_user_prompt(text, context_before, context_after)},
            ],
            "format": schema,
            "stream": False,
            # 분류 작업이라 창의성이 필요 없다. Claude(effort=low)/Gemini(temperature=0)와
            # 같은 이유로 결정적으로 고정한다.
            "options": {"temperature": 0},
        }).encode("utf-8")

        req = urllib.request.Request(
            f"{self.host}/api/chat", data=body,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT_SEC) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        raw = data.get("message", {}).get("content", "")
        if not raw:
            raise RuntimeError(f"Ollama가 빈 응답을 돌려줬습니다: {data}")
        return _finalize(json.loads(raw))


_shared = None


def _resolve_provider() -> str:
    """DUALGUARD_LLM_PROVIDER 해석. 잘못된 값이면 auto로 떨어진다."""
    want = (os.environ.get(PROVIDER_ENV) or "auto").strip().lower()
    return want if want in ("auto", "anthropic", "gemini", "ollama") else "auto"


def get_shared_classifier(model: Optional[str] = None):
    """
    설정된 백엔드의 분류기를 반환한다(프로세스당 1개 재사용).

    auto일 때는 Claude를 먼저 본다 — 구조화 출력 + 프롬프트 캐시로 세그먼트 단위
    반복 호출에 유리하고, 무료 티어 RPM 제한이 없어서 통화 하나를 한 번에 돌릴 수 있다.
    Claude 키가 없으면 Gemini로 내려간다.

    ollama는 auto 체인에 없다 — 개발자 PC에 우연히 떠 있다고 데모 중 조용히
    그쪽으로 넘어가면 안 되므로, DUALGUARD_LLM_PROVIDER=ollama로 명시했을 때만 쓴다.
    """
    global _shared
    want = _resolve_provider()

    if want == "gemini":
        candidates = [GeminiClassifier]
    elif want == "anthropic":
        candidates = [LLMClassifier]
    elif want == "ollama":
        candidates = [OllamaClassifier]
    else:
        candidates = [LLMClassifier, GeminiClassifier]

    for cls in candidates:
        if isinstance(_shared, cls) and (model is None or _shared.model == model):
            if _shared.available:
                return _shared
        probe = cls(model=model) if model else cls()
        if probe.available:
            _shared = probe
            return _shared

    # 아무 키도 없다. 첫 후보를 돌려주고, available=False라 호출부가 폴백한다.
    if _shared is None or not isinstance(_shared, candidates[0]):
        _shared = candidates[0](model=model) if model else candidates[0]()
    return _shared


def is_available() -> bool:
    return get_shared_classifier().available


def active_provider_label() -> str:
    """리포트/대시보드에 '실제로 무엇이 돌았는지' 표시할 문자열."""
    clf = get_shared_classifier()
    return clf.label if clf.available else "키워드 규칙 (LLM 키 없음)"


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
            "LLM API 키가 없습니다. 다음 중 하나를 설정하세요:\n"
            "  Claude → ANTHROPIC_API_KEY 환경변수 또는 `ant auth login`\n"
            "  Gemini → GEMINI_API_KEY 환경변수 (+ pip install google-genai)\n"
            "  Ollama → 로컬 서버 실행 + `ollama pull llama3.1` 후 "
            f"{PROVIDER_ENV}=ollama (auto 모드에는 자동 포함 안 됨)\n"
            f"백엔드를 고정하려면 {PROVIDER_ENV}=anthropic|gemini|ollama"
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
