"""
콘텐츠 위험도 임시 스텁 (구버전 - 하위 호환용으로만 남겨둠)

⚠ 새 코드에서는 이 파일 대신 `content_risk.py`를 쓸 것.

여기 있던 해시 기반 난수 더미는 통합 스코어링 로직을 혼자 테스트하려고 급히 만든
것이었다. 지금은 기획서의 8대 사회공학 기법 구조와 집계 공식
(0.5×최고카테고리 + 0.5×상위3개평균)이 `content_risk.py`에 제대로 구현돼 있고,
LLM 연동 전 대역도 키워드 규칙 기반이라 **왜 그 점수가 나왔는지 근거가 남는다.**

난수 더미는 근거가 없어서 대시보드 검증에 쓸 수 없으므로 새 코드에서는 쓰지 말 것.
기존 스크립트가 아직 import하고 있어 함수만 유지한다.
"""

import hashlib


def get_content_risk_dummy(transcript_text: str = "") -> float:
    """
    [구버전 더미] 대화 텍스트 해시로 0~100 값을 만든다.

    Deprecated: content_risk.classify_by_keywords() 를 쓸 것.
    """
    digest = hashlib.md5(f"content::{transcript_text}".encode("utf-8")).hexdigest()
    ratio = int(digest[:8], 16) / 0xFFFFFFFF
    return round(10.0 + ratio * 80.0, 2)
