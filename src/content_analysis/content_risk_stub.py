"""
콘텐츠 위험도(content_risk) 임시 스텁
담당: 강동연 파트(STT->LLM 8대 사회공학 기법 분류)가 아직 없을 때,
통합 스코어링 로직(Fraud Risk Score)을 혼자 테스트해보기 위한 임시 대역.

강동연 파트가 실제 콘텐츠 위험도 계산 함수를 만들면 이 파일은 삭제하고
그 함수를 바로 fraud_risk_score.compute_fraud_risk_score()에 연결하면 됨.

기획서 DOCX 상 공식 (참고용, 실제 구현은 강동연 담당):
  content_risk = 0.5 * 최고위험카테고리점수 + 0.5 * 상위3개카테고리평균
"""

import hashlib


def get_content_risk_dummy(transcript_text: str = "") -> float:
    """[임시 대역] 대화 텍스트를 받아 0~100 콘텐츠 위험도를 반환하는 더미 함수."""
    digest = hashlib.md5(f"content::{transcript_text}".encode("utf-8")).hexdigest()
    ratio = int(digest[:8], 16) / 0xFFFFFFFF
    return round(10.0 + ratio * 80.0, 2)
